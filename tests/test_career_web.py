"""Build a Career's API (A199): the signed checkpoint, resuming from it, and the routes.

The checkpoint replaces replay-from-scratch, so the one property everything rests on is that
resuming from a checkpoint gives exactly the career a full replay gives -- checked here on
real seasons, through a JSON round trip, as the token carries it.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import career_routes as R

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


@pytest.fixture(scope="module")
def client():
    from web.app import app
    with TestClient(app) as c:
        yield c


def _auto_moves(deck, model, seed, setup, seasons):
    """An automatic player's moves through `seasons` completed seasons."""
    moves = []
    while True:
        c = C.play_career(deck, model, seed, setup, moves)
        if c.complete or len(c.seasons) >= seasons and c.pending.kind == "offer":
            return moves, c
        moves.append(C.policy_move(c, "random", __import__("random").Random(len(moves))))


def test_resuming_from_a_checkpoint_is_the_same_career_as_a_replay(deck, model):
    setup = C.Setup("Tester", "spin", 2012)
    moves, full = _auto_moves(deck, model, 41, setup, 4)
    for stop in range(1, len(full.seasons)):
        # the moves that produced the first `stop` seasons, and the checkpoint after them
        prefix = []
        while len(C.play_career(deck, model, 41, setup, prefix).seasons) < stop or \
                C.play_career(deck, model, 41, setup, prefix).pending.kind != "offer":
            prefix.append(moves[len(prefix)])
        at = C.play_career(deck, model, 41, setup, prefix)
        cp = C.Checkpoint.from_dict(json.loads(json.dumps(at.checkpoint.to_dict())))
        resumed = C.play_career(deck, model, 41, setup, moves[len(prefix):], resume=cp)
        assert [vars(s) for s in resumed.seasons] == [vars(s) for s in full.seasons]
        assert (resumed.pending is None) == (full.pending is None)
        if full.pending is not None:
            assert resumed.pending.offers == full.pending.offers
        assert vars(resumed.checkpoint) == vars(full.checkpoint)


def _through_moments(client, d, answer="m1"):
    """Answer key moments (A204) until the season they paused finishes."""
    while d["pending"] and d["pending"]["kind"] == "moment":
        r = client.post("/api/career/move", json={"token": d["token"], "move": answer})
        assert r.status_code == 200, r.text
        d = r.json()
    return d


