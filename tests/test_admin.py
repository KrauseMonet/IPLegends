"""The admin console [A154]: who is an admin, what an action may touch, and whether the
console's SQL actually runs.

Two layers. The privilege rule and the guards need no database and always run. The
queries are checked against a REAL Postgres, because a fake connection keyed on SQL text
never reads the query and cannot tell a correct one from a broken one (A108/A124 reached
the same conclusion). Those tests are opt-in: point IPLEGENDS_SCRATCH_DB at a THROWAWAY
database with every migration applied. Each test runs in a transaction that is rolled
back, but never aim this at a database whose rows matter.
"""

from __future__ import annotations

import datetime
import os

import pytest

from web import accounts, admin, auth


@pytest.fixture(autouse=True)
def _no_admin_env(monkeypatch):
    monkeypatch.delenv(admin.ADMIN_ACCOUNT_IDS, raising=False)
    monkeypatch.delenv(admin.FALLBACK_ADMIN_VAR, raising=False)


# --- who is an admin ----------------------------------------------------------------------

def test_nobody_is_an_admin_when_nothing_is_configured():
    assert admin.admin_ids() == frozenset()
    assert not admin.is_admin(1)
    assert not admin.is_admin(None)


def test_admin_ids_is_a_list_and_compares_whole_ids(monkeypatch):
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, " 17, 4 ,x, ")
    assert admin.is_admin(17) and admin.is_admin(4)
    # "7" is inside "17" and "1" inside both: neither is an admin.
    assert not admin.is_admin(7) and not admin.is_admin(1)


def test_the_daily_reset_account_stands_in_when_no_admin_list_is_set(monkeypatch):
    monkeypatch.setenv(admin.FALLBACK_ADMIN_VAR, "9")
    assert admin.is_admin(9)
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "   ")   # blank counts as unset
    assert admin.is_admin(9)


def test_an_explicit_admin_list_replaces_the_fallback(monkeypatch):
    monkeypatch.setenv(admin.FALLBACK_ADMIN_VAR, "9")
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "3")
    assert admin.is_admin(3)
    assert not admin.is_admin(9)


def test_an_admin_may_also_reset_their_own_daily(monkeypatch):
    from web import app
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "5")
    assert app._may_reset_daily(5)
    assert not app._may_reset_daily(6)


# --- guards, which must refuse before any SQL runs ------------------------------------------

class _NoSql:
    def execute(self, *a, **k):
        raise AssertionError("a refused action must not reach the database")


def test_an_admin_cannot_delete_their_own_account(monkeypatch):
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "5")
    with pytest.raises(admin.AdminError, match="your own"):
        admin.delete_account(_NoSql(), 5, 5)


@pytest.mark.parametrize("action", [
    lambda c: admin.delete_account(c, 5, 6),
    lambda c: admin.set_password(c, 5, 6, "longenough"),
    lambda c: admin.rename_account(c, 5, 6, "someone"),
    lambda c: admin.clear_kit(c, 5, 6),
    lambda c: admin.remove_daily_result(c, 5, datetime.date(2026, 9, 1), 6),
])
def test_no_action_touches_another_admins_account(monkeypatch, action):
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "5,6")
    with pytest.raises(admin.AdminError, match="admin"):
        action(_NoSql())


def test_a_rename_is_held_to_the_registration_rules(monkeypatch):
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "5")
    for bad in ("ab", "has space", "x" * 25, "<b>"):
        with pytest.raises(admin.AdminError):
            admin.rename_account(_NoSql(), 5, 7, bad)
    with pytest.raises(admin.AdminError, match="8 characters"):
        admin.set_password(_NoSql(), 5, 7, "short")


# --- the SQL, against a real Postgres (opt-in) ---------------------------------------------

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


def _account(pg, name, days_ago=0):
    a = accounts.create_account(pg, name, f"{name}@example.com", "password1")
    pg.execute("update accounts set created_at = now() - make_interval(days => %s) "
               "where account_id = %s", (days_ago, a.account_id))
    return a.account_id


@needs_pg
def test_overview_counts_and_fills_quiet_days_with_zeroes(pg):
    before = admin.overview(pg, datetime.date(2026, 9, 1))
    _account(pg, "fresh_one")
    _account(pg, "week_old", days_ago=3)
    _account(pg, "month_old", days_ago=40)
    o = admin.overview(pg, datetime.date(2026, 9, 1))
    assert o["accounts"] - before["accounts"] == 3
    assert o["accounts_1d"] - before["accounts_1d"] == 1
    assert o["accounts_7d"] - before["accounts_7d"] == 2
    days = o["signups_by_day"]
    assert len(days) == admin.TREND_DAYS
    assert days[-1]["date"] == datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    assert sum(d["count"] for d in days) - sum(d["count"] for d in before["signups_by_day"]) == 2


