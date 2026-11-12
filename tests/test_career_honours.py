"""[A286] Honours in Build a Career, and the continuity a repeated card now carries.

The rules are checked on hand-built seasons, where the right answer is obvious by
construction; every threshold here is STATED rather than read from the module (A207), so a
moved constant fails a test instead of moving it too. One real career checks the wiring.
"""

import random
import re
from types import SimpleNamespace as NS

import pytest

from game import career as C
from game import career_honours as H

# Every career switch as production has it, so the wiring tests meet the questions a real
# player meets (conftest turns each off for older seeded tests).
FOCUS_TESTS = COACH_TESTS = PHASE_TESTS = KEEPER_TESTS = LEANS_TESTS = DRILL_TESTS = True


def line(stage="league", opp="Mumbai Indians 2016", club="Mumbai Indians", result="W",
         played=True, batted=True, runs=0, balls=0, out=True, wickets=0, bowl_runs=0,
         bowl_balls=0, chasing=False, pom=False, margin="by 5 runs"):
    return NS(stage=stage, opponent=opp, opp_short="MI", opp_franchise=club, result=result,
              played=played, batted=batted, runs=runs, balls=balls, out=out, wickets=wickets,
              bowl_runs=bowl_runs, bowl_balls=bowl_balls, chasing=chasing, pom=pom,
              margin=margin)


def rec(year=2016, feed=(), rival=None, captain=False, champion=False, franchise="Mumbai Indians",
        **kw):
    r = NS(year=year, captain=captain, champion=champion, franchise=franchise, runs=0,
           orange_cap=False, purple_cap=False, rating=80, skills=[], mentors=[],
           drills_built={}, phase_built={}, traits={})
    r.feats = H.season_feats(list(feed), rival, captain)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# --- a season's feats ----------------------------------------------------------------------

def test_a_seasons_feats_count_only_the_matches_you_played():
    feed = [line(runs=40, balls=30),
            line(stage="Qualifier 1", runs=20, wickets=2, bowl_balls=24, bowl_runs=30),
            line(stage="Final", runs=55, balls=33, out=False, result="W", pom=True),
            line(runs=99, played=False),
            line(club="Chennai Super Kings", runs=25, balls=20, out=False, chasing=True)]
    f = H.season_feats(feed, ["Chennai Super Kings", "MS Dhoni"], captain=True)
    assert f["po"] == [2, 75, 2]
    assert f["finals"] == [[55, 0, 1, 1]]
    assert f["opp"] == {"Mumbai Indians": [3, 115, 2], "Chennai Super Kings": [1, 25, 0]}
    assert f["chases"] == 1
    assert f["played"] == 4 and f["won"] == 4
    assert f["rival"] == ["MS Dhoni", "Chennai Super Kings", 1, 1, 25, 0]
    assert f["best"]["stage"] == "Final" and f["best"]["runs"] == 55   # 55 + 40 + 10 for a final won


def test_a_chase_counts_only_if_you_were_not_out_with_20_and_the_side_won():
    yes = line(runs=20, out=False, chasing=True)
    for no in (line(runs=19, out=False, chasing=True), line(runs=40, out=True, chasing=True),
               line(runs=40, out=False, chasing=False), line(runs=40, out=False, chasing=True,
                                                            result="L")):
        assert H.season_feats([no], None, False)["chases"] == 0
    assert H.season_feats([yes], None, False)["chases"] == 1


def test_clubs_are_counted_by_their_canonical_name():
    feed = [line(club="Delhi Daredevils", runs=10), line(club="Delhi Capitals", runs=20)]
    assert H.season_feats(feed, None, False)["opp"] == {"Delhi Capitals": [2, 30, 0]}


# --- feat titles ---------------------------------------------------------------------------

def _keys(seasons):
    return {t.key for t in H.titles(seasons)}


