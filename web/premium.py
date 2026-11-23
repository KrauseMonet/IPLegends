"""Premium passes, paid by UPI and approved by hand [A305].

Plain functions over a bare psycopg connection, the shape `web/accounts.py` and
`web/admin.py` use. `web/app.py` decides who may call them.

**A pass is a claim until an admin has seen the money.** Paying straight to a UPI ID tells
the site nothing, so the flow is: the player orders a pass and gets a six-character
reference to put in the UPI note; pays; submits the 12-digit UTR their app shows; and an
admin matches the UTR and reference against the payments received and approves or rejects.
`has_premium` is the ONE check every premium feature asks, on the server -- the page only
decides what to show.

**What a pass costs and how long it lasts lives here and nowhere else.** The page reads it
from `/api/premium` so the price on screen and the amount in the UPI link cannot disagree.
"""

from __future__ import annotations

import dataclasses
import datetime
import secrets
import sys
from urllib.parse import quote

import psycopg

# The address the tip modal already uses (web/static/common.js). A test holds the two equal.
UPI_ID = "mishrakoustav01-1@okaxis"
UPI_PAYEE = "Fine Leg XI"
NOTE_PREFIX = "FLXI"

REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, no 1/I
REFERENCE_LENGTH = 6
MAX_OPEN_ORDERS = 3          # pending orders an account may hold at once


@dataclasses.dataclass(frozen=True)
class Pass:
    kind: str
    name: str
    rupees: int
    ends_on: datetime.date | None     # last valid day; None = never ends
    limit: int | None = None          # how many may ever be sold; None = no limit
    blurb: str = ""


PASSES: dict[str, Pass] = {
    "season": Pass("season", "Season Pass", 249, datetime.date(2027, 6, 30),
                   blurb="Everything premium until 30 June 2027, through the IPL 2027 final."),
    "founder": Pass("founder", "Founder Pass", 999, None, limit=100,
                    blurb="Everything premium for as long as the site runs, and your name "
                          "on the credits page. The first 100 only."),
}


class PremiumError(ValueError):
    """Refused with a 400, never a 500."""


@dataclasses.dataclass(frozen=True)
class PassRow:
    pass_id: int
    account_id: int
    kind: str
    amount_rupees: int
    reference: str
    utr: str | None
    status: str
    created_at: datetime.datetime
    claimed_at: datetime.datetime | None
    starts_on: datetime.date | None
    ends_on: datetime.date | None
    note: str | None


_COLUMNS = ("pass_id, account_id, kind, amount_rupees, reference, utr, status, created_at, "
            "claimed_at, starts_on, ends_on, note")


def _row(r) -> PassRow:
    return PassRow(*r)


def upi_link(kind: str, reference: str) -> str:
    """Opens the payer's own UPI app with the amount and the note filled in."""
    p = PASSES[kind]
    return (f"upi://pay?pa={quote(UPI_ID)}&pn={quote(UPI_PAYEE)}"
            f"&am={p.rupees}.00&cu=INR&tn={quote(note_text(reference))}")


def note_text(reference: str) -> str:
    return f"{NOTE_PREFIX} {reference}"


def clean_utr(utr: str) -> str:
    """The 12-digit UPI reference, with the spaces apps print in it removed."""
    u = "".join((utr or "").split())
    if len(u) != 12 or not u.isdigit():
        raise PremiumError("a UPI transaction ID (UTR) is 12 digits")
    return u


def _new_reference() -> str:
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))


def _log(actor: int, action: str, detail: str) -> None:
    print(f"[premium] account {actor} {action}: {detail}", file=sys.stderr, flush=True)


# --- reading -----------------------------------------------------------------------------

def passes_of(conn, account_id: int) -> list[PassRow]:
    return [_row(r) for r in conn.execute(
        f"select {_COLUMNS} from premium_passes where account_id = %s "
        "order by created_at desc, pass_id desc", (account_id,))]


