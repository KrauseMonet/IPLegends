"""Email+password accounts (`web/accounts.py`). No live Postgres -- like
`tests/test_rooms.py`, `FakeConn`/`FakeCursor` are a minimal in-memory stand-in for the
`accounts`/`game_results`/`game_result_players` tables (migrations 026/027), matched on
the handful of distinct SQL statements `web/accounts.py` issues. Tests call the REAL
`create_account`/`authenticate`/`get_account`/`save_game_result`/`profile_stats` -- the
load/save layer included, not just the validation logic above it.
"""

from __future__ import annotations

import pytest

from web import accounts


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeConn:
    """Dicts standing in for `accounts`/`game_results`/`game_result_players`/`people`
    (migrations 001/026/027). `people` is seeded directly by tests that need it
    (`seed_person`) rather than reproducing the real archive-loading pipeline."""

    def __init__(self):
        self.rows: dict[int, tuple] = {}                    # accounts: account_id -> row
        self.people: dict[str, str] = {}                    # person_id -> primary_name
        self.game_results: dict[int, tuple] = {}             # game_result_id -> row
        self.game_result_players: list[tuple] = []           # flat list of child rows
        self._next_id = 1
        self._next_game_result_id = 1

    def seed_person(self, person_id: str, primary_name: str) -> None:
        self.people[person_id] = primary_name

    def execute(self, sql: str, params: tuple = ()) -> FakeCursor:
        sql_norm = " ".join(sql.split()).lower()

        if sql_norm.startswith("select username, email from accounts"):
            username, email = params
            for _, (aid, u, e, ph) in self.rows.items():
                if u.lower() == username.lower() or e.lower() == email.lower():
                    return FakeCursor([(u, e)])
            return FakeCursor([])

        if sql_norm.startswith("insert into accounts"):
            username, email, password_hash = params
            account_id = self._next_id
            self._next_id += 1
            self.rows[account_id] = (account_id, username, email, password_hash)
            return FakeCursor([(account_id,)])

        if sql_norm.startswith("select account_id, username, email, password_hash"):
            identifier, _identifier_again = params
            for _, (aid, u, e, ph) in self.rows.items():
                if u.lower() == identifier.lower() or e.lower() == identifier.lower():
                    return FakeCursor([(aid, u, e, ph)])
            return FakeCursor([])

        if sql_norm.startswith("select account_id, username, email from accounts"):
            (account_id,) = params
            row = self.rows.get(account_id)
            return FakeCursor([(row[0], row[1], row[2])] if row else [])

        if sql_norm.startswith("select count(*), count(*) filter"):
            (account_id,) = params
            mine = [r for r in self.game_results.values() if r[1] == account_id]
            games_played = len(mine)
            titles_won = sum(1 for r in mine if r[4])
            return FakeCursor([(games_played, titles_won)])

        if sql_norm.startswith("insert into game_results"):
            account_id, source, natural_key, champion = params
            conflict = any(
                r[1] == account_id and r[2] == source and r[3] == natural_key
                for r in self.game_results.values()
            )
            if conflict:
                return FakeCursor([])   # ON CONFLICT DO NOTHING RETURNING -- no rows
            game_result_id = self._next_game_result_id
            self._next_game_result_id += 1
            self.game_results[game_result_id] = (
                game_result_id, account_id, source, natural_key, champion)
            return FakeCursor([(game_result_id,)])

        if sql_norm.startswith("insert into game_result_players"):
            existing = {(r[0], r[1]) for r in self.game_result_players}
            for k in range(0, len(params), 7):
                row = tuple(params[k:k + 7])
                if (row[0], row[1]) not in existing:
                    self.game_result_players.append(row)
                    existing.add((row[0], row[1]))
            return FakeCursor([])

        if sql_norm.startswith("select p.person_id, p.primary_name, sum(grp.sim_bat_runs)"):
            (account_id,) = params
            return FakeCursor(self._leaderboard(account_id, stat_index=2, limit=5))

        if sql_norm.startswith("select p.person_id, p.primary_name, sum(grp.sim_bowl_wickets)"):
            (account_id,) = params
            return FakeCursor(self._leaderboard(account_id, stat_index=4, limit=4))

        raise AssertionError(f"FakeConn does not know this query: {sql_norm[:80]!r}")

    def _leaderboard(self, account_id: int, *, stat_index: int, limit: int) -> list[tuple]:
        # stat_index into a game_result_players row: 2=sim_bat_runs, 4=sim_bowl_wickets.
        # Mirrors the real SQL's own group-by-person / sum / order-by-total-desc-then-
        # name-asc / limit exactly, computed in Python over the same in-memory rows
        # rather than reproducing a SQL engine.
        my_result_ids = {gr[0] for gr in self.game_results.values() if gr[1] == account_id}
        totals: dict[str, int] = {}
        for row in self.game_result_players:
            game_result_id, person_id = row[0], row[1]
            if game_result_id not in my_result_ids:
                continue
            value = row[stat_index]
            if value is None:
                continue
            totals[person_id] = totals.get(person_id, 0) + value
        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], self.people[kv[0]]))
        return [(pid, self.people[pid], total) for pid, total in ranked[:limit]]