def _token(client, **kw):
    body = {"name": "Tester", "role": "opener", "debut": 2014, "seed": 77, **kw}
    r = client.post("/api/career/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_new_career_is_asked_for_a_franchise_and_names_its_two_spins(client):
    d = _token(client)
    assert d["pending"]["kind"] == "offer" and d["pending"]["year"] == 2014
    assert len(d["pending"]["offers"]) == C.OFFERS and not d["pending"]["can_retire"]
    assert d["start"]["name"] and d["ceiling"]["name"] and d["seasons"] == []


def test_a_season_is_played_one_move_at_a_time(client, monkeypatch):
    """The seed is one whose first event asks a question, so the offer has to be carried
    beside the checkpoint until the answer arrives. Nobody is released here (form is not
    what this test is about, and an engine change once ended this career after two)."""
    monkeypatch.setattr(C, "POOR_PERCENTILE", 0.0)
    seed = next(s for s in range(1, 500) if len(C.draw_event(s, 0, role="opener").options) > 1)
    d = _token(client, seed=seed)
    asked = False
    seasons = 0
    while seasons < 2:
        p = d["pending"]
        asked |= p["kind"] == "event"
        move = {"offer": "o0", "event": "e0", "mega": "b0",
                "plan": "p" + "0" * len(p.get("plan") or [])}.get(p["kind"], "m1")
        r = client.post("/api/career/move", json={"token": d["token"], "move": move})
        assert r.status_code == 200, r.text
        d = r.json()
        if d["seasons"] and len(d["seasons"]) > seasons:
            seasons = len(d["seasons"])
            s = d["seasons"][-1]
            assert 70 <= s["card"] <= 99 and s["teams"] in (8, 9, 10)
    assert asked and d["pending"]["can_retire"] and d["pending"]["season_no"] == 3


def test_a_career_ends_with_a_summary_and_a_line_with_no_player_in_it(client):
    d = _token(client)
    while not d["seasons"]:
        p = d["pending"]
        move = {"offer": "o0", "event": "e0"}.get(p["kind"], "m1")
        d = client.post("/api/career/move", json={"token": d["token"], "move": move}).json()
    if d["pending"]:
        d = client.post("/api/career/move", json={"token": d["token"], "move": "r"}).json()
    assert d["summary"]["seasons"] == 1 and d["pending"] is None
    assert d["summary"]["ending"] in ("retired", "released")
    assert d["start"]["name"] not in d["share"] and d["ceiling"]["name"] not in d["share"]
    assert "Tester" not in d["share"]
    # The full address, so a chat app makes it a link a reader can tap.
    assert d["share"].splitlines()[-1] == "https://fineleg.app/career"
    after = client.post("/api/career/move", json={"token": d["token"], "move": "o0"})
    assert after.status_code == 409


def test_a_tampered_career_is_refused(client):
    d = _token(client)
    payload, sig = d["token"].rsplit(".", 1)
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    body["cp"]["k"] = 5                                    # skip ahead five seasons
    forged = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    for token in (f"{forged}.{sig}", f"{payload}.{'0' * len(sig)}", "nonsense"):
        r = client.post("/api/career/move", json={"token": token, "move": "o0"})
        assert r.status_code == 400


def test_a_career_signature_is_never_a_session_signature():
    """Domain separation, checked directly: the shapes of a cookie and a token differ too,
    so a format check would refuse a swap even without it -- this is what makes the swap
    impossible rather than merely unlikely."""
    from web import auth
    for payload in ("1.1700000000", "e30", "anything"):
        assert R._sign(payload) != auth._sign(payload)


def test_a_session_cookie_is_not_a_career_and_a_career_is_not_a_session(client):
    from web import auth
    cookie = auth.make_session_cookie(1)
    r = client.post("/api/career/move", json={"token": cookie, "move": "o0"})
    assert r.status_code == 400
    assert auth.verify_session_cookie(_token(client)["token"]) is None


@pytest.mark.parametrize("body", [
    {"name": "<b>x</b>"}, {"name": "   "}, {"role": "umpire"}, {"debut": 2007}, {"debut": 2018},
])
def test_a_career_that_cannot_exist_is_refused(client, body):
    r = client.post("/api/career/start",
                    json={"name": "Tester", "role": "opener", "debut": 2014, **body})
    assert r.status_code in (400, 422)


def test_a_move_that_does_not_fit_is_a_conflict_not_a_crash(client):
    d = _token(client)
    for move in ("r", "o9", "e0", "zz"):
        r = client.post("/api/career/move", json={"token": d["token"], "move": move})
        assert r.status_code == 409, move


def test_every_event_options_hint_says_what_its_numbers_do():
    for e in C.EVENTS:
        for o in e.options:
            h = R.option_hint(o).lower()
            # [A214] the Rating points it applies, exactly, and every way it can turn out
            own = [part for part in h.split("; ") if "% chance" not in part]
            assert (f"{o.season:+d} rating this season" in own) == bool(o.season)
            assert (f"{o.permanent:+d} rating for good" in own) == bool(o.permanent)
            assert ("captain" in h) == o.captain
            assert (f"miss {o.missed} matches" in h) == bool(o.missed)
            for out in o.outcomes:
                if out.permanent or out.missed:
                    assert f"{round(100 * out.chance)}% chance" in h


def test_a_suspended_club_is_named_when_there_is_no_offer_to_stay(client, deck):
    """Chennai have no team in 2016: a 2015 Chennai career is told why it cannot stay. The
    seed is chosen so Chennai do offer a 2015 contract, so the test always has a case."""
    seed = next(s for s in range(1, 500)
                if any("Chennai" in o.franchise for o in C.offers_for(deck, s, 0, 2015, None, None)))
    d = _token(client, debut=2015, seed=seed)
    while not d["seasons"]:
        p = d["pending"]
        move = (f"o{next(n for n, o in enumerate(p['offers']) if 'Chennai' in o['franchise'])}"
                if p["kind"] == "offer" else "e0" if p["kind"] == "event" else "m1")
        d = client.post("/api/career/move", json={"token": d["token"], "move": move}).json()
    assert d["seasons"][0]["franchise"] == "Chennai Super Kings"
    assert d["pending"]["club_gone"] == "Chennai Super Kings"
    assert not any(o["stay"] for o in d["pending"]["offers"])
    # and a club that does play the next year is not named
    later = _token(client, debut=2017, seed=seed)
    assert later["pending"]["club_gone"] is None


def test_hints_name_missed_matches_and_an_ending():
    for e in C.EVENTS:
        for o in e.options:
            h = R.option_hint(o).lower()
            assert (f"miss {o.missed} matches" in h) == (o.missed > 0)
            assert ("career ends" in h) == o.retire


def test_a_three_year_deal_through_the_api(client, deck):
    seed = next(s for s in range(1, 2000) if all(len(C.draw_event(s, k, role="opener").options) == 1 for k in range(2)))
    # 2015 and 2016: a 2017 deal's second year would be 2018, a mega year that ends it (A212)
    d = _token(client, seed=seed, debut=2015)
    clubs_2016 = {C.canonical(deck.cards_by_fs[fs][0].franchise) for fs in C.year_fs_ids(deck, 2016)}
    i = next(n for n, o in enumerate(d["pending"]["offers"]) if C.canonical(o["franchise"]) in clubs_2016)
    assert d["pending"]["offers"][i]["price3"]
    d = client.post("/api/career/move", json={"token": d["token"], "move": f"c{i}"}).json()
    d = _through_moments(client, d)
    assert d["seasons"][0]["contract_year"] == 1
    o = d["pending"]["offers"]
    assert len(o) == 1 and o[0]["contract_year"] == 2 and o[0]["price3"] is None
    bad = client.post("/api/career/move", json={"token": d["token"], "move": "c0"})
    assert bad.status_code == 409


def test_an_event_card_reaches_the_page_with_its_real_name(client, deck):
    seed = next(s for s in range(1, 5000) if C.draw_event(s, 0, role="opener").key == "mentor")
    d = _token(client, seed=seed)
    d = client.post("/api/career/move", json={"token": d["token"], "move": "o0"}).json()
    e = d["pending"]["event"]
    assert e["key"] == "mentor" and "{name}" not in e["text"] and "senior player" not in e["text"]
    names = {c.name for fs in C.year_fs_ids(deck, 2014) for c in deck.cards_by_fs[fs]}
    assert any(n in e["text"] for n in names)


def test_the_move_that_plays_a_season_carries_it_as_it_happened(client):
    """And only that move: a reload (view) and an offer that leads to an event carry none."""
    seed = next(s for s in range(1, 2000) if len(C.draw_event(s, 0, role="opener").options) > 1)
    d = _token(client, seed=seed)
    assert d["season_play"] is None
    d = client.post("/api/career/move", json={"token": d["token"], "move": "o0"}).json()
    assert d["pending"]["kind"] == "event" and d["season_play"] is None
    d = client.post("/api/career/move", json={"token": d["token"], "move": "e0"}).json()
    if d["pending"] and d["pending"]["kind"] == "moment":
        # paused at a key moment: the season so far, marked incomplete, and no new season yet
        assert d["season_play"] and d["season_play"]["complete"] is False and not d["seasons"]
        assert d["pending"]["moment"]["stakes"] and len(d["pending"]["moment"]["options"]) == 3
    d = _through_moments(client, d)
    sp = d["season_play"]
    s = d["seasons"][-1]
    assert sp and sp["complete"] and len(sp["feed"]) >= 14 and sp["table"]
    assert sum(l["runs"] for l in sp["feed"]) == s["runs"]
    assert sum(1 for l in sp["feed"] if l["watch"] is not None) == len(sp["watch"])
    assert all(l["watch"] is not None for l in sp["feed"] if l["stage"] != "league")
    for w in sp["watch"]:
        assert w["home_innings"]["over_log"] and w["yours"]
    v = client.post("/api/career/view", json={"token": d["token"]}).json()
    assert v["season_play"] is None and v["seasons"] == d["seasons"]


def _auto_move(p) -> str:
    """Any pending decision answered as a plain automatic player would: the first offer or
    answer, a balanced call, retention when offered, no bowling plan."""
    if p["kind"] == "mega":
        return "k" if p["mega"]["retain"] else f"b{p['mega']['suggested']}"
    if p["kind"] == "plan":
        return "p" + "0" * len(p["plan"])
    return {"offer": "o0", "event": "e0"}.get(p["kind"], "m1")


def test_a_paused_moment_says_what_kind_it_is_and_the_feed_carries_form(client):
    """Each key moment the API pauses at names its kind (A205), and a played match line
    carries the live form line the page draws."""
    d, kinds, forms = _token(client, role="middle", seed=1), set(), []
    for _ in range(40):
        p = d["pending"]
        if p is None:
            break
        if p["kind"] == "moment":
            kinds.add(p["moment"]["kind"])
            forms += [l["form"] for l in d["season_play"]["feed"] if l["form"]]
        r = client.post("/api/career/move", json={"token": d["token"], "move": _auto_move(p)})
        assert r.status_code == 200, r.text
        d = r.json()
    assert kinds <= {"must_win", "playoff", "make_or_break"}
    assert "make_or_break" in kinds, kinds
    assert forms and all(1 <= len(f) <= 5 for f in forms)


def test_each_milestone_rides_on_the_feed_row_of_its_match(client):
    """A209: the API puts a season's milestones on the very rows the career recorded them
    against, and lists the season's honours on the season card. Played through the routes,
    opener seed 7, until the first season with a match milestone."""
    d = client.post("/api/career/start", json={"name": "Ms", "role": "opener", "debut": 2010,
                                               "seed": 7}).json()
    found = None
    for _ in range(40):
        p = d["pending"]
        if p is None:
            break
        d = client.post("/api/career/move", json={"token": d["token"], "move": _auto_move(p)}).json()
        sp = d.get("season_play")
        if sp and sp["complete"] and any(l["milestones"] for l in sp["feed"]):
            found = d
            break
    assert found, "the fixture must reach a season with a match milestone"
    _, _, cp, _ = R.read_token(found["token"])
    rec = cp.seasons[-1]
    on_rows = [(i, t) for i, l in enumerate(found["season_play"]["feed"]) for t in l["milestones"]]
    assert on_rows == [(m.match, m.text) for m in rec.milestones if m.match is not None]
    assert found["seasons"][-1]["milestones"] == [m.text for m in rec.milestones]


def test_a_played_season_carries_its_analysis_with_your_side_tracked(client, deck, model):
    """A211: the draft season's chart set, for the career's season. Your side's runs in it are
    your side's innings, summed here from the season's own scorecards; a reload carries none."""
    d = client.post("/api/career/start", json={"name": "An", "role": "opener", "debut": 2014,
                                               "seed": 5}).json()
    moves = []
    while not (d.get("season_play") and d["season_play"]["complete"]):
        p = d["pending"]
        moves.append("o0" if p["kind"] == "offer" else "e0" if p["kind"] == "event" else "m1")
        d = client.post("/api/career/move", json={"token": d["token"], "move": moves[-1]}).json()
    a = d["season_play"]["analysis"]
    assert a is not None
    seed, setup, _, _ = R.read_token(d["token"])
    detail = C.play_career(deck, model, seed, setup, moves).detail
    yours = next(s for r in detail.results for s in (r.home, r.away) if getattr(s, "you", False))
    runs = sum((r.home_innings if r.home is yours else r.away_innings).runs
               for r in detail.results if yours in (r.home, r.away))
    assert a["fixtures"] == len(detail.results)
    assert a["your_total_runs"] == runs > 0
    again = client.post("/api/career/view", json={"token": d["token"]}).json()
    assert again["season_play"] is None


def test_every_match_of_yours_opens_its_own_scorecard(client):
    """[A214] Each feed line names its scorecard, every scorecard is that match (the two
    sides and the score the line shows), and none carries the reveal's over log."""
    d = _token(client, seed=4)
    while not (d.get("season_play") and d["season_play"]["complete"]):
        d = client.post("/api/career/move", json={"token": d["token"],
                                                  "move": _auto_move(d["pending"])}).json()
    sp = d["season_play"]
    assert [l["card"] for l in sp["feed"]] == list(range(len(sp["cards"])))
    for l in sp["feed"]:
        card = sp["cards"][l["card"]]
        assert sp["your_short"] in (card["home"], card["away"])
        assert l["opp_short"] in (card["home"], card["away"])
        mine = card["home_score"] if card["home"] == sp["your_short"] else card["away_score"]
        assert mine.split("/")[0].split(" ")[0] == l["score_for"].split("/")[0].split(" ")[0]
        assert card["home_innings"]["over_log"] == [] and card["away_innings"]["over_log"] == []
        assert card["home_innings"]["batting"] and card["home_innings"]["bowling"]


def test_answers_that_no_longer_replay_resume_from_the_last_season(client):
    """[A214] A token carrying this season's answers from before an engine change: they are
    dropped and the season's choices asked again, rather than the career refused (which the
    page takes for an unreadable career and forgets)."""
    d = _token(client, seed=4)
    seed, setup, cp, _ = R.read_token(d["token"])
    deck, model = R._state()
    career = C.play_career(deck, model, seed, setup, [], resume=cp)
    stale = R.make_token(seed, setup, career, ["o0", "e7"])          # no such answer
    v = client.post("/api/career/view", json={"token": stale})
    assert v.status_code == 200 and v.json()["pending"]["kind"] == "offer"
    m = client.post("/api/career/move", json={"token": stale, "move": "e0"})
    assert m.status_code == 200 and m.json()["pending"]["kind"] == "offer"
    # a move that is simply wrong for a sound token is still refused
    assert client.post("/api/career/move", json={"token": d["token"], "move": "e9"}).status_code == 409


# --- [A217] the page's tier-1 facelift: your player marked, the IPL points table -------------

def _season_with_moves(client, role, seed):
    """Play a career's first season through the API, keeping the moves so the engine's own
    season can be rebuilt beside the response."""
    d = client.post("/api/career/start", json={"name": "Marked", "role": role, "debut": 2014,
                                               "seed": seed}).json()
    moves = []
    while not (d.get("season_play") and d["season_play"]["complete"]):
        moves.append(_auto_move(d["pending"]))
        d = client.post("/api/career/move", json={"token": d["token"], "move": moves[-1]}).json()
    return d, moves


def test_your_player_and_only_him_is_marked_in_every_scorecard(client, deck, model):
    """Each batting and bowling row, crease entry and over carries `you` exactly where the
    engine's own player is the career's (by person id, never the name), so the lists line
    up one to one with the engine's -- including the rule that a bowler with no ball has no
    row. Checked on an all-rounder, so both kinds of mark have somewhere to be."""
    d, moves = _season_with_moves(client, "allrounder", 6)
    seed, setup, _, _ = R.read_token(d["token"])
    detail = C.play_career(deck, model, seed, setup, moves).detail
    sp = d["season_play"]
    assert len(sp["cards"]) == len(detail.cards)
    seen = {"bat": 0, "bowl": 0, "over": 0, "crease": 0}
    for card, r in zip(sp["cards"], detail.cards):
        for key, inn in (("home_innings", r.home_innings), ("away_innings", r.away_innings)):
            out = card[key]
            want = [b.player.person_id == C.YOU_ID for b in inn.batting]
            assert [row["you"] for row in out["batting"]] == want
            seen["bat"] += sum(want)
            bowled = [bo for bo in inn.bowling if bo.balls > 0]
            want = [bo.player.person_id == C.YOU_ID for bo in bowled]
            assert [row["you"] for row in out["bowling"]] == want
            assert [row["name"] for row in out["bowling"]] == [bo.player.name for bo in bowled]
            seen["bowl"] += sum(want)
    for w, r in zip(sp["watch"], detail.watch):
        for key, inn in (("home_innings", r.home_innings), ("away_innings", r.away_innings)):
            for row, o in zip(w[key]["over_log"], inn.over_log):
                assert row["bowler_you"] == (o.bowler_id == C.YOU_ID)
                seen["over"] += row["bowler_you"]
                for end in ("striker", "non_striker"):
                    snap = getattr(o, end)
                    if snap is not None:
                        assert row[end]["you"] == (snap.person_id == C.YOU_ID)
                        seen["crease"] += row[end]["you"]
    assert all(seen.values()), seen


def _balls(overs: str) -> int:
    o, b = overs.split(".")
    return 6 * int(o) + int(b)


def test_the_league_table_is_the_ipl_points_table(client, deck, model):
    """The career's table carries what the solo season's points table draws: position, For
    and Against as runs/overs, and the last five league results. For/Against reproduce each
    row's own NRR (the IPL's all-out rule is in the overs), and the form is each side's last
    five league results in the order they were played, recounted from the season itself."""
    d, moves = _season_with_moves(client, "opener", 9)
    seed, setup, _, _ = R.read_token(d["token"])
    detail = C.play_career(deck, model, seed, setup, moves).detail
    table = d["season_play"]["table"]
    assert [t["pos"] for t in table] == list(range(1, len(table) + 1))
    for t in table:
        nrr = 6 * (t["runs_for"] / _balls(t["overs_for"])
                   - t["runs_against"] / _balls(t["overs_against"]))
        assert round(nrr, 3) == pytest.approx(t["nrr"], abs=0.0015)
        league = [r for r in detail.results if r.stage == "league"
                  and t["short"] in (r.home.short, r.away.short)]
        want = ["T" if r.winner is None else "W" if r.winner.short == t["short"] else "L"
                for r in league][-5:]
        assert t["form"] == want and len(want) == 5
        assert t["won"] + t["lost"] + t["tied"] == t["played"] == len(league)
    assert len({t["short"] for t in table}) == len(table)          # shorts name one side each


def test_a_bowler_who_never_bowled_does_not_shift_the_marks():
    """`web.app._innings_out` leaves out a bowler who got no ball, so the marks must skip him
    too or every row after him reads as somebody else's. A real season rarely lists one ahead
    of you, so the case is built by hand."""
    from types import SimpleNamespace as NS
    pl = lambda pid: NS(person_id=pid)
    inn = NS(batting=[NS(player=pl("a"))],
             bowling=[NS(player=pl("idle"), balls=0), NS(player=pl("b"), balls=12),
                      NS(player=pl(C.YOU_ID), balls=24)],
             over_log=[])
    result = NS(home_innings=inn, away_innings=None)
    out = {"home_innings": {"batting": [{"name": "a"}],
                            "bowling": [{"name": "b"}, {"name": "you"}], "over_log": []}}
    R._mark_you(result, out)
    assert [row["you"] for row in out["home_innings"]["bowling"]] == [False, True]


def test_a_level_match_is_a_tie_in_the_form():
    from types import SimpleNamespace as NS
    a, b = NS(), NS()
    assert C.form_letter(NS(winner=None), a) == "T"
    assert C.form_letter(NS(winner=a), a) == "W"
    assert C.form_letter(NS(winner=a), b) == "L"


# --- [A218] tier 2: the notes' marks, and the auctioneer's prices ---------------------------

def test_every_season_note_is_marked_the_way_it_points(deck):
    """`note_tone` reads the wording the season card's own generators write, so it is fed
    their output here rather than hand-typed sentences: a change of wording it stops
    recognising fails this, instead of quietly losing its ▲ or ▼."""
    import dataclasses
    t = C.note_tone
    assert t(C._effect_words("A lift.", 2, 0, 0)) == "up"
    assert t(C._effect_words("A niggle.", -2, 0, 0)) == "down"
    assert t(C._effect_words("Rebuilt.", 0, 3, 0)) == "up"
    assert t(C._effect_words("Rebuild.", -3, 5, 0)) == ""                 # mixed
    assert t(C._effect_words("Rested.", 0, 0, 2)) == "down"               # matches missed alone
    assert t(C._effect_words("Played through.", 1, 0, 2)) == "up"         # the Rating decides
    assert t(C._effect_words("Nothing.", 0, 0, 0)) == ""
    assert t(C.development_words(1.2)) == "up"
    assert t(C.development_words(-0.8)) == "down"
    assert t(C.absence_words("rested", 2, 5)) == ""                       # information, not a cost
    # [A224] the potential, the clock and the shape's hints
    assert t(C.potential_words(84, 85, 88)) == "up"
    assert t(C.potential_words(84, 86, None)) == "up"
    assert t(C.clock_words(C.CLOCK_SLOW)) == "up"
    assert t(C.clock_words(C.CLOCK_FAST)) == "down"
    assert t(C._effect_words("Fitness.", -1, 0, 0, -0.5)) == ""            # mixed
    assert t(C._effect_words("Managed it.", 0, 0, 3, 0.5)) == "down"
    hints = {sh: [C.shape_words(C.Shape(*sh), c / 2, c / 2 + 1, 25) for c in range(40)]
             for sh in (("early", "gentle"), ("late", "steep"), ("standard", "standard"))}
    marks = {h: t(h) for hs in hints.values() for h in hs if h}
    assert marks == {"At 25 you are already close to your best.": "up",
                     "Past your peak now, but your body is holding up well.": "up",
                     "Still improving at 25: you are coming into your own late.": "up",
                     "Past your peak now, and the years are catching up with you quickly.": "down",
                     "Past your peak now.": ""}
    card = next(c for cs in deck.cards_by_fs.values() for c in cs
                if c.bat_scoring is not None and c.bowl_scoring is not None and c.role == "allrounder")
    better_bat = dataclasses.replace(card, bat_scoring=card.bat_scoring + 0.08,
                                     bat_dismissal=card.bat_dismissal * 0.9)
    worse_bowl = dataclasses.replace(card, bowl_scoring=card.bowl_scoring - 0.1,
                                     bowl_dismissal=card.bowl_dismissal * 0.9)
    assert t(C.skill_words(card, better_bat)) == "up"
    assert t(C.skill_words(better_bat, card)) == "down"
    assert t(C.skill_words(card, worse_bowl)) == "down"
    assert t(C.skill_words(worse_bowl, card)) == "up"
    safer = dataclasses.replace(card, bat_dismissal=card.bat_dismissal * 0.85)   # out less, nothing else
    assert t(C.skill_words(card, safer)) == "up"
    assert t(C.skill_words(safer, card)) == "down"
    mixed = dataclasses.replace(card, bat_scoring=card.bat_scoring + 0.08, bat_dismissal=card.bat_dismissal * 1.2)
    assert t(C.skill_words(card, mixed)) == ""


def test_the_career_auction_carries_its_prices_in_lakh_for_the_voice():
    """The page speaks a price ("three crore, fifty lakh") from `lakh`; it must be the same
    price the screen shows as text, bid by bid and at the hammer."""
    a = C.MegaResult(base=50, sold=True, retained=False, fs_id=1, franchise="Mumbai Indians",
                     short="MI", price=350,
                     bids=[("Rajasthan Royals", "RR", 55), ("Mumbai Indians", "MI", 350)])
    out = R._auction(a, 2018)
    assert out.price_lakh == 350 and out.price == R.crore(350)
    assert [(b.lakh, b.price) for b in out.bids] == [(55, R.crore(55)), (350, R.crore(350))]


def test_a_seasons_notes_reach_the_page_with_one_mark_each(client):
    d, _ = _season_with_moves(client, "pace", 3)
    assert sum(len(s["notes"]) for s in d["seasons"])          # something to mark
    for s in d["seasons"]:
        assert len(s["note_tones"]) == len(s["notes"])
        assert [C.note_tone(n) for n in s["notes"]] == s["note_tones"]


# --- [A219] tier 3: the role lines, and the milestones' kinds ----------------------------------

def test_every_role_line_states_a_rule_the_game_has(client):
    """Each create-screen line makes a claim; each claim is checked against the constant it
    rests on, so retuning the rule without the words (or the words without the rule) fails."""
    roles = {r["key"]: r for r in client.get("/api/career/options").json()["roles"]}
    assert set(roles) == set(C.ROLES) and all(r["blurb"] for r in roles.values())
    span = lambda slots: f"{min(slots)} to {max(slots)}"
    assert span(C.TOP_SLOTS) in roles["opener"]["blurb"]
    assert span(C.MIDDLE_SLOTS) in roles["middle"]["blurb"]
    assert span(C.FINISHER_SLOTS) in roles["finisher"]["blurb"]
    # "the hardest place to lose": the finisher's own bar is the most forgiving
    assert "hardest place to lose" in roles["finisher"]["blurb"]
    assert C.ROLES["finisher"]["poor_percentile"] < C.POOR_PERCENTILE
    assert all(v.get("poor_percentile", C.POOR_PERCENTILE) >= C.ROLES["finisher"]["poor_percentile"]
               for v in C.ROLES.values())
    # "the most injury-prone job", and a spinner "fewer injuries than a quick"
    assert "most injury-prone" in roles["pace"]["blurb"]
    assert max(C.INJURY_RISK, key=C.INJURY_RISK.get) == "pace"
    assert "fewer injuries than a quick" in roles["spin"]["blurb"]
    assert C.INJURY_RISK["spin"] < C.INJURY_RISK["pace"]
    # "bowls in about six matches in ten"
    assert "six matches in ten" in roles["allrounder"]["blurb"]
    assert round(C.ALLROUNDER_BOWL_SHARE * 10) == 6


def test_the_career_cards_milestones_carry_their_kind(client, deck, model):
    """The story timeline colours a milestone by its kind; the API's list must be the
    summary's own (year, text), in order, each with the key its season recorded."""
    d = _token(client, seed=11)
    while d["pending"]:
        d = client.post("/api/career/move", json={"token": d["token"],
                                                  "move": _auto_move(d["pending"])}).json()
    seed, setup, cp, _ = R.read_token(d["token"])
    career = C.play_career(deck, model, seed, setup, [], resume=cp)
    got = d["summary"]["milestones"]
    assert [(m["year"], m["text"]) for m in got] == list(career.summary.milestones)
    assert [m["key"] for m in got] == [m.key for r in career.seasons for m in r.milestones]
    assert got and all(m["key"] for m in got)


def test_the_career_cards_bowling_rates_add_up_from_its_seasons(client):
    """[A220] Average, strike rate and best figures, recounted here from the seasons the API
    lists, not from the summary they came out of."""
    d = client.post("/api/career/start", json={"name": "Rates", "role": "allrounder",
                                               "debut": 2012, "seed": 6}).json()
    while d["pending"]:
        d = client.post("/api/career/move", json={"token": d["token"],
                                                  "move": _auto_move(d["pending"])}).json()
    s, seasons = d["summary"], d["seasons"]
    wkts = sum(r["wickets"] for r in seasons)
    runs = sum(r["bowl_runs"] for r in seasons)
    balls = sum(r["bowl_balls"] for r in seasons)
    assert wkts == s["wickets"] > 0
    assert s["bowling_average"] == round(runs / wkts, 1)
    assert s["bowling_strike_rate"] == round(balls / wkts, 1)
    bests = [tuple(map(int, r["best"].split("/"))) for r in seasons if r["best"]]
    w, r = max(bests, key=lambda b: (b[0], -b[1]))
    assert s["best_bowling"] == f"{w}/{r}"
    # a tie on wickets goes to the cheaper figures, as a scorecard has it
    assert R._best_bowling([type("S", (), {"bowl_balls": 6, "best": (3, 30)}),
                            type("S", (), {"bowl_balls": 6, "best": (3, 17)}),
                            type("S", (), {"bowl_balls": 6, "best": (2, 4)})]) == "3/17"
    assert R._best_bowling([type("S", (), {"bowl_balls": 0, "best": (0, 0)})]) is None
