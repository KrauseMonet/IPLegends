"""Build a Career in the papers (A228): Player of the Match, the facts a season records, the
clippings they become, a real teammate's farewell, the league's real news, ambitions, the
"where are they now" choice, and who you are.

The facts are checked against the season's own records and feed, never against the press
module's own reading of them; the archive-facing constants are checked against the archive."""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from game import career as C
from game import press as P
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
needs_deck = pytest.mark.skipif(DOC is None, reason="no deck snapshot")
ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "ipl_json.zip"


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# Careers chosen to cover a batter, a bowler and an all-rounder, a 2008 debut (the first
# season's news) and a 2017 one (the mega years), with identity and ambitions set.
CASES = [(3, "opener", 2010), (11, "pace", 2010), (5, "allrounder", 2008), (8, "spin", 2017),
         (2, "middle", 2013)]


@pytest.fixture(scope="module")
def careers(deck, model):
    out = []
    for seed, role, debut in CASES:
        setup = C.Setup(f"Test Player{seed}", role, debut, False, seed, "Ranchi", "bow",
                        ("title", "hundred", "one_club"))
        car, moves = C.auto_career(deck, model, seed, setup, "strength")
        out.append((seed, setup, car, moves))
    return out


# --- Player of the Match ------------------------------------------------------------------------


def _bat(pid, runs, balls):
    return NS(player=NS(person_id=pid), runs=runs, balls=balls)


def _bowl(pid, wickets, balls, runs):
    return NS(player=NS(person_id=pid), wickets=wickets, balls=balls, runs=runs)


def _match(home_bat, away_bowl, away_bat, home_bowl, winner):
    home, away = NS(name="H"), NS(name="A")
    r = NS(home=home, away=away, winner={"home": home, "away": away, None: None}[winner],
           home_innings=NS(batting=home_bat, bowling=away_bowl),
           away_innings=NS(batting=away_bat, bowling=home_bowl))
    return r


def test_player_of_the_match_comes_from_the_winning_side():
    # The loser's opener made 90; the winner's best was 40 and two cheap wickets.
    r = _match([_bat("h1", 40, 30)], [_bowl("a9", 0, 24, 40)],
               [_bat("a1", 90, 50)], [_bowl("h9", 2, 24, 20)], winner="home")
    assert C.player_of_match(r) == "h9"           # 2 wickets + 11 under par beats 40 off 30


def test_a_match_level_after_the_super_overs_looks_at_both_sides():
    r = _match([_bat("h1", 40, 30)], [_bowl("a9", 0, 24, 40)],
               [_bat("a1", 90, 50)], [_bowl("h9", 2, 24, 20)], winner=None)
    assert C.player_of_match(r) == "a1"


def test_the_score_counts_strike_and_economy_against_par():
    # Same runs, faster scorer wins; same wickets, cheaper bowler wins.
    assert C.pom_score(50, 25, 0, 0, 0) > C.pom_score(50, 45, 0, 0, 0)
    assert C.pom_score(0, 0, 2, 24, 18) > C.pom_score(0, 0, 2, 24, 38)
    assert C.pom_score(0, 0, 1, 0, 0) == C.FORM_WICKET_RUNS


@needs_deck
def test_your_player_of_the_match_count_is_your_feed_s(careers):
    for _seed, _setup, car, _ in careers:
        assert car.summary.poms == sum(r.poms for r in car.seasons)
        pom_ms = [m for r in car.seasons for m in r.milestones if m.key == "pom"]
        if car.summary.poms:
            assert pom_ms[0].text.startswith("First Player of the Match award")


# --- what a match puts in the papers -----------------------------------------------------------


def _line(**kw):
    base = dict(played=True, batted=True, runs=0, balls=10, out=True, wickets=0, bowl_runs=0,
                bowl_balls=0, stage="league", result="W", margin="", pom=False, landmarks=[],
                fours=0, sixes=0, chasing=False, mate=None, mate_runs=0, opp_franchise="X",
                opponent="X 2010")
    base.update(kw)
    return NS(**base)


