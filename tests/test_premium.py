"""Premium passes [A305]: ordering, claiming with a UTR, admin approval, and the one check
every premium feature asks (`has_premium`).

Two layers, as tests/test_admin.py: rules that need no database always run; the SQL runs
against a REAL Postgres when IPLEGENDS_SCRATCH_DB points at a throwaway, fully migrated
database (each test rolls back). A fake connection would never read the queries, and the
whole point of a pass is the rows.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import os
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from web import accounts, admin, auth
from web import premium as P
from web import premium_routes as R

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = (ROOT / "migrations" / "058_premium_passes.sql").read_text()
TODAY = datetime.date(2026, 12, 1)


# --- the schema and the code agree [CLAUDE.md: reconcile before applying] ---------------

def _check_values(name: str) -> set[str]:
    m = re.search(rf"constraint {name}\s+check\s*\(\s*\w+\s+in\s*\(([^)]*)\)", MIGRATION, re.S)
    assert m, name
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_every_pass_kind_is_one_the_table_allows():
    assert set(P.PASSES) == _check_values("premium_passes_kind_ck")


def test_every_status_the_code_writes_is_one_the_table_allows():
    src = (ROOT / "web" / "premium.py").read_text()
    written = set(re.findall(r"status = '(\w+)'", src))
    assert {"approved", "rejected", "revoked"} <= written, "the scan has drifted"
    assert written | {"pending"} <= _check_values("premium_passes_status_ck")


def test_a_new_reference_fits_the_tables_pattern_and_avoids_lookalikes():
    pattern = re.search(r"reference ~ '(\^[^']+\$)'", MIGRATION).group(1)
    for _ in range(300):
        ref = P._new_reference()
        assert re.fullmatch(pattern, ref)
        assert not set(ref) & set("01IO")


def test_the_upi_id_is_the_one_the_tip_modal_uses():
    common = (ROOT / "web" / "static" / "common.js").read_text()
    assert re.search(r"^const UPI_ID = '([^']*)';", common, re.M).group(1) == P.UPI_ID


# --- pure rules ---------------------------------------------------------------------------

def test_the_upi_link_carries_the_price_and_the_reference():
    link = P.upi_link("season", "ABC234")
    assert link.startswith("upi://pay?pa=mishrakoustav01-1%40okaxis&")
    assert "&am=249.00&" in link and "&cu=INR&" in link
    assert link.endswith("&tn=FLXI%20ABC234")
    assert "&am=999.00&" in P.upi_link("founder", "ABC234")


@pytest.mark.parametrize("raw,clean", [("123456789012", "123456789012"),
                                       (" 1234 5678 9012 ", "123456789012")])
def test_a_utr_is_twelve_digits_spaces_allowed(raw, clean):
    assert P.clean_utr(raw) == clean


@pytest.mark.parametrize("bad", ["12345678901", "1234567890123", "12345678901a", "", None])
def test_anything_else_is_not_a_utr(bad):
    with pytest.raises(P.PremiumError):
        P.clean_utr(bad)


def test_a_season_pass_cannot_be_bought_after_it_ends_and_a_limited_one_sells_out():
    season, founder = P.PASSES["season"], P.PASSES["founder"]
    assert P.available(season, season.ends_on, 0) is None
    assert "ended" in P.available(season, season.ends_on + datetime.timedelta(days=1), 0)
    assert P.available(founder, TODAY, 99) is None
    assert "sold out" in P.available(founder, TODAY, 100)


def test_the_prices_and_dates_are_the_ones_decided():
    # Stated, not read from the code: a change here is a decision, not a refactor.
    assert (P.PASSES["season"].rupees, P.PASSES["season"].ends_on) == (249, datetime.date(2027, 6, 30))
    assert (P.PASSES["founder"].rupees, P.PASSES["founder"].ends_on,
            P.PASSES["founder"].limit) == (999, None, 100)


# --- the routes' gates (no database reached) ---------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-premium-secret")
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "7")
    from web.app import app
    with TestClient(app) as c:
        yield c


def test_buying_and_claiming_need_an_account(client):
    assert client.post("/api/premium/order", json={"kind": "season"}).status_code == 401
    assert client.post("/api/premium/claim",
                       json={"pass_id": 1, "utr": "123456789012"}).status_code == 401


def test_the_payments_console_is_404_to_anybody_but_an_admin(client):
    for path, method in [("/api/admin/payments", "get"),
                         ("/api/admin/payments/1/approve", "post"),
                         ("/api/admin/payments/1/reject", "post"),
                         ("/api/admin/payments/1/revoke", "post")]:
        assert getattr(client, method)(path).status_code == 404
        client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(8))
        assert getattr(client, method)(path).status_code == 404
        client.cookies.clear()


def test_the_page_is_served_and_wired():
    vercel = (ROOT / "vercel.json").read_text()
    assert '"src": "/premium", "dest": "/web/static/premium.html"' in vercel
    page = (ROOT / "web" / "static" / "premium.html").read_text()
    assert re.search(r'src="/static/premium\.js\?v=\w+"', page)
    from web.app import app
    assert "/premium" in {r.path for r in app.routes if hasattr(r, "path")}
    assert "premium" in __import__("web.app", fromlist=["MeOut"]).MeOut.model_fields


def test_the_tip_modal_no_longer_promises_nothing_will_ever_be_paid_for():
    for f in ("common.js", "season.js", "terms.html"):
        text = (ROOT / "web" / "static" / f).read_text()
        assert "nothing\n            ever will be" not in text and "stays free —" not in text
        assert "free without it and stays that way" not in text


# --- the SQL, against a real Postgres (opt-in) -------------------------------------------

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _account(pg, name):
    return accounts.create_account(pg, name, f"{name}@example.com", "password1").account_id


def _paid(pg, account, kind="season", utr="111122223333"):
    row = P.order(pg, account, kind, TODAY)
    return P.claim(pg, account, row.pass_id, utr)


@needs_pg
def test_an_order_is_pending_with_its_price_and_pressing_buy_twice_gives_one(pg):
    a = _account(pg, "buyer_one")
    first = P.order(pg, a, "season", TODAY)
    assert (first.status, first.amount_rupees, first.utr) == ("pending", 249, None)
    assert P.order(pg, a, "season", TODAY).pass_id == first.pass_id
    assert P.order(pg, a, "founder", TODAY).pass_id != first.pass_id
    assert not P.has_premium(pg, a, TODAY)


@needs_pg
def test_nobody_holds_a_pass_until_an_admin_approves_a_paid_claim(pg):
    a, boss = _account(pg, "buyer_two"), _account(pg, "the_admin")
    row = P.order(pg, a, "season", TODAY)
    with pytest.raises(P.PremiumError, match="nobody has submitted"):
        P.approve(pg, boss, row.pass_id, TODAY)
    row = P.claim(pg, a, row.pass_id, "1111 2222 3333")
    assert row.utr == "111122223333" and not P.has_premium(pg, a, TODAY)
    done = P.approve(pg, boss, row.pass_id, TODAY)
    assert (done.status, done.starts_on, done.ends_on) == ("approved", TODAY, datetime.date(2027, 6, 30))
    assert P.has_premium(pg, a, TODAY)
    assert P.has_premium(pg, a, datetime.date(2027, 6, 30))
    assert not P.has_premium(pg, a, datetime.date(2027, 7, 1))
    with pytest.raises(P.PremiumError, match="already approved"):
        P.approve(pg, boss, row.pass_id, TODAY)


@needs_pg
def test_a_founder_pass_never_ends_and_wins_over_a_season_pass(pg):
    a, boss = _account(pg, "buyer_three"), _account(pg, "admin_three")
    P.approve(pg, boss, _paid(pg, a, "season", "100000000001").pass_id, TODAY)
    f = P.approve(pg, boss, _paid(pg, a, "founder", "100000000002").pass_id, TODAY)
    assert f.ends_on is None
    assert P.active_pass(pg, a, datetime.date(2040, 1, 1)).kind == "founder"
    assert P.active_pass(pg, a, TODAY).kind == "founder"


@needs_pg
def test_one_payment_backs_one_pass_until_its_claim_is_rejected(pg):
    a, b, boss = _account(pg, "payer_a"), _account(pg, "copier_b"), _account(pg, "admin_c")
    stolen = _paid(pg, b, utr="999988887777")      # b types a's UTR first
    mine = P.order(pg, a, "season", TODAY)
    with pytest.raises(P.PremiumError, match="already on another order"):
        P.claim(pg, a, mine.pass_id, "999988887777")
    P.reject(pg, boss, stolen.pass_id, "no such payment with your reference")
    assert P.claim(pg, a, mine.pass_id, "999988887777").utr == "999988887777"


@needs_pg
def test_you_can_only_claim_your_own_pending_order(pg):
    a, b, boss = _account(pg, "owner_a"), _account(pg, "other_b"), _account(pg, "admin_d")
    row = P.order(pg, a, "season", TODAY)
    with pytest.raises(P.PremiumError, match="no such order"):
        P.claim(pg, b, row.pass_id, "222233334444")
    P.claim(pg, a, row.pass_id, "222233334444")
    assert P.claim(pg, a, row.pass_id, "222233334445").utr == "222233334445"   # a typo, fixed
    P.approve(pg, boss, row.pass_id, TODAY)
    with pytest.raises(P.PremiumError, match="already approved"):
        P.claim(pg, a, row.pass_id, "222233334446")


@needs_pg
def test_a_pass_that_already_covers_you_cannot_be_bought_again(pg):
    a, boss = _account(pg, "covered_a"), _account(pg, "admin_e")
    P.approve(pg, boss, _paid(pg, a, "season", "300000000001").pass_id, TODAY)
    with pytest.raises(P.PremiumError, match="already have a pass"):
        P.order(pg, a, "season", TODAY)
    P.approve(pg, boss, _paid(pg, a, "founder", "300000000002").pass_id, TODAY)   # an upgrade is fine
    with pytest.raises(P.PremiumError, match="already have a pass"):
        P.order(pg, a, "founder", TODAY)


@needs_pg
def test_open_orders_are_capped(pg, monkeypatch):
    a = _account(pg, "hoarder")
    monkeypatch.setitem(P.PASSES, "founder", dataclasses.replace(P.PASSES["founder"], limit=None))
    P.claim(pg, a, P.order(pg, a, "season", TODAY).pass_id, "400000000001")
    P.claim(pg, a, P.order(pg, a, "founder", TODAY).pass_id, "400000000002")
    P.order(pg, a, "season", TODAY)                   # a third open order
    with pytest.raises(P.PremiumError, match="orders waiting"):
        P.order(pg, a, "founder", TODAY)


@needs_pg
def test_a_limited_pass_sells_out_at_its_limit(pg, monkeypatch):
    sold_before = P.sold(pg, "founder")
    monkeypatch.setitem(P.PASSES, "founder",
                        dataclasses.replace(P.PASSES["founder"], limit=sold_before + 1))
    a, b, boss = _account(pg, "first_in"), _account(pg, "too_late"), _account(pg, "admin_f")
    late = P.order(pg, b, "founder", TODAY)           # ordered while one was left
    _paid(pg, a, "founder", "500000000001")           # the last one, claimed
    with pytest.raises(P.PremiumError, match="sold out"):
        P.order(pg, _account(pg, "later_still"), "founder", TODAY)
    with pytest.raises(P.PremiumError, match="sold out"):
        P.claim(pg, b, late.pass_id, "500000000002")


@needs_pg
def test_approval_stops_at_the_limit_even_with_claims_waiting(pg, monkeypatch):
    a, b, boss = _account(pg, "early_a"), _account(pg, "early_b"), _account(pg, "admin_g")
    one, two = _paid(pg, a, "founder", "600000000001"), _paid(pg, b, "founder", "600000000002")
    (approved,), = pg.execute("select count(*) from premium_passes "
                              "where kind = 'founder' and status = 'approved'")
    monkeypatch.setitem(P.PASSES, "founder",
                        dataclasses.replace(P.PASSES["founder"], limit=approved + 1))
    P.approve(pg, boss, one.pass_id, TODAY)
    with pytest.raises(P.PremiumError, match="approved already"):
        P.approve(pg, boss, two.pass_id, TODAY)


@needs_pg
def test_a_season_pass_cannot_be_approved_after_its_season_ends(pg):
    a, boss = _account(pg, "too_slow"), _account(pg, "admin_h")
    row = _paid(pg, a, "season", "700000000001")
    with pytest.raises(P.PremiumError, match="ended"):
        P.approve(pg, boss, row.pass_id, datetime.date(2027, 7, 1))


@needs_pg
def test_revoking_ends_the_pass(pg):
    a, boss = _account(pg, "refunded"), _account(pg, "admin_i")
    row = P.approve(pg, boss, _paid(pg, a, "season", "800000000001").pass_id, TODAY)
    P.revoke(pg, boss, row.pass_id, "refunded on request")
    assert not P.has_premium(pg, a, TODAY)
    with pytest.raises(P.PremiumError):
        P.revoke(pg, boss, row.pass_id)


@needs_pg
def test_the_console_lists_paid_claims_only_and_the_overview_counts_them(pg):
    before = admin.overview(pg, TODAY)["payments_waiting"]
    a = _account(pg, "listed_one")
    P.order(pg, a, "founder", TODAY)                  # never paid: not a claim
    paid = _paid(pg, a, "season", "900000000001")
    rows = P.claims(pg, "pending")
    mine = [c for c in rows if c.row.account_id == a]
    assert [c.row.pass_id for c in mine] == [paid.pass_id]
    assert mine[0].username == "listed_one" and mine[0].email == "listed_one@example.com"
    assert admin.overview(pg, TODAY)["payments_waiting"] == before + 1


@needs_pg
def test_deleting_an_account_deletes_its_passes(pg):
    a = _account(pg, "leaving")
    _paid(pg, a, "season", "910000000001")
    pg.execute("delete from accounts where account_id = %s", (a,))
    (n,), = pg.execute("select count(*) from premium_passes where account_id = %s", (a,))
    assert n == 0


@needs_pg
def test_the_api_end_to_end_and_auth_me_shows_the_pass(pg, client, monkeypatch):
    @contextlib.contextmanager
    def same_conn():
        yield pg
    monkeypatch.setattr(R.db, "connection", same_conn)
    import web.app as appmod
    monkeypatch.setattr(appmod, "_db", same_conn)
    monkeypatch.setattr(R, "_today", lambda: TODAY)
    monkeypatch.setattr(appmod, "_today", lambda: TODAY)
    buyer = _account(pg, "api_buyer")
    boss = _account(pg, "api_admin")
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, str(boss))

    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(buyer))
    o = client.post("/api/premium/order", json={"kind": "season"}).json()
    assert o["rupees"] == 249 and o["note"] == f"FLXI {o['reference']}"
    assert client.post("/api/premium/claim",
                       json={"pass_id": o["pass_id"], "utr": "12345"}).status_code == 400
    assert client.post("/api/premium/claim",
                       json={"pass_id": o["pass_id"], "utr": "920000000001"}).status_code == 200
    assert client.get("/api/auth/me").json()["premium"] is None
    assert client.post(f"/api/admin/payments/{o['pass_id']}/approve").status_code == 404

    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(boss))
    listed = client.get("/api/admin/payments").json()["claims"]
    assert o["pass_id"] in [c["order"]["pass_id"] for c in listed]
    assert client.post(f"/api/admin/payments/{o['pass_id']}/approve").json()["status"] == "approved"

    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(buyer))
    me = client.get("/api/auth/me").json()
    assert me["premium"] == {"kind": "season", "name": "Season Pass", "ends_on": "2027-06-30"}
    offer = client.get("/api/premium").json()
    assert offer["active"]["kind"] == "season"
    assert [x["status"] for x in offer["orders"]] == ["approved"]
