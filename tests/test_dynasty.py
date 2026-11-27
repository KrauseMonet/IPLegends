"""Club dynasty, stage one (A307).

The card's rules are checked on hand-built careers where the right answer is obvious; the
warmer start is checked in the engine itself; and joining, choosing a club and the pass
gates against a REAL Postgres when IPLEGENDS_SCRATCH_DB points at a throwaway, fully
migrated database.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import os
import random
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from game import career as C
from game import dynasty as D
from tools.snapshot_deck import deck_from, model_from, read_document
from web import accounts, auth
from web import career_routes as R
from web import premium as P

DOC = read_document()
TODAY = datetime.date(2026, 12, 1)
KKR = "Kolkata Knight Riders"


def season(year, club=KKR, **kw):
    base = {"year": year, "club": club, "franchise": club, "short": "KKR", "runs": 0,
            "wickets": 0, "matches": 14, "captain": False, "champion": False,
            "finalist": False, "orange": False, "purple": False, "rating": 80, "grade": 80,
            "ko": []}
    base.update(kw)
    return base


def rec(name, seasons, ended="retired", last_club=None):
    return {"name": name, "role": "opener", "debut": seasons[0]["year"], "ended": ended,
            "finished": True, "epilogue": "", "last_club": last_club or seasons[-1]["club"],
            "seasons": seasons}


# --- the rules, on hand-built careers -------------------------------------------------------

def test_a_career_joins_only_once_finished_and_only_if_it_played_for_the_club():
    played = rec("A", [season(2012)])
    assert D.joins(played, KKR)
    assert not D.joins({**played, "finished": False}, KKR)
    assert D.joins({**played, "ended": None}, KKR)        # ran out of years: still finished
    assert not D.joins(rec("B", [season(2012, club="Mumbai Indians")]), KKR)


def test_the_head_start_is_lost_only_after_a_release_at_the_club_and_only_by_the_latest():
    assert D.START_BONUS == 1
    assert D.start_bonus([], KKR) == 1
    released_here = rec("A", [season(2012)], ended="released")
    released_away = rec("B", [season(2012), season(2013, club="Mumbai Indians")],
                        ended="released")
    retired_here = rec("C", [season(2014)], ended="retired")
    assert D.start_bonus([released_here], KKR) == 0
    assert D.start_bonus([released_away], KKR) == 1
    assert D.start_bonus([released_here, retired_here], KKR) == 1     # the latest decides
    assert D.start_bonus([retired_here, released_here], KKR) == 0


def test_eras_are_titles_at_most_three_years_apart_named_after_the_longest_serving():
    by_year = {y: ["Rao"] for y in range(2010, 2017)}
    by_year[2012] = ["Rao", "Iyer"]
    for y in (2013, 2014, 2015):
        by_year[y] = ["Iyer", "Rao"]
    by_year[2016] = ["Iyer"]
    e = D.eras([2010, 2013, 2019], by_year)                # gap 3 joins, gap 6 does not
    assert [(x.first, x.last, x.titles) for x in e] == [(2010, 2013, 2)]
    assert e[0].name == "Rao" and e[0].label == "The Rao years, 2010–2013"
    assert D.eras([2010, 2014], by_year) == []             # gap 4 splits, one title is no era
    late = D.eras([2014, 2016], by_year)
    assert late[0].name == "Iyer"                          # 3 seasons in 2014-16 against 2


def test_the_card_counts_each_fact_once_and_scores_it_by_the_stated_weights():
    a = rec("Asha Rao", [season(2012, runs=500, champion=True, captain=True, orange=True,
                                ko=[["Final", "Chennai Super Kings", 1]]),
                         season(2013, runs=300, finalist=True,
                                ko=[["Final", "Chennai Super Kings", 0],
                                    # a mislabelled own-club entry, three times: never the rival
                                    ["Qualifier 1", KKR, 1], ["Eliminator", KKR, 1],
                                    ["Qualifier 2", KKR, 1]]),
                         season(2014, club="Mumbai Indians", runs=900, champion=True)])
    b = rec("Ben Iyer", [season(2012, wickets=20, champion=True, purple=True),
                         season(2013, wickets=15, finalist=True)])
    c = D.card([a, b], KKR)
    assert (c.careers, c.seasons) == (2, 4)
    assert c.titles == [2012]                              # two players, one title
    assert c.finals_lost == [2013]
    assert c.caps == [(2012, "orange", "Asha Rao"), (2012, "purple", "Ben Iyer")]
    assert [(x.name, x.value) for x in c.runs] == [("Asha Rao", 800)]   # Mumbai's 900 not counted
    assert [(x.name, x.value) for x in c.wickets] == [("Ben Iyer", 35)]
    assert c.captains == [("Asha Rao", 1)]
    assert c.rival == ("Chennai Super Kings", 1, 1)       # two meetings; KKR itself never a rival
    assert c.covered == [2012, 2013] and 2014 in c.gaps and len(c.gaps) == 17
    assert c.score == 10 * 1 + 4 * 1 + 3 * 2 + 1 * 4


def test_a_club_met_once_in_the_knockouts_is_not_yet_a_rival():
    a = rec("A", [season(2012, ko=[["Final", "Mumbai Indians", 1]])])
    assert D.card([a], KKR).rival is None


def test_the_share_line_names_no_player():
    a = rec("Asha Rao", [season(2012, champion=True), season(2013, champion=True)])
    text = D.share_text(D.card([a], KKR), KKR)
    assert "Asha" not in text and "Rao" not in text
    assert "1 career" in text and "2 titles" in text and "1 era" in text


def test_a_finished_careers_record_keeps_the_seasons_the_card_reads():
    seasons = [NS(year=2012, franchise="Kolkata Knight Riders", short="KKR", runs=410, wickets=0,
                  matches=14, captain=True, champion=True, finalist=True, orange_cap=False,
                  purple_cap=False, rating=82, display=86,
                  feats={"ko": [["Final", "Chennai Super Kings", 1]]}),
               NS(year=2013, franchise="Delhi Daredevils", short="DD", runs=200, wickets=1,
                  matches=12, captain=False, champion=False, finalist=False, orange_cap=False,
                  purple_cap=False, rating=83, display=79, feats=None)]
    cp = NS(seasons=seasons, ended="released", epilogue=None)
    r = D.record_of(NS(name="Asha", role="opener", debut=2012), cp, C.canonical)
    assert r["last_club"] == "Delhi Capitals" and r["epilogue"] == ""
    assert [s["club"] for s in r["seasons"]] == [KKR, "Delhi Capitals"]
    assert r["seasons"][0]["ko"] == [["Final", "Chennai Super Kings", 1]]
    assert r["seasons"][0]["grade"] == 86 and r["seasons"][1]["ko"] == []


def test_a_season_records_each_knockout_opponent_and_the_result():
    from game import career_honours as H

    def line(stage, opp, result):
        return NS(played=True, result=result, opp_franchise=opp, opp_short="X", runs=10,
                  wickets=0, stage=stage, batted=True, out=True, chasing=False, pom=False,
                  opponent=opp, balls=8, bowl_runs=0, bowl_balls=0, margin="")
    feats = H.season_feats([line("league", "Mumbai Indians", "W"),
                            line("Qualifier 1", "Delhi Daredevils", "L"),
                            line("Qualifier 2", "Mumbai Indians", "W"),
                            line("Final", "Chennai Super Kings", "W")], None, False)
    assert feats["ko"] == [["Qualifier 1", "Delhi Capitals", 0],
                           ["Qualifier 2", "Mumbai Indians", 1],
                           ["Final", "Chennai Super Kings", 1]]


def test_a_career_is_finished_once_its_epilogue_is_chosen_however_it_ended():
    assert (D.LAST_YEAR, D.MAX_SEASONS) == (C.LAST_YEAR, C.MAX_SEASONS)
    to_2026 = [NS(year=y) for y in range(2014, 2027)]
    assert D.finished(NS(seasons=to_2026, ended=None, epilogue="coach"))
    assert not D.finished(NS(seasons=to_2026, ended=None, epilogue=None))
    assert D.finished(NS(seasons=to_2026[:3], ended="retired", epilogue="quiet"))
    # an older token reads "" for an epilogue mid-career: not finished
    assert not D.finished(NS(seasons=to_2026[:3], ended=None, epilogue=""))
    nineteen = [NS(year=2008 + i) for i in range(19)]
    assert D.finished(NS(seasons=nineteen[:-1] + [NS(year=2025)], ended=None, epilogue=""))


LONG_SEED = 1007   # 1000 until A323 (released early once the rescue moved the matches): an opener debuting 2014 who plays to 2026


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_real_career_that_plays_on_to_2026_counts_as_finished():
    """The shape the first version missed: most careers end by running out of seasons, with
    no `ended`. An opener debuting 2014 that plays on to 2026 (measured)."""
    deck, model = deck_from(DOC), model_from(DOC)
    c, _ = C.auto_career(deck, model, LONG_SEED, C.Setup("Long", "opener", 2014), "random")
    cp = c.checkpoint
    assert cp.ended is None and cp.seasons[-1].year == 2026 and cp.epilogue
    assert D.finished(cp)


# --- the warmer start, in the engine --------------------------------------------------------

def test_club_standing_adds_the_dynasty_step_only_at_the_dynasty_club():
    s = C.Setup("A", "opener", 2012, dynasty="Delhi Capitals", dynasty_bonus=1)
    assert C.club_standing(s, "Delhi Daredevils", 0) == 1          # the same club, renamed
    assert C.club_standing(s, "Mumbai Indians", 0) == 0
    assert C.club_standing(s, "Delhi Capitals", 1) == 2
    assert C.club_standing(s, "Delhi Capitals", C.STANDING_MAX) == C.STANDING_MAX
    assert C.club_standing(C.Setup("A", "opener", 2012), "Delhi Capitals", 0) == 0


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_dynasty_career_starts_a_step_warmer_at_its_club_in_the_engine():
    deck, model = deck_from(DOC), model_from(DOC)
    plain = C.Setup("Warm", "opener", 2012)
    moves = []
    for _ in range(12):
        c = C.play_career(deck, model, 5, plain, moves)
        if c.seasons:
            break
        moves.append(C.policy_move(c, "random", random.Random(len(moves))))
    club = C.canonical(c.seasons[0].franchise)
    warm = C.play_career(deck, model, 5, dataclasses.replace(plain, dynasty=club, dynasty_bonus=1),
                         moves)
    other = C.play_career(deck, model, 5, dataclasses.replace(
        plain, dynasty="Gujarat Lions" if club != "Gujarat Lions" else KKR, dynasty_bonus=1), moves)
    assert warm.checkpoint.standing == min(C.STANDING_MAX, c.checkpoint.standing + 1)
    assert other.checkpoint.standing == c.checkpoint.standing


def test_the_token_carries_the_dynasty_and_an_older_token_reads_none(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-dynasty-secret")
    s = C.Setup("T", "spin", 2012, dynasty=KKR, dynasty_bonus=1)
    career = NS(checkpoint=C.Checkpoint())
    seed, back, _cp, _p = R.read_token(R.make_token(7, s, career, []))
    assert (back.dynasty, back.dynasty_bonus) == (KKR, 1)
    import base64
    import json
    body = {"v": R.TOKEN_VERSION, "seed": 7, "cp": C.Checkpoint().to_dict(), "partial": [],
            "setup": ["T", "spin", 2012, False, None, "", "", [], "", "R", "R", ""]}
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    old = R.decode_payload(payload)[1]
    assert (old.dynasty, old.dynasty_bonus) == ("", 0)


def test_a_signed_out_start_never_asks_about_a_dynasty(monkeypatch):
    def no_db():
        raise AssertionError("the database was reached")
    monkeypatch.setattr(R.db, "connection", no_db)
    assert R._dynasty_for_start(NS(cookies={})) == ("", 0, ())


# --- the SQL and the routes, against a real Postgres (opt-in) -------------------------------

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


@pytest.fixture
def client(pg, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-dynasty-secret")

    @contextlib.contextmanager
    def same_conn():
        yield pg
    monkeypatch.setattr(R.db, "connection", same_conn)
    monkeypatch.setattr(R, "_today", lambda: TODAY)
    from web.app import app
    with TestClient(app) as c:
        yield c


def _account(pg, name):
    return accounts.create_account(pg, name, f"{name}@example.com", "password1").account_id


def _give_pass(pg, account):
    boss = _account(pg, f"boss_{account}")
    row = P.order(pg, account, "season", TODAY)
    row = P.claim(pg, account, row.pass_id, f"{account:012d}")
    P.approve(pg, boss, row.pass_id, TODAY)


def _finished_token(dynasty, club=KKR, ended="retired", epilogue="coach", name="Done",
                    years=(2012,)):
    recs = [C.SeasonRecord(k=i, year=y, franchise=club, short="KKR", price=20, stayed=False,
                           event="quiet", choice="", captain=False, ability=0.0, teams=9,
                           runs=410, champion=y == 2012) for i, y in enumerate(years)]
    cp = C.Checkpoint(seasons=recs, ended=ended, epilogue=epilogue)
    setup = C.Setup(name, "opener", 2012, dynasty=dynasty, dynasty_bonus=1)
    return R.make_token(9, setup, NS(checkpoint=cp), [])


@needs_pg
def test_a_dynasty_needs_a_pass_and_a_real_club(pg, client):
    a = _account(pg, "dyn_owner")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    assert client.post("/api/career/dynasty", json={"franchise": KKR}).status_code == 403
    _give_pass(pg, a)
    assert client.post("/api/career/dynasty", json={"franchise": "Kolkata"}).status_code == 400
    d = client.post("/api/career/dynasty", json={"franchise": KKR}).json()
    assert d["club"] == KKR and d["card"]["careers"] == 0 and d["card"]["next_bonus"] == 1
    assert len(d["clubs"]) == 15


@needs_pg
@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_pass_holder_with_a_club_starts_dynasty_careers_and_nobody_else_does(pg, client):
    a = _account(pg, "dyn_starter")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    start = {"name": "Dyn", "role": "opener", "debut": 2012}
    assert client.post("/api/career/start", json=start).json()["dynasty"] == ""
    _give_pass(pg, a)
    client.post("/api/career/dynasty", json={"franchise": KKR})
    d = client.post("/api/career/start", json=start).json()
    assert d["dynasty"] == KKR
    assert R.read_token(d["token"])[1].dynasty_bonus == 1
    client.cookies.clear()
    assert client.post("/api/career/start", json=start).json()["dynasty"] == ""
    # a club still set but the pass gone: no longer a dynasty career
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    pg.execute("update premium_passes set status = 'revoked' where account_id = %s", (a,))
    assert client.post("/api/career/start", json=start).json()["dynasty"] == ""


@needs_pg
def test_a_finished_dynasty_career_joins_and_frees_its_slot(pg, client):
    a = _account(pg, "dyn_joiner")
    _give_pass(pg, a)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    client.post("/api/career/dynasty", json={"franchise": KKR})

    ran_out = _finished_token(KKR, ended=None, years=range(2014, 2027), name="Ran out")
    assert client.post("/api/career/slots/5", json={"token": ran_out}).json()["joined_dynasty"] is True
    pending = _finished_token(KKR, epilogue=None)              # finished, epilogue not chosen
    assert client.post("/api/career/slots/1", json={"token": pending}).json()["joined_dynasty"] is False
    elsewhere = _finished_token(KKR, club="Mumbai Indians", name="Away")
    assert client.post("/api/career/slots/2", json={"token": elsewhere}).json()["joined_dynasty"] is False
    plain = _finished_token("", name="Plain")
    assert client.post("/api/career/slots/3", json={"token": plain}).json()["joined_dynasty"] is False

    done = _finished_token(KKR)
    r = client.post("/api/career/slots/4", json={"token": done}).json()
    assert r["joined_dynasty"] is True
    assert sorted(s["slot"] for s in client.get("/api/career/slots").json()["slots"]) == [1, 2, 3]
    again = client.post("/api/career/slots/4", json={"token": done}).json()   # a retried save
    assert again["joined_dynasty"] is True
    (n,), = pg.execute("select count(*) from dynasty_careers where account_id = %s", (a,))
    assert n == 2
    card = client.get("/api/career/dynasty").json()["card"]
    assert (card["careers"], card["titles"]) == (2, [2012])
    assert "Done" not in card["share"]


@needs_pg
def test_a_career_for_a_club_no_longer_the_dynasty_does_not_join(pg, client):
    a = _account(pg, "dyn_switcher")
    _give_pass(pg, a)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    client.post("/api/career/dynasty", json={"franchise": "Mumbai Indians"})
    assert client.post("/api/career/slots/1",
                       json={"token": _finished_token(KKR)}).json()["joined_dynasty"] is False


@needs_pg
def test_a_release_at_the_club_costs_the_next_career_its_head_start(pg, client):
    a = _account(pg, "dyn_released")
    _give_pass(pg, a)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    client.post("/api/career/dynasty", json={"franchise": KKR})
    client.post("/api/career/slots/1", json={"token": _finished_token(KKR, ended="released")})
    assert client.get("/api/career/dynasty").json()["card"]["next_bonus"] == 0


@needs_pg
def test_a_dynasty_goes_with_its_account(pg):
    from web import dynasty as W
    a = _account(pg, "dyn_gone")
    W.set_club(pg, a, KKR)
    W.add(pg, a, "a" * 64, KKR, rec("X", [season(2012)]))
    pg.execute("delete from accounts where account_id = %s", (a,))
    for t in ("dynasties", "dynasty_careers"):
        (n,), = pg.execute(f"select count(*) from {t} where account_id = %s", (a,))
        assert n == 0


# --- stage two: the retired players' jobs (A308) --------------------------------------------

def test_the_staff_is_the_latest_retired_player_in_each_job_at_the_club():
    first = {**rec("Old Coach", [season(2010)]), "epilogue": "coach", "temper": 1}
    later = {**rec("New Coach", [season(2012)]), "epilogue": "coach", "temper": -1}
    acad = {**rec("Academy Man", [season(2011)]), "epilogue": "academy"}
    quiet = {**rec("Quiet One", [season(2013)]), "epilogue": "quiet"}
    away = {**rec("Elsewhere", [season(2014, club="Mumbai Indians")]), "epilogue": "commentary"}
    running = {**rec("Not Done", [season(2015)]), "epilogue": "commentary", "finished": False}
    staff = D.staff_from([first, acad, later, quiet, away, running], KKR)
    assert staff == (("coach", "New Coach", -1), ("academy", "Academy Man", 0))
    assert D.staff_member(staff, "coach")[1] == "New Coach"
    assert D.staff_member(staff, "commentary") is None


def test_the_coachs_words_name_him():
    assert D.staff_words("The coach takes you aside.", "Asha Rao") == "Asha Rao takes you aside."
    assert D.staff_words("Make your case to the coach", "Asha Rao") == "Make your case to Asha Rao"
    assert D.staff_words("A new coach wants to rebuild your technique.", "Asha Rao") == (
        "Asha Rao, now the club's coach, wants to rebuild your technique.")
    assert D.staff_words("The coach", "") == "The coach"


def test_the_commentator_calls_only_the_big_moments_and_the_same_way_each_time():
    from game import press
    hundred = {"k": "hundred", "y": 2014, "match": 3, "stage": "league", "result": "W",
               "margin": "by 20 runs", "runs": 104, "balls": 61, "out": False, "fours": 9,
               "sixes": 5, "opp": "Mumbai Indians", "club": KKR, "batted": True}
    plain = press.Voice("Dev Iyer", "opener", 7)
    called = press.Voice("Dev Iyer", "opener", 7, commentator="Asha Rao")
    a, b = press.render(hundred, plain), press.render(hundred, called)
    assert a is not None and b is not None
    assert b.body[:-1] == a.body and "Asha Rao" in b.body[-1] and "on air" in b.body[-1]
    assert press.render(hundred, called).body == b.body                     # never drawn
    debut = {"k": "debut", "y": 2012, "club": KKR, "franchise": KKR, "price": 20}
    assert all("Asha Rao" not in x for x in (press.render(debut, called) or NS(body=[])).body)


def test_the_token_carries_the_staff_and_an_older_token_reads_none(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-dynasty-secret")
    staff = (("coach", "Asha Rao", 1), ("commentary", "Dev Iyer", 0))
    s = C.Setup("T", "spin", 2012, dynasty=KKR, dynasty_bonus=1, staff=staff)
    back = R.read_token(R.make_token(7, s, NS(checkpoint=C.Checkpoint()), []))[1]
    assert back.staff == staff
    import base64
    import json
    body = {"v": R.TOKEN_VERSION, "seed": 7, "cp": C.Checkpoint().to_dict(), "partial": [],
            "setup": ["T", "spin", 2012, False, None, "", "", [], "", "R", "R", "", KKR, 1]}
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    assert R.decode_payload(payload)[1].staff == ()


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_an_academy_career_starts_with_a_step_of_work_and_says_so():
    deck, model = deck_from(DOC), model_from(DOC)
    plain = C.Setup("Acad", "opener", 2012)
    acad = dataclasses.replace(plain, staff=(("academy", "Asha Rao", 0),))
    work = lambda career: career.checkpoint.traits.get("work", 0.0)
    # the step is there before anything is played (a card can then fall differently, since
    # some cards read your traits, so the two careers are compared at their start)
    assert work(C.play_career(deck, model, 5, acad, [])) == (
        work(C.play_career(deck, model, 5, plain, [])) + D.ACADEMY_WORK) == 1.0

    def first_season(setup):
        moves = []
        for _ in range(12):
            c = C.play_career(deck, model, 5, setup, moves)
            if c.seasons:
                return c.seasons[0]
            moves.append(C.policy_move(c, "random", random.Random(len(moves))))
        raise AssertionError("no season played")
    assert any("Asha Rao's academy" in n for n in first_season(acad).notes)
    assert not any("academy" in n for n in first_season(plain).notes)


def test_the_coachs_name_reaches_the_question_only_at_the_dynasty_club():
    from web.career_routes import EventOptionOut, EventOut, OfferOut, PendingOut
    offer = lambda club: OfferOut(franchise=club, short="X", crest=None, price="₹1 cr",
                                  price3=None, contract_year=0, strength=80, stay=False,
                                  position=None, attack_rank=None)
    def pending(club):
        return PendingOut(kind="event", year=2015, season_no=4, squad=False, offers=[offer(club)],
                          event=EventOut(key="coach", title="A new coach",
                                         text="A new coach wants to rebuild your technique.",
                                         options=[EventOptionOut(label="Rebuild it",
                                                                 hint="The coach thinks so.")]),
                          can_retire=True)
    staff = (("coach", "Asha Rao", 1),)
    career = NS(setup=C.Setup("T", "opener", 2012, dynasty=KKR, staff=staff),
                checkpoint=NS(current=KKR))
    here = R._staff_said(pending(KKR), career)
    assert here.event.text.startswith("Asha Rao, now the club's coach")
    assert here.event.options[0].hint == "Asha Rao thinks so."
    away = R._staff_said(pending("Mumbai Indians"), career)
    assert away.event.text.startswith("A new coach")


@needs_pg
@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_new_dynasty_career_is_given_the_clubs_staff(pg, client):
    from web import dynasty as W
    a = _account(pg, "dyn_staff")
    _give_pass(pg, a)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    client.post("/api/career/dynasty", json={"franchise": KKR})
    W.add(pg, a, "c" * 64, KKR, {**rec("Asha Rao", [season(2012)]), "epilogue": "coach",
                                  "temper": 1})
    d = client.post("/api/career/start", json={"name": "Next", "role": "opener",
                                                "debut": 2013}).json()
    assert d["staff"] == [{"job": "coach", "name": "Asha Rao"}]
    assert R.read_token(d["token"])[1].staff == (("coach", "Asha Rao", 1),)
    card = client.get("/api/career/dynasty").json()["card"]
    assert card["staff"] == [{"job": "coach", "name": "Asha Rao"}]


MENTOR_SEED = 4      # an opener debuting 2012 draws the mentor card at his first club (search)


def _to_mentor(deck, model, setup):
    moves = []
    for _ in range(30):
        c = C.play_career(deck, model, MENTOR_SEED, setup, moves)
        p = c.pending
        if p is not None and p.kind == "event" and p.event.key == "mentor":
            return c, moves
        assert c.pending is not None and len(c.seasons) < 4, "no mentor card: re-find MENTOR_SEED"
        moves.append(C.policy_move(c, "random", random.Random(len(moves))))
    raise AssertionError("no mentor card: re-find MENTOR_SEED")


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_at_the_dynasty_club_the_coach_is_the_mentor_and_passes_on_his_style():
    deck, model = deck_from(DOC), model_from(DOC)
    plain = C.Setup("Taught", "opener", 2012)
    c, moves = _to_mentor(deck, model, plain)
    club = C.canonical(c.pending.offers[0].franchise)
    learn = moves + ["e0"]                                   # "Learn from him"

    def taught(setup):
        """Learn from him, then answer whatever else comes until the season is played."""
        more = list(learn)
        for _ in range(12):
            career = C.play_career(deck, model, MENTOR_SEED, setup, more)
            if career.seasons:
                return career
            more.append(C.policy_move(career, "random", random.Random(len(more))))
        raise AssertionError("the season never played")

    def coached(sign, at=club):
        return dataclasses.replace(plain, dynasty=at, staff=(("coach", "Asha Rao", sign),))
    real = taught(plain).seasons[0].mentors
    assert real and "Asha Rao" not in real
    bold, careful = taught(coached(1)), taught(coached(-1))
    assert bold.seasons[0].mentors == careful.seasons[0].mentors == ["Asha Rao"]
    temper = lambda career: career.checkpoint.traits.get("temper", 0.0)
    assert temper(bold) - temper(careful) == pytest.approx(2 * C.MENTOR_LEAN)
    # a coach of another club is no mentor here
    elsewhere = taught(coached(1, at="Gujarat Lions" if club != "Gujarat Lions" else KKR))
    assert elsewhere.seasons[0].mentors == real


def test_a_careers_papers_carry_its_commentator():
    st = C.Setup("T", "opener", 2012, dynasty=KKR, staff=(("commentary", "Dev Iyer", 0),))
    assert R.voice(3, st).commentator == "Dev Iyer"
    assert R.voice(3, C.Setup("T", "opener", 2012)).commentator == ""