@pytest.mark.parametrize("kw,kind", [
    (dict(runs=101, balls=60, landmarks=["hundred"]), "hundred"),
    (dict(runs=72, balls=36), "blitz"),                       # exactly a strike rate of 200
    (dict(runs=72, balls=37), None),                          # just under it
    (dict(runs=69, balls=20), None),                          # fast, but not 70
    (dict(wickets=5, bowl_balls=24, landmarks=["five_for"]), "five_for"),
    (dict(runs=105, balls=60, wickets=3, landmarks=["hundred", "hattrick"]), "hattrick"),
    (dict(stage="Final", runs=52, balls=40), "final_star"),
    (dict(stage="Final", wickets=3, bowl_balls=24), "final_star"),
    (dict(stage="Qualifier 1", pom=True, runs=40), "playoff_hero"),
    (dict(stage="Qualifier 1", pom=True, runs=40, result="L"), None),
    (dict(stage="league", pom=True, runs=40), None),
    (dict(played=False, runs=0), None),
])
def test_what_a_match_must_do_to_make_the_papers(kw, kind):
    f = P.match_fact(_line(**kw), 0, 2010, "Mumbai Indians")
    assert (f["k"] if f else None) == kind


@needs_deck
def test_every_hundred_and_five_for_of_a_season_is_a_story(careers):
    """Recounted from the season's own records: a hundred or a five-for is told unless the
    same match was a hat-trick, which outranks it."""
    for _seed, _setup, car, _ in careers:
        for r in car.seasons:
            kinds = Counter(f["k"] for f in r.press)
            hat = [f for f in r.press if f["k"] == "hattrick"]
            assert kinds["hundred"] + sum(f["runs"] >= 100 for f in hat) == r.hundreds
            assert kinds["five_for"] + sum(f["wickets"] >= 5 for f in hat) == r.five_fors
            assert kinds["hattrick"] == r.hat_tricks


@needs_deck
def test_season_stories_follow_the_season_record(careers):
    for _seed, _setup, car, _ in careers:
        for i, r in enumerate(car.seasons):
            kinds = [f["k"] for f in r.press]
            assert ("title" in kinds) == r.champion, r.year
            assert ("final_lost" in kinds) == (r.finalist and not r.champion), r.year
            assert ("debut" in kinds) == (i == 0)
            caps = sorted(f["cap"] for f in r.press if f["k"] == "cap")
            assert caps == sorted(c for c, on in (("orange", r.orange_cap), ("purple", r.purple_cap)) if on)
            for f in r.press:
                if f["k"] == "cap":
                    assert f["n"] == (r.runs if f["cap"] == "orange" else r.wickets)
                if f["k"] == "title":
                    assert f["n"] == sum(x.champion for x in car.seasons[:i + 1])
                if f["k"] == "mega":
                    assert r.year in C.MEGA_YEARS and i > 0
                if f["k"] == "move":
                    prev = car.seasons[i - 1]
                    assert f["from"] == prev.franchise
                    assert C.canonical(prev.franchise) != C.canonical(r.franchise)


@needs_deck
def test_a_real_teammate_s_farewell_is_his_real_last_season(careers, deck):
    fs_of, years = C._deck_index(deck)
    by_name = defaultdict(set)
    for cards in deck.cards_by_fs.values():
        for c in cards:
            by_name[c.name].add(c.person_id)
    seen = 0
    for _seed, _setup, car, _ in careers:
        for r in car.seasons:
            squad = {c.person_id for c in deck.cards_by_fs[fs_of[(r.franchise, r.year)]]}
            for f in (f for f in r.press if f["k"] == "mate_last"):
                seen += 1
                assert r.year <= C.MATE_LAST_BEFORE
                pid = next(p for p in by_name[f["name"]] if p in squad)
                assert max(years[pid]) == r.year
                assert len(years[pid]) == f["seasons"] >= C.MATE_MIN_SEASONS
                assert f["together"] >= 2 or f["seasons"] >= C.MATE_LONG_SEASONS
    assert seen, "no farewell in any test career: the rule was never exercised"


# --- the clippings -------------------------------------------------------------------------------


def _all_clips(careers):
    for seed, setup, car, _ in careers:
        v = P.Voice(setup.name, setup.role, seed, setup.number, setup.home, setup.celebration)
        for r in car.seasons:
            for f in r.press:
                yield v, f, P.render(f, v)


PRONOUN = re.compile(r"\b(he|him|his|himself|she|her|hers|herself)\b", re.I)