def test_big_match_player_is_better_in_the_playoffs_than_usual():
    usual = [line(runs=20)] * 10                       # 20 a league match
    good = [line(stage="Qualifier 1", runs=40)] * 4    # 40 a playoff match: twice as good
    assert "big_match" in _keys([rec(feed=usual + good)])
    # the same 40 a playoff match is ordinary for a player who makes 40 every match
    assert "big_match" not in _keys([rec(feed=[line(runs=40)] * 10 + good)])
    # and three playoff matches are not enough, however good
    assert "big_match" not in _keys([rec(feed=usual + good[:3])])
    # never under 30 a match, whatever the player's usual level
    low = [line(runs=5)] * 10 + [line(stage="Final", runs=28)] * 4
    assert "big_match" not in _keys([rec(feed=low)])


def test_finals_specialist_needs_two_good_finals_and_at_least_half_of_those_played():
    good, bad = line(stage="Final", runs=30), line(stage="Final", runs=29)
    assert "finals" in _keys([rec(feed=[good]), rec(feed=[line(stage="Final", wickets=2)])])
    assert "finals" not in _keys([rec(feed=[good])])
    assert "finals" not in _keys([rec(feed=[good]), rec(feed=[good]), rec(feed=[bad]),
                                  rec(feed=[bad]), rec(feed=[bad])])


def test_scourge_is_the_club_you_did_far_more_against_than_usual():
    rest = [line(club="Chennai Super Kings", runs=20)] * 20
    them = [line(club="Kolkata Knight Riders", runs=40)] * 8
    ts = {t.key: t for t in H.titles([rec(feed=rest + them)])}
    assert ts["scourge"].label == "Scourge of Kolkata Knight Riders"
    assert "scourge" not in _keys([rec(feed=rest + them[:7])])      # eight matches at least
    assert "scourge" not in _keys([rec(feed=[line(club="Chennai Super Kings", runs=35)] * 20 + them)])


def test_chase_master_needs_ten_not_outs_in_won_chases():
    one = line(runs=30, out=False, chasing=True)
    assert "chase" in _keys([rec(feed=[one] * 10)])
    assert "chase" not in _keys([rec(feed=[one] * 9)])


def _cap_keys(seasons):
    return {t.key for t in H.captain_titles(seasons)}


def test_captaincy_titles():
    plain = [rec(year=2010 + i) for i in range(3)]
    assert _cap_keys(plain) == set()
    one = [rec(year=2010, captain=True, champion=True)]
    assert _cap_keys(one) == {"winning_captain"}
    three = [rec(year=2010 + i, captain=True, champion=True, franchise=club)
             for i, club in enumerate(("Mumbai Indians", "Chennai Super Kings", "Rajasthan Royals"))]
    assert "dynasty" in _cap_keys(three) and "winning_captain" not in _cap_keys(three)
    assert _cap_keys(three[:2]) == {"winning_captain"}               # two is not a dynasty
    long = [rec(year=2010 + i, captain=True, franchise="Mumbai Indians") for i in range(6)]
    assert {"leader", "one_club_captain"} <= _cap_keys(long)
    assert "leader" not in _cap_keys(long[:5])
    assert "one_club_captain" not in _cap_keys(long[:3])
    moved = long[:3] + [rec(year=2013, captain=True, franchise="Chennai Super Kings")]
    assert "one_club_captain" not in _cap_keys(moved)
    # a Delhi Daredevils captain who led Delhi Capitals led one club
    renamed = [rec(year=2015, captain=True, franchise="Delhi Daredevils"),
               rec(year=2016, captain=True, franchise="Delhi Daredevils"),
               rec(year=2019, captain=True, franchise="Delhi Capitals"),
               rec(year=2020, captain=True, franchise="Delhi Capitals")]
    assert "one_club_captain" in _cap_keys(renamed)


def test_born_winner_needs_60_percent_of_40_matches_as_captain():
    def led(won, played):
        return rec(captain=True, feed=[line(result="W")] * won + [line(result="L")] * (played - won))
    assert "winning_record" in _cap_keys([led(24, 40)])
    assert "winning_record" not in _cap_keys([led(23, 40)])
    assert "winning_record" not in _cap_keys([led(24, 39)])
    # matches won when not captain do not count
    assert "winning_record" not in _cap_keys([led(12, 20), rec(feed=[line(result="W")] * 20)])


