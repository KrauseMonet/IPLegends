"""[A231] The server half of the resend on a fresh connection.

The page sends a stuck room request again through the project's `vercel.app` address,
which makes it cross-origin: the API must say fineleg.app may make it (and nobody else),
Vercel must answer `/api/` on that address while sending every page visit to fineleg.app,
and the failure reports must count a resend apart from a failure. The page side is
tests/test_hedge_js.py.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from game.site import SITE_ORIGIN
from web.app import ClientFailureIn, app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECOND_HOST = "iplegends.vercel.app"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def preflight(client, origin):
    return client.options("/api/rooms/AB12CD/auction/bid", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})


def test_the_site_may_send_a_room_move_from_the_second_hostname(client):
    r = preflight(client, SITE_ORIGIN)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == SITE_ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]
    assert int(r.headers["access-control-max-age"]) == 7200        # once a session, not per move
    assert "access-control-allow-credentials" not in r.headers     # never as a signed-in player


@pytest.mark.parametrize("origin", ["https://evil.example", "https://fineleg.app.evil.example",
                                    "https://notfineleg.app", "http://fineleg.app"])
def test_no_other_site_may(client, origin):
    r = preflight(client, origin)
    assert r.headers.get("access-control-allow-origin") is None


def test_a_poll_from_the_site_carries_the_permission(client):
    r = client.get("/api/health", headers={"Origin": SITE_ORIGIN})
    assert r.headers.get("access-control-allow-origin") == SITE_ORIGIN


def test_the_second_hostname_answers_the_api_and_sends_pages_to_the_site():
    routes = json.loads((ROOT / "vercel.json").read_text())["routes"]
    gated = [r for r in routes if any(h.get("type") == "host" for h in r.get("has", []))]
    assert routes[:2] == gated, "the host rules must come before every other route"
    api, pages = gated
    assert all(h["value"] == SECOND_HOST for r in gated for h in r["has"])
    assert re.fullmatch(api["src"], "/api/rooms/AB12CD") and api["dest"] == "api/index.py"
    assert re.fullmatch(pages["src"], "/rooms/AB12CD") and pages["status"] == 308
    assert pages["headers"]["Location"] == SITE_ORIGIN + "/$1"


def test_the_page_and_the_routes_name_the_same_second_hostname():
    common = (ROOT / "web" / "static" / "common.js").read_text()
    mapping = re.search(r"const HEDGE_SECOND = \{([^}]*)\}", common).group(1)
    assert f"'{SITE_ORIGIN.split('//')[1]}': 'https://{SECOND_HOST}'" in mapping


def test_a_resend_is_accepted_in_a_failure_report_with_which_copy_answered():
    e = ClientFailureIn(t=1.0, kind="hedge", method="GET", path="/api/rooms/AB12CD", ms=2100,
                        won="second")
    assert (e.kind, e.won) == ("hedge", "second")
    with pytest.raises(ValueError):
        ClientFailureIn(t=1.0, kind="hedge", method="GET", path="/x", ms=1, won="third")


# --- the summary, against a real Postgres (opt-in, like tests/test_client_failures.py) -----

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")


@pytest.mark.skipif(not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway database")
def test_resends_are_counted_apart_from_failures():
    import psycopg

    from web import client_failures
    conn = psycopg.connect(SCRATCH)
    try:
        conn.execute("delete from client_failure_reports")
        base = {"t": 1.0, "method": "GET", "path": "/api/rooms/AB12CD", "status": None,
                "online": True, "net": None, "visible": True}
        client_failures.record(conn, "/rooms/AB12CD", 98, [
            dict(base, kind="timeout", ms=8000),
            dict(base, kind="hedge", ms=2100, won="second"),
            dict(base, kind="hedge", ms=2050, won="second"),
            dict(base, kind="hedge", ms=2300, won="first")])
        s = client_failures.summary(conn, 24)
        assert (s["failures"], s["hedges"], s["ok"]) == (1, 3, 98)
        assert s["rate"] == pytest.approx(1 / 99)
        assert [k for k, *_ in s["by_kind"]] == ["timeout"]
        assert dict((k, n) for k, n, _ in s["hedge_won"]) == {"second": 2, "first": 1}
    finally:
        conn.rollback()
        conn.close()