@needs_deck
def test_a_clipping_never_guesses_the_player_s_pronouns(careers):
    """Your player is the person at the keyboard. Checked on every clipping the test careers
    made, and on the farewell and every epilogue."""
    n = 0
    for v, _f, c in _all_clips(careers):
        n += 1
        for text in [c.headline, c.dek, *c.body, c.quote]:
            assert not PRONOUN.search(text), text
    assert n > 30
    v = P.Voice("Ann Example", "pace", 1, None, "Pune", "sky")
    for ending in ("retired", "released", "archive"):
        c = P.farewell(v, ending, 12, 3000, 140, 2, "Mumbai Indians", 2021, 9, 30)
        assert not any(PRONOUN.search(t) for t in [c.headline, *c.body, c.quote])
    for key in P.EPILOGUES:
        c = P.epilogue(key, v, "Mumbai Indians", 2021)
        assert not any(PRONOUN.search(t) for t in [c.headline, *c.body])


@needs_deck
def test_only_your_player_is_ever_quoted(careers):
    for v, _f, c in _all_clips(careers):
        if c.quote:
            assert re.fullmatch(rf"“[^”]+” (said {v.sur}|{v.sur} said|{v.sur} told reporters afterwards)\.",
                                c.quote), c.quote
        assert "“" not in " ".join(c.body)


@needs_deck
def test_a_clipping_s_figures_are_the_match_s(careers):
    for v, f, c in _all_clips(careers):
        text = " ".join([c.headline, c.dek, *c.body])
        if f["k"] in ("hundred", "blitz"):
            assert f"{f['runs']}{'' if f['out'] else '*'} off {f['balls']} ball" in text
        if f["k"] in ("five_for", "hattrick"):
            assert f"{f['wickets']} for {f['bowl_runs']}" in text
        if f["k"] == "debut":
            assert v.name in text and str(f["age"]) in text
        assert c.year == f["y"]


def test_the_same_fact_reads_the_same_every_time_and_careers_differ():
    f = {"k": "hundred", "y": 2016, "m": 3, "club": "Mumbai Indians", "opp": "Kolkata Knight Riders",
         "host": "Mumbai Indians", "stage": "league", "result": "W", "margin": "MI 2016 by 20 runs",
         "runs": 104, "balls": 55, "out": False, "fours": 9, "sixes": 6, "wickets": 0,
         "bowl_runs": 0, "bowl_balls": 0, "pom": True, "chasing": False, "mate": "RG Sharma 30",
         "mate_runs": 30, "batted": True}
    a = P.render(dict(f), P.Voice("Ann Example", "opener", 7))
    b = P.render(dict(f), P.Voice("Ann Example", "opener", 7))
    assert a == b
    heads = {P.render(dict(f), P.Voice("Ann Example", "opener", s)).headline for s in range(40)}
    assert len(heads) >= 3
    assert a.dateline == "MUMBAI"
    assert "104* off 55 balls (9 fours and 6 sixes)" in " ".join(a.body)


def test_a_final_is_datelined_at_the_real_final_s_city_and_a_neutral_season_at_none():
    v = P.Voice("Ann Example", "opener", 7)
    base = {"k": "blitz", "m": 1, "club": "Mumbai Indians", "opp": "Chennai Super Kings",
            "host": "Mumbai Indians", "result": "W", "margin": "", "runs": 80, "balls": 35,
            "out": True, "fours": 5, "sixes": 6, "pom": False}
    assert P.render({**base, "y": 2016, "stage": "league"}, v).dateline == "MUMBAI"
    assert P.render({**base, "y": 2020, "stage": "league"}, v).dateline == ""
    assert P.render({**base, "y": 2013, "stage": "Final", "k": "final_star"}, v).dateline == "KOLKATA"
    assert P.render({**base, "y": 2012, "stage": "league", "host": "Royal Challengers Bangalore"},
                    v).dateline == "BANGALORE"
    assert P.render({**base, "y": 2016, "stage": "league", "host": "Royal Challengers Bangalore"},
                    v).dateline == "BENGALURU"


def test_the_title_credits_the_real_top_scorer_only_when_he_outscored_you():
    v = P.Voice("Ann Example", "opener", 7)
    f = {"k": "title", "y": 2016, "club": "Mumbai Indians", "opp": "Chennai Super Kings", "n": 1,
         "m": 16, "margin": "MI 2016 by 5 runs", "runs": 60, "balls": 40, "out": True,
         "star": "RG Sharma 30", "mate_runs": 30, "played": True, "bowl_balls": 0}
    body = " ".join(P.render(dict(f), v).body)
    assert "Example top-scored" in body and "RG Sharma" not in body
    body = " ".join(P.render({**f, "runs": 12, "balls": 9}, v).body)
    assert "RG Sharma 30 top-scored" in body