def test_a_captains_knock_is_a_fifty_in_a_final_won_as_captain():
    assert "captains_knock" in _cap_keys([rec(captain=True, feed=[line(stage="Final", runs=50)])])
    assert "captains_knock" not in _cap_keys([rec(captain=True, feed=[line(stage="Final", runs=49)])])
    assert "captains_knock" not in _cap_keys(
        [rec(captain=True, feed=[line(stage="Final", runs=80, result="L")])])
    assert "captains_knock" not in _cap_keys([rec(feed=[line(stage="Final", runs=80)])])


def test_an_older_season_with_no_feats_still_earns_its_captaincy_titles():
    old = rec(captain=True, champion=True)
    old.feats = None
    assert _cap_keys([old]) == {"winning_captain"}
    assert H.titles([old])                          # and nothing else is read off it


# --- the nickname --------------------------------------------------------------------------

def _pace(n, **kw):
    return [rec(year=2010 + i, **kw) for i in range(n)]


def test_a_new_ball_pacer_is_a_swing_king_type():
    seasons = _pace(7, phase_built={"bowl": [0.03, -0.01, -0.01]})
    assert H.nick_type(seasons, "pace", "pace") == "swing"
    assert H.nick_type(seasons[:1], "pace", "pace") == "pace"     # once is not a trade
    assert H.nickname(seasons, "pace", "pace", "Arjun Rao")[0] in H.NICK_POOLS["swing"]


def test_the_papers_name_what_showed_from_season_four_and_never_move_after():
    """Stated, not read from the module: a skill shown twice names the career from its
    fourth season; work chosen names it only from the seventh, if nothing has shown."""
    shown = [rec(year=2010 + i, skills=[["anchor", 1, 1.0]] if i in (1, 2) else []) for i in range(10)]
    assert H.nickname(shown[:3], "opener", None, "A") is None
    assert H.nickname(shown[:4], "opener", None, "A")[1] == 2013
    later = shown + [rec(year=2020 + i, drills_built={"spin": 2.0}) for i in range(6)]
    assert H.nickname(later, "opener", None, "A") == H.nickname(shown[:4], "opener", None, "A")
    worked = [rec(year=2010 + i, drills_built={"spin": 1.0}) for i in range(8)]
    assert H.nickname(worked[:6], "opener", None, "A") is None
    assert H.nickname(worked, "opener", None, "A")[1] == 2016


def test_the_nickname_types_each_pick_their_career():
    assert H.nick_type(_pace(2, drills_built={"death_wkts": 1.0}), "pace", "pace") == "yorker"
    assert H.nick_type(_pace(2, traits={"temper": 1.0}), "spin", "spin") == "spin_strike"
    assert H.nick_type(_pace(2, traits={"temper": -1.0}), "spin", "spin") == "spin_miser"
    assert H.nick_type(_pace(2, traits={"temper": -1.0}), "opener", None) == "anchor"
    assert H.nick_type(_pace(2, phase_built={"bat": [0.03, 0, 0]}), "opener", None) == "launch"
    assert H.nick_type(_pace(2, phase_built={"bat": [0, 0, 0.03]}), "finisher", None) == "finisher"
    assert H.nick_type(_pace(2, drills_built={"spin": 1.0}), "middle", None) == "spin_killer"
    assert H.nick_type(_pace(2, skills=[["aggressor", 1, 1.0]]), "middle", None) == "destroyer"
    assert H.nick_type(_pace(2, captain=True, champion=True), "middle", None) == "general"
    assert H.nick_type(_pace(2), "allrounder", None) == "allrounder"
    assert H.nick_type(_pace(2), "middle", None) is None