def active_pass(conn, account_id: int | None, today: datetime.date) -> PassRow | None:
    """The approved pass covering `today`, preferring one that never ends."""
    if account_id is None:
        return None
    r = conn.execute(
        f"select {_COLUMNS} from premium_passes "
        "where account_id = %s and status = 'approved' and starts_on <= %s "
        "  and (ends_on is null or ends_on >= %s) "
        "order by ends_on desc nulls first limit 1", (account_id, today, today)).fetchone()
    return _row(r) if r else None


def has_premium(conn, account_id: int | None, today: datetime.date) -> bool:
    """The one check every premium feature asks."""
    return active_pass(conn, account_id, today) is not None


def sold(conn, kind: str) -> int:
    """Passes of this kind sold or waiting on a paid claim -- what a limit counts."""
    (n,), = conn.execute(
        "select count(*) from premium_passes where kind = %s "
        "and (status = 'approved' or (status = 'pending' and utr is not null))", (kind,))
    return n


def available(p: Pass, today: datetime.date, sold_count: int) -> str | None:
    """Why this pass cannot be bought today, or None if it can."""
    if p.ends_on is not None and today > p.ends_on:
        return f"the {p.name} has ended"
    if p.limit is not None and sold_count >= p.limit:
        return f"the {p.name} has sold out"
    return None


# --- the player's side -------------------------------------------------------------------

def order(conn, account_id: int, kind: str, today: datetime.date) -> PassRow:
    """Start buying a pass. Reuses an unpaid order of the same kind rather than making a
    second, so pressing Buy twice gives one reference."""
    p = PASSES.get(kind)
    if p is None:
        raise PremiumError(f"unknown pass {kind!r}")
    why = available(p, today, sold(conn, kind))
    if why:
        raise PremiumError(why)
    held = active_pass(conn, account_id, today)
    if held is not None:
        held_ends = held.ends_on
        if held_ends is None or (p.ends_on is not None and held_ends >= p.ends_on):
            raise PremiumError("you already have a pass that lasts at least as long")
    mine = passes_of(conn, account_id)
    for row in mine:
        if row.kind == kind and row.status == "pending" and row.utr is None:
            return row
    if sum(1 for row in mine if row.status == "pending") >= MAX_OPEN_ORDERS:
        raise PremiumError("you have orders waiting already; finish or wait on those first")
    for _ in range(8):
        try:
            with conn.transaction():     # a savepoint, so a clash does not abort the request
                r = conn.execute(
                    f"insert into premium_passes (account_id, kind, amount_rupees, reference) "
                    f"values (%s, %s, %s, %s) returning {_COLUMNS}",
                    (account_id, kind, p.rupees, _new_reference())).fetchone()
            return _row(r)
        except psycopg.errors.UniqueViolation:
            continue
    raise PremiumError("could not make an order reference; try again")


def claim(conn, account_id: int, pass_id: int, utr: str) -> PassRow:
    """Record the UTR of a payment for one of this account's pending orders. May be
    corrected while the order is still waiting."""
    u = clean_utr(utr)
    r = conn.execute(f"select {_COLUMNS} from premium_passes where pass_id = %s for update",
                     (pass_id,)).fetchone()
    if r is None or r[1] != account_id:
        raise PremiumError("no such order")
    row = _row(r)
    if row.status != "pending":
        raise PremiumError(f"that order is already {row.status}")
    if row.utr == u:
        return row
    taken = conn.execute(
        "select 1 from premium_passes where utr = %s and status <> 'rejected' and pass_id <> %s",
        (u, pass_id)).fetchone()
    if taken:
        raise PremiumError("that UTR is already on another order")
    p = PASSES[row.kind]
    if row.utr is None and p.limit is not None and sold(conn, row.kind) >= p.limit:
        raise PremiumError(f"the {p.name} has sold out")
    r = conn.execute(
        f"update premium_passes set utr = %s, claimed_at = now() where pass_id = %s "
        f"returning {_COLUMNS}", (u, pass_id)).fetchone()
    return _row(r)