@pytest.fixture
def conn():
    return FakeConn()


class _SquadEntry:
    """A minimal stand-in for `web.app`'s `JourneySquadEntryOut` -- `save_game_result`
    only ever reads these six attributes off whatever it's given, so a real import of
    `web.app` (which needs the deck at module load, per `tests/test_web.py`'s own
    docstring on why that module isn't imported in lightweight tests) is unnecessary
    here."""

    def __init__(self, person_id, sim_bat_runs=None, sim_bat_balls=None,
                 sim_bowl_wickets=None, sim_bowl_runs=None, sim_bowl_balls=None):
        self.person_id = person_id
        self.sim_bat_runs = sim_bat_runs
        self.sim_bat_balls = sim_bat_balls
        self.sim_bowl_wickets = sim_bowl_wickets
        self.sim_bowl_runs = sim_bowl_runs
        self.sim_bowl_balls = sim_bowl_balls


def test_a_new_account_can_be_created(conn):
    account = accounts.create_account(conn, "krausemonet", "km@example.com", "correcthorse")
    assert account.username == "krausemonet"
    assert account.email == "km@example.com"
    assert account.account_id is not None


def test_a_duplicate_username_is_rejected_case_insensitively(conn):
    accounts.create_account(conn, "krausemonet", "a@example.com", "correcthorse")
    with pytest.raises(accounts.AccountError, match="username"):
        accounts.create_account(conn, "KrauseMonet", "b@example.com", "correcthorse")


def test_a_duplicate_email_is_rejected_case_insensitively(conn):
    accounts.create_account(conn, "alice", "shared@example.com", "correcthorse")
    with pytest.raises(accounts.AccountError, match="email"):
        accounts.create_account(conn, "bob", "Shared@Example.com", "correcthorse")


@pytest.mark.parametrize("username", ["ab", "a" * 25, "has space", "has-dash", ""])
def test_an_invalid_username_is_rejected(conn, username):
    with pytest.raises(accounts.AccountError):
        accounts.create_account(conn, username, "ok@example.com", "correcthorse")


def test_a_short_password_is_rejected(conn):
    with pytest.raises(accounts.AccountError, match="password"):
        accounts.create_account(conn, "alice", "alice@example.com", "short")


def test_an_invalid_email_is_rejected(conn):
    with pytest.raises(accounts.AccountError, match="email"):
        accounts.create_account(conn, "alice", "not-an-email", "correcthorse")


def test_authenticate_accepts_the_right_password_by_username(conn):
    accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    account = accounts.authenticate(conn, "alice", "correcthorse")
    assert account is not None
    assert account.username == "alice"


def test_authenticate_accepts_the_right_password_by_email(conn):
    accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    account = accounts.authenticate(conn, "alice@example.com", "correcthorse")
    assert account is not None


def test_authenticate_rejects_the_wrong_password(conn):
    accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    assert accounts.authenticate(conn, "alice", "wrong password") is None


def test_authenticate_rejects_an_unknown_identifier(conn):
    assert accounts.authenticate(conn, "nobody", "whatever") is None


def test_get_account_returns_none_for_an_unknown_id(conn):
    assert accounts.get_account(conn, 999) is None


def test_get_account_returns_the_right_account(conn):
    created = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    fetched = accounts.get_account(conn, created.account_id)
    assert fetched == created


def test_a_new_game_result_is_saved(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "V Kohli")
    squad = [_SquadEntry("p1", sim_bat_runs=50, sim_bat_balls=30)]
    saved = accounts.save_game_result(conn, account.account_id, "solo", "seed-moves-1",
                                       champion=True, squad=squad)
    assert saved is True
    assert len(conn.game_results) == 1
    assert len(conn.game_result_players) == 1


def test_saving_the_same_natural_key_twice_is_a_no_op(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "V Kohli")
    squad = [_SquadEntry("p1", sim_bat_runs=50, sim_bat_balls=30)]
    first = accounts.save_game_result(conn, account.account_id, "solo", "seed-moves-1",
                                       champion=True, squad=squad)
    second = accounts.save_game_result(conn, account.account_id, "solo", "seed-moves-1",
                                        champion=True, squad=squad)
    assert first is True
    assert second is False
    assert len(conn.game_results) == 1          # not two
    assert len(conn.game_result_players) == 1   # not doubled