def test_what_a_career_showed_names_it_before_what_it_worked_on():
    """A batter who drilled against spin but became an anchor is named for the anchoring."""
    both = _pace(2, drills_built={"spin": 1.0}, skills=[["anchor", 1, 1.0]])
    assert H.nick_type(both, "middle", None) == "anchor"
    assert H.nick_type(_pace(2, drills_built={"spin": 1.0}), "middle", None) == "spin_killer"
    # a pacer who worked the new ball but SHOWED death wickets is named for the death
    pacer = _pace(2, phase_built={"bowl": [0.03, 0, 0]}, skills=[["death_wickets", 2, 1.0]])
    assert H.nick_type(pacer, "pace", "pace") == "yorker"


def test_no_nickname_belongs_to_a_real_player():
    taken = {"Captain Cool", "Universe Boss", "Mr 360", "Hitman", "King Kohli", "The Wall",
             "Thala", "Sir Jadeja", "Yorker King", "Chinna Thala", "Gabbar", "Little Master"}
    names = {n for pool in H.NICK_POOLS.values() for n in pool}
    assert not names & taken


# --- the best match, the rival, the finish --------------------------------------------------

def test_the_best_match_reads_the_half_that_made_it():
    b = {"batted": True, "runs": 6, "balls": 4, "out": True, "bowl_balls": 24, "wkts": 5,
         "bowl_runs": 30, "stage": "Qualifier 1", "opp": "Sunrisers Hyderabad 2017",
         "result": "W", "year": 2017}
    assert H.signature_words(b) == "5/30 v Sunrisers Hyderabad, 2017 Qualifier 1 · won"
    bat = dict(b, runs=90, balls=55, out=False, wkts=0, stage="Final")
    assert H.signature_words(bat) == "90* off 55 v Sunrisers Hyderabad, 2017 Final · won"
    both = dict(b, runs=45, balls=30, stage="league")
    assert H.signature_words(both) == "45 off 30 and 5/30 v Sunrisers Hyderabad, 2017 · won"


def test_the_signature_is_the_careers_best_match():
    a = rec(year=2012, feed=[line(runs=60, balls=40)])
    b = rec(year=2013, feed=[line(stage="Final", runs=50, balls=30)])
    assert H.signature([a, b])["year"] == 2013                # a final lifts a match
    assert H.signature([a, rec(year=2013, feed=[line(runs=60, balls=40)])])["year"] == 2012


def test_rivalries_add_up_each_rival_over_the_seasons_he_was_your_rival():
    a = rec(feed=[line(club="Chennai Super Kings", runs=30, result="W")],
            rival=["Chennai Super Kings", "MS Dhoni"])
    b = rec(feed=[line(club="Chennai Super Kings", runs=10, result="L")],
            rival=["Chennai Super Kings", "MS Dhoni"])
    (r,) = H.rivalries([a, b])
    assert (r["matches"], r["won"], r["runs"], r["seasons"]) == (2, 1, 40, 2)


def test_the_finish_climbs_with_what_the_career_won():
    assert H.finish(0) == "Standard" and H.finish(7) == "Standard"
    assert H.finish(8) == "Bronze" and H.finish(13) == "Bronze"
    assert H.finish(14) == "Silver" and H.finish(21) == "Silver"
    assert H.finish(22) == "Gold" and H.finish(33) == "Gold" and H.finish(34) == "Platinum"
    seasons = [rec(champion=True), rec(orange_cap=True)]
    assert H.finish_points(seasons, mastered=1, titles_=[]) == 3 + 2 + 2


# --- continuity -----------------------------------------------------------------------------

def test_a_card_drawn_again_remembers_the_last_time():
    seasons = [NS(year=2012, event="endorsement", choice="Do the shoot"),
               NS(year=2014, event="form", choice="Let the cricket talk")]
    assert "2012" in C.again_words("endorsement", seasons)
    assert "Do the shoot" in C.again_words("endorsement", seasons)
    assert C.again_words("workload", seasons) == ""
    assert C.again_words("quiet", [NS(year=2012, event="quiet", choice="")]) == ""


