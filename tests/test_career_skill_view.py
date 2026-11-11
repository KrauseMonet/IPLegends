"""How a career's skills are shown: slots, combinations, new skills (A282)."""

import re
from pathlib import Path

from game import career_skills as CS
from game.skills import SKILL_BY_KEY, TIER_BLOCKS


def test_every_combination_is_two_played_skills_of_different_tiers():
    """`shown` keeps one skill a tier, so a same-tier pair could never be held together."""
    assert CS.COMBOS
    for a, b, _name in CS.COMBOS:
        assert a in SKILL_BY_KEY and b in SKILL_BY_KEY
        assert SKILL_BY_KEY[a].tier != SKILL_BY_KEY[b].tier
        assert a not in CS.NOT_PLAYED and b not in CS.NOT_PLAYED
    names = [n for *_, n in CS.COMBOS]
    assert len(names) == len(set(names))


def test_combos_need_both_halves():
    assert CS.combos(["aggressor", "death_hitter"]) == ["Finisher"]
    assert CS.combos(["aggressor"]) == []
    assert CS.combos(["anchor", "run_machine", "boundary_hitter"]) == ["Rock of the order"]


def test_the_hint_names_the_missing_half_and_only_one_the_role_can_show():
    assert CS.combo_hint(["anchor"], "batter") == "Add Run machine to Anchor for “Rock of the order”"
    assert CS.combo_hint(["anchor", "run_machine"], "batter") == ""   # nothing missing for anchor
    # a batter cannot show bowling skills, so a hint never asks him for one
    assert CS.combo_hint(["hard_to_hit"], "batter") == ""
    assert CS.combo_hint(["hard_to_hit"], "bowler") == "Add Miser to Hard to hit for “Impossible to get away”"
    assert CS.combo_hint([], "batter") == ""


def test_slots_open_at_the_tier_blocks_and_hold_the_last_seasons_skills():
    assert TIER_BLOCKS == {1: 80, 2: 87, 3: 94}
    s = CS.slots([["anchor", 1, 1.2], ["run_machine", 2, 0.9]], 87)
    assert [(x["tier"], x["open"], x["key"]) for x in s] == [
        (1, True, "anchor"), (2, True, "run_machine"), (3, False, None)]
    assert [x["open"] for x in CS.slots([], 86)] == [True, False, False]
    assert [x["open"] for x in CS.slots([], 79)] == [False, False, False]
    assert [x["open"] for x in CS.slots([], 94)] == [True, True, True]


def test_a_skill_is_new_only_the_first_season_it_is_shown():
    from web.career_routes import SkillOut, _mark_new

    class S:  # just enough of a SeasonOut
        def __init__(self, keys):
            self.skills = [SkillOut(key=k, label=k, tier=1, blurb="") for k in keys]

    seasons = _mark_new([S(["anchor"]), S(["anchor", "run_machine"]), S([]), S(["run_machine"])])
    assert [[k.new for k in s.skills] for s in seasons] == [[True], [False, True], [], [False]]


def test_the_page_draws_the_slots_and_the_season_combinations():
    js = (Path(__file__).resolve().parent.parent / "web/static/career.js").read_text()
    assert re.search(r"\$\{crSlots\(d\)\}", js), "the player card draws the slots"
    assert "k => k.new).map" in js and "crSeasonSkills(r)" in js and "r.combos" in js and "k.new" in js
    for field in ("skill_slots", "combo_hint", "opens_at", "combos_now"):
        assert field in js


def test_no_career_skill_description_gives_your_player_a_pronoun():
    from web.career_routes import _skills_out
    for k in SKILL_BY_KEY:
        text = _skills_out([[k, SKILL_BY_KEY[k].tier, 1]])[0].blurb
        assert not re.search(r"\b(he|his|him|she|her)\b", text, re.I), (k, text)