def test_only_the_career_s_first_hundred_and_five_for_are_called_first():
    feed = [_line(runs=104, balls=60), _line(runs=30), _line(runs=110, balls=58),
            _line(wickets=5, bowl_balls=24), _line(wickets=6, bowl_balls=24)]
    s = P.SeasonFacts(k=3, year=2016, club="Mumbai Indians", price=100, age=22,
                      first_hundred=True, first_five_for=True)
    facts = [f for f in P.season_facts(s, feed) if f["k"] in ("hundred", "five_for")]
    assert [(f["k"], f["m"], bool(f.get("first"))) for f in facts] == [
        ("hundred", 0, True), ("hundred", 2, False), ("five_for", 3, True), ("five_for", 4, False)]
    later = P.SeasonFacts(k=4, year=2017, club="Mumbai Indians", price=100, age=23)
    assert not any(f.get("first") for f in P.season_facts(later, feed))


def test_an_edition_puts_the_biggest_story_first_and_stops_at_five():
    v = P.Voice("Ann Example", "opener", 7)
    facts = [{"k": "injury", "y": 2016, "club": "Mumbai Indians", "what": "Side strain"},
             {"k": "title", "y": 2016, "club": "Mumbai Indians", "opp": "X", "n": 1, "m": None,
              "played": False}]
    facts += [{"k": "ambition", "y": 2016, "club": "Mumbai Indians", "label": f"A{i}"} for i in range(6)]
    ed = P.edition(facts, v)
    assert ed[0].key == "title" and len(ed) == P.EDITION_MAX


def test_an_unknown_kind_is_skipped_not_an_error():
    assert P.render({"k": "from_the_future", "y": 2030}, P.Voice("A B", "opener", 1)) is None


# --- the league around you, against the archive ------------------------------------------------


@needs_deck
@pytest.mark.parametrize("year,want", [
    (2008, "The first season of the Indian Premier League: eight franchises."),
    (2011, "Kochi Tuskers Kerala play their first IPL season."),
    (2011, "Pune Warriors play their first IPL season."),
    (2012, "Kochi Tuskers Kerala are gone: 2011 was their last season."),
    (2013, "Sunrisers Hyderabad play their first IPL season."),
    (2016, "Chennai Super Kings are absent this season."),
    (2016, "Rajasthan Royals are absent this season."),
    (2018, "Chennai Super Kings are back after 2 seasons away."),
    (2019, "Delhi Daredevils are now Delhi Capitals."),
    (2021, "Kings XI Punjab are now Punjab Kings."),
    (2022, "Lucknow Super Giants play their first IPL season."),
    (2022, "Ten teams this year."),
    (2009, "The season is played in South Africa."),
    (2014, "A mega auction tears up every squad."),
])
def test_the_league_news_is_the_archive_s(deck, year, want):
    assert want in P.league_news(year, C.clubs_by_year(deck), C.MEGA_YEARS)


@needs_deck
def test_a_quiet_year_has_no_invented_news(deck):
    assert P.league_news(2010, C.clubs_by_year(deck), C.MEGA_YEARS) == []


def _archive_matches():
    from etl.franchise_map import canonical
    with zipfile.ZipFile(ARCHIVE) as z:
        for n in z.namelist():
            if n.endswith(".json"):
                info = json.loads(z.read(n))["info"]
                yield (info["dates"][0], n, info.get("city"), info.get("venue", ""),
                       [canonical(t) for t in info["teams"]])