@needs_pg
def test_account_search_escapes_wildcards_and_matches_ids(pg):
    a = _account(pg, "under_score")
    b = _account(pg, "underXscore")
    _, rows = admin.list_accounts(pg, "under_score")
    ids = {r["account_id"] for r in rows}
    assert a in ids and b not in ids            # "_" searched for itself, not any char
    total, rows = admin.list_accounts(pg, str(b))
    assert b in {r["account_id"] for r in rows}
    total, rows = admin.list_accounts(pg, "")
    assert rows[0]["account_id"] == b           # newest first


@needs_pg
def test_rename_password_kit_and_delete_really_change_the_row(pg, monkeypatch):
    actor = _account(pg, "the_admin")
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, str(actor))
    target = _account(pg, "target_user")
    other = _account(pg, "taken_name")

    with pytest.raises(admin.AdminError, match="taken"):
        admin.rename_account(pg, actor, target, "TAKEN_NAME")
    admin.rename_account(pg, actor, target, "renamed_user")
    assert accounts.get_account(pg, target).username == "renamed_user"

    admin.set_password(pg, actor, target, "brand-new-pass")
    assert accounts.authenticate(pg, "renamed_user", "brand-new-pass") is not None
    assert accounts.authenticate(pg, "renamed_user", "password1") is None

    pg.execute("update accounts set kit_name='X', kit_monogram='X', kit_colour='red' "
               "where account_id = %s", (target,))
    admin.clear_kit(pg, actor, target)
    assert accounts.get_account(pg, target).kit is None

    detail = admin.account_detail(pg, target)
    assert detail["username"] == "renamed_user" and detail["games_played"] == 0

    admin.delete_account(pg, actor, target)
    assert accounts.get_account(pg, target) is None
    assert accounts.get_account(pg, other) is not None
    with pytest.raises(admin.AdminError, match="no account"):
        admin.delete_account(pg, actor, target)


@needs_pg
def test_daily_board_removal_is_scoped_to_one_player_and_one_day(pg, monkeypatch):
    actor = _account(pg, "the_admin")
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, str(actor))
    p1, p2 = _account(pg, "player_one"), _account(pg, "player_two")
    day = datetime.date(2001, 1, 1)
    pg.execute(
        "insert into daily_challenges (challenge_date, seed, scenario_kind, scenario, "
        "deck_fs_ids) values (%s, 1, 'win_by_runs', %s, '[]')",
        (day, '{"opposition_fs_id": 1, "opposition_name": "Test XI", "stage": "Final", '
              '"runs_required": 10, "rules": 3}'))
    for pid, margin in ((p1, 12), (p2, 30)):
        pg.execute("insert into daily_results (challenge_date, account_id, state, "
                   "objective_met, margin) values (%s, %s, 's', true, %s)", (day, pid, margin))
    view = admin.daily_view(pg, day)
    assert "Test XI" in view["scenario"]
    assert [r["account_id"] for r in view["board"]] == [p2, p1]   # the board's own order

    admin.remove_daily_result(pg, actor, day, p2)
    assert [r["account_id"] for r in admin.daily_view(pg, day)["board"]] == [p1]
    assert pg.execute("select 1 from daily_challenges where challenge_date = %s",
                      (day,)).fetchone() is not None                # the day survives
    assert admin.daily_view(pg, datetime.date(2000, 1, 1))["scenario"] is None


@needs_pg
def test_rooms_list_and_close(pg, monkeypatch):
    actor = _account(pg, "the_admin")
    pg.execute("insert into rooms (code, format, timer_seconds, seed, host_id) "
               "values ('ZZADM1', 'final', 30, 1, 'h')")
    pg.execute("insert into room_players (room_code, player_id, seat_order, name, is_cpu) "
               "values ('ZZADM1', 'h', 0, '<b>Asha</b>', false), "
               "('ZZADM1', 'c', 1, 'CPU 1', true)")
    room = next(r for r in admin.list_rooms(pg) if r["code"] == "ZZADM1")
    assert room["players"] == ["<b>Asha</b>"] and room["cpu_seats"] == 1
    admin.delete_room(pg, actor, "zzadm1")
    assert not any(r["code"] == "ZZADM1" for r in admin.list_rooms(pg))
    assert pg.execute("select count(*) from room_players where room_code = 'ZZADM1'"
                      ).fetchone()[0] == 0


def test_the_session_secret_is_not_needed_to_hash(monkeypatch):
    # set_password hashes, and hashing must not require SESSION_SECRET (auth.py's rule).
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert auth.hash_password("longenough").startswith("pbkdf2_sha256$")