def test_a_different_natural_key_saves_a_second_game(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "V Kohli")
    squad = [_SquadEntry("p1", sim_bat_runs=50, sim_bat_balls=30)]
    accounts.save_game_result(conn, account.account_id, "solo", "seed-moves-1",
                               champion=True, squad=squad)
    second = accounts.save_game_result(conn, account.account_id, "solo", "seed-moves-2",
                                        champion=False, squad=squad)
    assert second is True
    assert len(conn.game_results) == 2


def test_a_squad_member_who_neither_batted_nor_bowled_gets_no_child_row(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "V Kohli")
    conn.seed_person("p2", "Never Played")
    squad = [_SquadEntry("p1", sim_bat_runs=10, sim_bat_balls=8), _SquadEntry("p2")]
    accounts.save_game_result(conn, account.account_id, "solo", "seed-moves-1",
                               champion=False, squad=squad)
    assert len(conn.game_result_players) == 1
    assert conn.game_result_players[0][1] == "p1"


def test_profile_stats_with_no_saved_games(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    stats = accounts.profile_stats(conn, account.account_id)
    assert stats.games_played == 0
    assert stats.titles_won == 0
    assert stats.top_batters == []
    assert stats.top_bowlers == []


def test_profile_stats_counts_games_and_titles(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "V Kohli")
    squad = [_SquadEntry("p1", sim_bat_runs=10, sim_bat_balls=8)]
    accounts.save_game_result(conn, account.account_id, "solo", "k1", champion=True, squad=squad)
    accounts.save_game_result(conn, account.account_id, "solo", "k2", champion=False, squad=squad)
    accounts.save_game_result(conn, account.account_id, "room", "k3", champion=True, squad=squad)
    stats = accounts.profile_stats(conn, account.account_id)
    assert stats.games_played == 3
    assert stats.titles_won == 2


def test_profile_stats_sums_runs_across_games_for_the_same_player(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "V Kohli")
    accounts.save_game_result(conn, account.account_id, "solo", "k1", champion=False,
                               squad=[_SquadEntry("p1", sim_bat_runs=40, sim_bat_balls=30)])
    accounts.save_game_result(conn, account.account_id, "solo", "k2", champion=False,
                               squad=[_SquadEntry("p1", sim_bat_runs=60, sim_bat_balls=40)])
    stats = accounts.profile_stats(conn, account.account_id)
    assert len(stats.top_batters) == 1
    assert stats.top_batters[0].person_id == "p1"
    assert stats.top_batters[0].total == 100


def test_profile_stats_ranks_top_5_batters_and_top_4_bowlers_by_total(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    for i in range(6):
        conn.seed_person(f"bat{i}", f"Batter {i}")
    for i in range(5):
        conn.seed_person(f"bowl{i}", f"Bowler {i}")
    squad = (
        [_SquadEntry(f"bat{i}", sim_bat_runs=(i + 1) * 10, sim_bat_balls=10) for i in range(6)]
        + [_SquadEntry(f"bowl{i}", sim_bowl_wickets=(i + 1), sim_bowl_runs=20, sim_bowl_balls=24)
           for i in range(5)]
    )
    accounts.save_game_result(conn, account.account_id, "solo", "k1", champion=False, squad=squad)
    stats = accounts.profile_stats(conn, account.account_id)
    assert len(stats.top_batters) == 5
    assert [r.person_id for r in stats.top_batters] == ["bat5", "bat4", "bat3", "bat2", "bat1"]
    assert len(stats.top_bowlers) == 4
    assert [r.person_id for r in stats.top_bowlers] == ["bowl4", "bowl3", "bowl2", "bowl1"]


def test_profile_stats_never_shows_a_pure_bowler_as_a_batter(conn):
    account = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    conn.seed_person("p1", "Pure Bowler")
    squad = [_SquadEntry("p1", sim_bowl_wickets=3, sim_bowl_runs=20, sim_bowl_balls=24)]
    accounts.save_game_result(conn, account.account_id, "solo", "k1", champion=False, squad=squad)
    stats = accounts.profile_stats(conn, account.account_id)
    assert stats.top_batters == []   # never a manufactured 0 (A23/A71)
    assert len(stats.top_bowlers) == 1


def test_profile_stats_raises_for_an_unknown_account(conn):
    with pytest.raises(accounts.AccountError):
        accounts.profile_stats(conn, 999)