@pytest.mark.skipif(not ARCHIVE.exists(), reason="no archive")
def test_final_venues_home_cities_and_neutral_seasons_are_the_archive_s():
    from etl.franchise_map import canonical
    matches = sorted(_archive_matches(), key=lambda m: (m[0], m[1]))
    last = {}
    cities = defaultdict(set)
    home = defaultdict(Counter)
    for date, _name, city, venue, teams in matches:
        y = int(date[:4])
        last[y] = (city, venue)
        cities[y].add(city)
        if y not in P.NEUTRAL_YEARS:
            for t in teams:
                home[t][city] += 1
    for y, (city, venue) in last.items():
        name, want = P.FINAL_VENUE[y]
        assert venue.startswith(name), (y, venue)
        assert (city or "Dubai") == want, (y, city)
    alias = {"Mohali": {"Chandigarh", "Mohali"}, "Bengaluru": {"Bangalore", "Bengaluru"},
             "Pune": {"Pune"}}
    for franchise, c in home.items():
        top = c.most_common(1)[0][0]
        assert top in alias.get(P.HOME_CITY[franchise], {P.HOME_CITY[franchise]}), (franchise, top)
    indian = set(P.HOME_CITY.values()) | {"Bangalore", "Chandigarh"}
    assert not cities[2009] & indian and not cities[2020] & indian
    assert cities[2021] & indian and cities[2021] - indian


# --- ambitions and who you are ----------------------------------------------------------------


def _rec(year, **kw):
    base = dict(k=year - 2010, year=year, franchise="Mumbai Indians", short="MI", price=100,
                stayed=True, event="quiet", choice="", captain=False, ability=0.0, teams=8)
    base.update(kw)
    return C.SeasonRecord(**base)


def test_an_ambition_is_done_in_the_season_it_is_reached():
    ss = [_rec(2010, runs=1500), _rec(2011, runs=1500), _rec(2012, runs=1100, champion=True)]
    assert C.ambition_status("runs4000", ss) == ("done", 2012)
    assert C.ambition_status("title", ss) == ("done", 2012)
    assert C.ambition_status("titles3", ss) == ("open", None)


def test_one_club_is_broken_by_a_move_and_a_rename_is_not_a_move():
    ss = [_rec(2017 + i, franchise="Delhi Daredevils" if i < 2 else "Delhi Capitals") for i in range(10)]
    assert C.ambition_status("one_club", ss) == ("done", 2026)
    moved = ss[:3] + [_rec(2020, franchise="Mumbai Indians")]
    assert C.ambition_status("one_club", moved) == ("broken", 2020)


@needs_deck
def test_an_ambition_reached_is_a_milestone_and_a_clipping_that_season(careers):
    for _seed, setup, car, _ in careers:
        for key in setup.ambitions:
            state, year = C.ambition_status(key, car.seasons)
            r = next((r for r in car.seasons if r.year == year), None)
            label = C.AMBITIONS[key][0]
            assert (r is not None and f"Ambition achieved: {label}" in [m.text for m in r.milestones]) \
                == (state == "done")
            if state == "done":
                assert {"k": "ambition", "label": label}.items() <= next(
                    f for f in r.press if f.get("label") == label).items()
            # once, in that season and no other
            assert sum(f"Ambition achieved: {label}" in [m.text for m in x.milestones]
                       for x in car.seasons) == (state == "done")


@pytest.mark.parametrize("kw", [dict(number=100), dict(number=-1), dict(home="<b>"),
                                dict(home="x" * 25), dict(celebration="moonwalk"),
                                dict(ambitions=("title", "hundred", "purple", "orange")),
                                dict(ambitions=("title", "title")), dict(ambitions=("fame",))])
def test_who_you_are_is_checked(kw):
    with pytest.raises(C.CareerError):
        C.Setup("Ann Example", "opener", 2012, **kw).check()


def test_a_full_identity_is_accepted():
    C.Setup("Ann Example", "opener", 2012, False, 0, "Navi Mumbai", "aeroplane",
            ("title", "pom10", "one_club")).check()


# --- the end of the road ------------------------------------------------------------------------


@needs_deck
def test_a_finished_career_asks_where_next_before_it_is_complete(deck, model):
    setup = C.Setup("Ann Example", "opener", 2012)
    moves = []
    car = C.play_career(deck, model, 4, setup, moves)
    while car.pending.kind != "offer" or not car.seasons:
        moves.append(C.policy_move(car, "strength", __import__("random").Random(0)))
        car = C.play_career(deck, model, 4, setup, moves)
    car = C.play_career(deck, model, 4, setup, moves + ["r"])
    assert car.pending.kind == "epilogue" and car.summary is None and car.retired
    done = C.play_career(deck, model, 4, setup, moves + ["r", "w1"])
    assert done.complete and done.summary.epilogue == "commentary"
    # resumed from the checkpoint at the question, as the page does
    resumed = C.play_career(deck, model, 4, setup, ["w2"], resume=car.checkpoint)
    assert resumed.summary.epilogue == list(P.EPILOGUES)[2]
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, 4, setup, ["w9"], resume=car.checkpoint)


