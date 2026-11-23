"""Premium passes over HTTP [A305]: the offer, ordering, submitting a UTR, and the admin's
approvals. What a pass is and every rule about it lives in `web/premium.py`; this module
only maps it onto HTTP.

Every admin route answers 404, not 403, to anybody who is not an admin (A154), and every
response that carries an account's orders is `no-store`.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from web import admin as admin_lib
from web import auth, db
from web import premium as premium_lib

router = APIRouter()


def _today() -> datetime.date:
    """UTC, the same day boundary as the daily challenge (web/app.py `_today`)."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def _account_id(request: Request) -> int | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    return auth.verify_session_cookie(token) if token is not None else None


def _signed_in(request: Request) -> int:
    account_id = _account_id(request)
    if account_id is None:
        raise HTTPException(status_code=401, detail="sign in to buy a pass")
    return account_id


def _admin(request: Request, response: Response) -> int:
    account_id = _account_id(request)
    if not admin_lib.is_admin(account_id):
        raise HTTPException(status_code=404, detail="not found")
    response.headers["Cache-Control"] = "no-store"
    return account_id


def _refused(fn, *args):
    try:
        return fn(*args)
    except premium_lib.PremiumError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- shapes ------------------------------------------------------------------------------

class PassOfferOut(BaseModel):
    kind: str
    name: str
    rupees: int
    ends_on: str | None = Field(description="last valid day; null for a pass that never ends")
    limit: int | None
    left: int | None = Field(description="how many remain, for a limited pass")
    blurb: str
    unavailable: str | None = Field(description="why it cannot be bought today, or null")


class OrderOut(BaseModel):
    pass_id: int
    kind: str
    name: str
    rupees: int
    reference: str
    note: str = Field(description="what to type in the UPI payment note")
    upi_link: str
    utr: str | None
    status: str
    created_at: str
    starts_on: str | None
    ends_on: str | None
    note_from_admin: str | None


class ActiveOut(BaseModel):
    kind: str
    name: str
    ends_on: str | None


class PremiumOut(BaseModel):
    passes: list[PassOfferOut]
    upi_id: str
    payee: str
    signed_in: bool
    active: ActiveOut | None
    orders: list[OrderOut]


def _iso(d) -> str | None:
    return d.isoformat() if d is not None else None


def order_out(row: premium_lib.PassRow) -> OrderOut:
    p = premium_lib.PASSES[row.kind]
    return OrderOut(
        pass_id=row.pass_id, kind=row.kind, name=p.name, rupees=row.amount_rupees,
        reference=row.reference, note=premium_lib.note_text(row.reference),
        upi_link=premium_lib.upi_link(row.kind, row.reference), utr=row.utr,
        status=row.status, created_at=row.created_at.isoformat(),
        starts_on=_iso(row.starts_on), ends_on=_iso(row.ends_on), note_from_admin=row.note)


def active_out(row: premium_lib.PassRow | None) -> ActiveOut | None:
    if row is None:
        return None
    return ActiveOut(kind=row.kind, name=premium_lib.PASSES[row.kind].name,
                     ends_on=_iso(row.ends_on))


# --- the player's side -------------------------------------------------------------------

@router.get("/api/premium", response_model=PremiumOut)
def premium_offer(request: Request, response: Response) -> PremiumOut:
    account_id = _account_id(request)
    today = _today()
    response.headers["Cache-Control"] = "no-store"
    with db.connection() as conn:
        offers = []
        for p in premium_lib.PASSES.values():
            n = premium_lib.sold(conn, p.kind) if p.limit is not None else 0
            offers.append(PassOfferOut(
                kind=p.kind, name=p.name, rupees=p.rupees, ends_on=_iso(p.ends_on),
                limit=p.limit, left=(max(0, p.limit - n) if p.limit is not None else None),
                blurb=p.blurb, unavailable=premium_lib.available(p, today, n)))
        active = premium_lib.active_pass(conn, account_id, today) if account_id else None
        orders = premium_lib.passes_of(conn, account_id) if account_id else []
    return PremiumOut(passes=offers, upi_id=premium_lib.UPI_ID, payee=premium_lib.UPI_PAYEE,
                      signed_in=account_id is not None, active=active_out(active),
                      orders=[order_out(r) for r in orders])


class OrderIn(BaseModel):
    kind: str


@router.post("/api/premium/order", response_model=OrderOut)
def premium_order(body: OrderIn, request: Request, response: Response) -> OrderOut:
    account_id = _signed_in(request)
    response.headers["Cache-Control"] = "no-store"
    with db.connection() as conn:
        row = _refused(premium_lib.order, conn, account_id, body.kind, _today())
    return order_out(row)


class ClaimIn(BaseModel):
    pass_id: int
    utr: str = Field(max_length=40)


@router.post("/api/premium/claim", response_model=OrderOut)
def premium_claim(body: ClaimIn, request: Request, response: Response) -> OrderOut:
    account_id = _signed_in(request)
    response.headers["Cache-Control"] = "no-store"
    with db.connection() as conn:
        row = _refused(premium_lib.claim, conn, account_id, body.pass_id, body.utr)
    return order_out(row)


# --- the admin's side --------------------------------------------------------------------

class AdminClaimOut(BaseModel):
    order: OrderOut
    account_id: int
    username: str
    email: str
    claimed_at: str | None


class NoteIn(BaseModel):
    """Optional, so a stranger's empty request reaches the admin check and gets its 404
    rather than a 422 that says the route exists."""
    note: str = Field(default="", max_length=200)


@router.get("/api/admin/payments")
def admin_payments(request: Request, response: Response, status: str = "pending") -> dict:
    _admin(request, response)
    with db.connection() as conn:
        rows = _refused(premium_lib.claims, conn, status)
        sold = {k: premium_lib.sold(conn, k) for k in premium_lib.PASSES}
    return {"status": status, "sold": sold,
            "claims": [AdminClaimOut(order=order_out(c.row), account_id=c.row.account_id,
                                     username=c.username, email=c.email,
                                     claimed_at=_iso(c.row.claimed_at)).model_dump()
                       for c in rows]}


@router.post("/api/admin/payments/{pass_id}/approve", response_model=OrderOut)
def admin_approve(pass_id: int, request: Request, response: Response) -> OrderOut:
    actor = _admin(request, response)
    with db.connection() as conn:
        return order_out(_refused(premium_lib.approve, conn, actor, pass_id, _today()))


@router.post("/api/admin/payments/{pass_id}/reject", response_model=OrderOut)
def admin_reject(pass_id: int, request: Request, response: Response,
                 body: NoteIn | None = None) -> OrderOut:
    actor = _admin(request, response)
    with db.connection() as conn:
        return order_out(_refused(premium_lib.reject, conn, actor, pass_id,
                                  body.note if body else ""))


@router.post("/api/admin/payments/{pass_id}/revoke", response_model=OrderOut)
def admin_revoke(pass_id: int, request: Request, response: Response,
                 body: NoteIn | None = None) -> OrderOut:
    actor = _admin(request, response)
    with db.connection() as conn:
        return order_out(_refused(premium_lib.revoke, conn, actor, pass_id,
                                  body.note if body else ""))