def test_a_coach_offer_made_again_says_when_and_what_you_said():
    card = {"key": "trial_bowl", "text": "The coach has an idea."}
    seasons = [NS(coach={"key": "trial_bowl", "take": False}, year=2013)]
    again = C.coach_again(card, seasons)
    assert again["text"].endswith("The coach asked before, in 2013, and you said no.")
    assert C.coach_again(card, [NS(coach={"key": "phase", "take": True}, year=2013)]) == card


def test_an_incident_that_happened_before_says_so():
    prompt = {"key": "press", "text": "The papers have gone for you."}
    seasons = [NS(incident={"key": "press", "choice": "Hit back"}, year=2015)]
    again = C.incident_again(prompt, seasons)
    assert "2015" in again["text"] and "Hit back" in again["text"]
    assert C.incident_again(prompt, []) == prompt


# --- one real career ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def deck():
    from tools.snapshot_deck import deck_from, read_document
    return deck_from(read_document())


@pytest.fixture(scope="module")
def model():
    from tools.snapshot_deck import model_from, read_document
    return model_from(read_document())


@pytest.fixture(scope="module")
def real(deck, model):
    from game import career_focus
    was, career_focus.FOCUS_ON = career_focus.FOCUS_ON, True    # built before the per-test switches
    try:
        car, moves = C.auto_career(deck, model, HONOURS_SEED,
                                   C.Setup("Arjun Rao", "pace", 2010, False), "strength")
    finally:
        career_focus.FOCUS_ON = was
    return car


HONOURS_SEED = 1


def test_every_season_of_a_real_career_keeps_its_feats_matching_its_record(real):
    for r in real.seasons:
        f = r.feats
        assert f is not None
        assert sum(c[1] for c in f["opp"].values()) == r.runs
        assert sum(c[2] for c in f["opp"].values()) == r.wickets
        assert f["played"] == r.matches


def test_the_nickname_is_a_milestone_in_the_season_it_was_coined_and_only_then(real):
    h = C.honours_of(real)
    assert h.nickname, "the fixture must earn a nickname"
    got = [(r.year, m.text) for r in real.seasons for m in r.milestones if m.key == "nickname"]
    assert got == [(h.nickname_year, f"The papers call you “{h.nickname}”")]
    facts = [f for r in real.seasons for f in r.press if f.get("k") == "nickname"]
    assert facts == [{"k": "nickname", "y": h.nickname_year, "club": facts[0]["club"],
                      "nick": h.nickname}]


def test_the_honours_reach_the_career_card_and_the_saved_card(real):
    from web import career_results, career_routes
    out = career_routes.honours_out(C.honours_of(real))
    saved = career_results.card(real)["career_honours"]
    assert saved == out.model_dump()
    assert career_routes.honours_out(saved) == out


REPEAT_SEED = 0          # an opener whose career is asked a coach trial, a sponsor and a slump twice


def test_a_real_career_remembers_each_question_it_is_asked_again(deck, model):
    setup, rng, moves = C.Setup("X", "opener", 2010), random.Random(REPEAT_SEED), []
    asked = []
    while True:
        car = C.play_career(deck, model, REPEAT_SEED, setup, moves)
        if car.complete:
            break
        p = car.pending
        if p.kind == "coach":
            asked.append(("coach", p.coach["key"], p.coach["text"], p.year))
        elif p.kind == "event":
            asked.append(("event", p.event.key, p.event_text or "", p.year))
        elif p.kind == "incident":
            asked.append(("incident", p.incident["key"], p.incident["text"], p.year))
        moves.append(C.policy_move(car, "random", rng))
    # A repeat names the year it was last asked; a first asking names no year at all (no card
    # or incident text carries one of its own).
    last, again = {}, {"coach": 0, "event": 0, "incident": 0}
    for kind, key, text, year in asked:
        prev = last.get((kind, key)) if key != "quiet" else None
        years = set(re.findall(r"\b20\d\d\b", text))
        assert years == ({str(prev)} if prev else set()), (kind, key, text)
        again[kind] += prev is not None
        last[(kind, key)] = year
    assert all(again.values()), f"the fixture must repeat each kind of question: {again}"