@needs_deck
def test_a_career_finished_before_the_papers_is_never_asked(deck, model):
    setup = C.Setup("Ann Example", "opener", 2012)
    car, _ = C.auto_career(deck, model, 4, setup, "strength")
    old = car.checkpoint.to_dict()
    del old["epilogue"]
    again = C.play_career(deck, model, 4, setup, [], resume=C.Checkpoint.from_dict(old))
    assert again.complete and again.summary.epilogue == ""


# --- through the API ------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        yield c


def _auto(p):
    if p["kind"] == "mega":
        return "k" if p["mega"]["retain"] else f"b{p['mega']['suggested']}"
    if p["kind"] == "plan":
        return "p" + "0" * len(p["plan"])
    return {"offer": "o0", "event": "e0", "epilogue": "w0"}.get(p["kind"], "m1")


@needs_deck
def test_who_you_are_rides_in_the_token_and_an_old_token_still_reads(client):
    from web import career_routes as R
    d = client.post("/api/career/start", json={
        "name": "Ann Example", "role": "opener", "debut": 2012, "seed": 9, "number": 7,
        "home": "  Navi   Mumbai ", "celebration": "sky", "ambitions": ["title", "hundred"]}).json()
    assert (d["number"], d["home"], d["celebration"]) == (7, "Navi Mumbai", "sky")
    assert [(a["key"], a["state"]) for a in d["ambitions"]] == [("title", "open"), ("hundred", "open")]
    d = client.post("/api/career/move", json={"token": d["token"], "move": "o0"}).json()
    assert d["number"] == 7 and d["home"] == "Navi Mumbai"
    # A token from before A228 carried four setup fields.
    seed, setup, cp, partial = R.read_token(d["token"])
    import base64, json as js
    body = js.dumps({"v": 1, "seed": seed, "setup": [setup.name, setup.role, setup.debut, False],
                     "cp": cp.to_dict(), "partial": partial}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    old = client.post("/api/career/view", json={"token": f"{payload}.{R._sign(payload)}"})
    assert old.status_code == 200 and old.json()["number"] is None and old.json()["ambitions"] == []


@pytest.mark.parametrize("bad", [{"number": 100}, {"home": "<i>x</i>"}, {"celebration": "moonwalk"},
                                 {"ambitions": ["title", "title"]}, {"ambitions": ["a", "b", "c", "d"]}])
def test_a_bad_identity_is_refused(client, bad):
    body = {"name": "Ann Example", "role": "opener", "debut": 2012, **bad}
    assert client.post("/api/career/start", json=body).status_code in (400, 422)


@needs_deck
def test_each_feed_clipping_sits_on_the_match_that_made_it(client):
    d = client.post("/api/career/start", json={"name": "Ann Example", "role": "opener",
                                               "debut": 2010, "seed": 3}).json()
    told = 0
    while d["pending"] and d["pending"]["kind"] != "epilogue":
        d = client.post("/api/career/move", json={"token": d["token"], "move": _auto(d["pending"])}).json()
        sp = d.get("season_play")
        if not sp:
            continue
        for c in sp["press"]:
            line = sp["feed"][c["match"]]
            told += 1
            # A title or a lost final sits on the final even when you sat it out.
            assert line["played"] or c["key"] in ("title", "final_lost")
            assert c["year"] in (d["seasons"][-1]["year"] if d["seasons"] else 0,
                                 d["pending"]["year"] if d["pending"] else 0)
            if c["key"] == "hundred":
                assert line["runs"] >= 100
            if c["key"] == "five_for":
                assert line["wickets"] >= 5
            if c["key"] in ("title", "final_lost", "final_star"):
                assert line["stage"] == "Final"
        if sp["complete"]:
            season = d["seasons"][-1]
            assert [c["headline"] for c in season["press"] if c["match"] is not None] \
                == [c["headline"] for c in sp["press"]]
            assert season["poms"] == sum(l["pom"] for l in sp["feed"])
    assert told, "no clipping in any feed: nothing was checked"
    assert d["pending"]["kind"] == "epilogue"