# --- the admin's side --------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ClaimRow:
    row: PassRow
    username: str
    email: str


def claims(conn, status: str = "pending", limit: int = 100) -> list[ClaimRow]:
    """Paid claims waiting on a decision (pending with a UTR), or the most recent decided
    ones. An order nobody has paid for is not a claim and is left out."""
    if status not in ("pending", "approved", "rejected", "revoked"):
        raise PremiumError(f"unknown status {status!r}")
    order_by = "p.claimed_at asc" if status == "pending" else "p.decided_at desc"
    rows = conn.execute(
        f"select {', '.join('p.' + c for c in _COLUMNS.split(', '))}, a.username, a.email "
        "from premium_passes p join accounts a using (account_id) "
        f"where p.status = %s and p.utr is not null order by {order_by} limit %s",
        (status, limit)).fetchall()
    return [ClaimRow(_row(r[:12]), r[12], r[13]) for r in rows]


def _locked(conn, pass_id: int) -> PassRow:
    r = conn.execute(f"select {_COLUMNS} from premium_passes where pass_id = %s for update",
                     (pass_id,)).fetchone()
    if r is None:
        raise PremiumError(f"no order {pass_id}")
    return _row(r)


def approve(conn, actor: int, pass_id: int, today: datetime.date) -> PassRow:
    row = _locked(conn, pass_id)
    if row.status != "pending":
        raise PremiumError(f"that order is already {row.status}")
    if row.utr is None:
        raise PremiumError("nobody has submitted a payment for that order")
    p = PASSES[row.kind]
    if p.ends_on is not None and today > p.ends_on:
        raise PremiumError(f"the {p.name} has ended; reject it and refund the payment")
    if p.limit is not None:
        (n,), = conn.execute("select count(*) from premium_passes where kind = %s "
                             "and status = 'approved'", (row.kind,))
        if n >= p.limit:
            raise PremiumError(f"all {p.limit} {p.name}es are approved already")
    r = conn.execute(
        f"update premium_passes set status = 'approved', starts_on = %s, ends_on = %s, "
        f"decided_at = now(), decided_by = %s where pass_id = %s returning {_COLUMNS}",
        (today, p.ends_on, actor, pass_id)).fetchone()
    _log(actor, "approved", f"pass {pass_id} ({row.kind}, UTR {row.utr}, ref {row.reference})")
    return _row(r)


def reject(conn, actor: int, pass_id: int, note: str = "") -> PassRow:
    """No payment matches. The UTR stops counting as used, so the real payer can still
    submit it if somebody else typed theirs first."""
    row = _locked(conn, pass_id)
    if row.status != "pending":
        raise PremiumError(f"that order is already {row.status}")
    r = conn.execute(
        f"update premium_passes set status = 'rejected', note = %s, decided_at = now(), "
        f"decided_by = %s where pass_id = %s returning {_COLUMNS}",
        ((note or "").strip()[:200] or None, actor, pass_id)).fetchone()
    _log(actor, "rejected", f"pass {pass_id} ({row.kind}, UTR {row.utr})")
    return _row(r)


def revoke(conn, actor: int, pass_id: int, note: str = "") -> PassRow:
    """End an approved pass, for a refund."""
    row = _locked(conn, pass_id)
    if row.status != "approved":
        raise PremiumError("only an approved pass can be revoked")
    r = conn.execute(
        f"update premium_passes set status = 'revoked', note = %s, decided_at = now(), "
        f"decided_by = %s where pass_id = %s returning {_COLUMNS}",
        ((note or "").strip()[:200] or None, actor, pass_id)).fetchone()
    _log(actor, "revoked", f"pass {pass_id} ({row.kind})")
    return _row(r)
