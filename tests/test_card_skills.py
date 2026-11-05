"""[A265] Skills on cards: every card the site sends carries the skills its season earned
(A258), as many as its rating or the player's career standing opens, and the page shows
them only where it shows the rating."""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

import game.skills as K
from tools.snapshot_deck import deck_from, read_document
from web import app as W

COMMON = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "common.js"


@pytest.fixture(scope="module")
def cards():
    deck = deck_from(read_document())
    saved = dict(W.STATE)
    W.STATE["deck"] = deck
    yield [c for cs in deck.cards_by_fs.values() for c in cs]
    W.STATE.clear()
    W.STATE.update(saved)


def find(cards, name, year):
    return next(c for c in cards if c.name == name and c.season_year == year)


def test_a_card_carries_the_skills_its_season_earned(cards):
    c = find(cards, "Sunil Narine", 2012)
    got = [(s.key, s.tier) for s in W._card(c).skills]
    want = [(s.key, s.tier) for s in K.card_skills(
        c.person_id, c.season_year, c.role, c.display, K.standing(cards)[c.person_id])]
    assert got == want and len(got) >= 2


def test_career_standing_opens_a_tier_the_season_card_alone_does_not(cards):
    """A card rated under a block still shows a skill there when the player's career
    standing (his second-best season) reaches it -- the ratified rule (A258)."""
    blocks = K.blocks()
    stand = K.standing(cards)
    lifted = [c for c in cards if c.display is not None and c.display < blocks[1]
              and W._card(c).skills]
    assert lifted, "no card below the first block shows a skill"
    assert all(stand[c.person_id] >= blocks[min(s.tier for s in W._card(c).skills)]
               for c in lifted)


def test_most_cards_show_nothing(cards):
    shown = sum(bool(W._card(c).skills) for c in cards)
    assert 500 < shown < len(cards) / 3


def test_the_journey_card_carries_them_too(cards):
    from game.season import JourneyAccumulator
    c = find(cards, "Sunil Narine", 2012)
    entry = W._journey_entry(c, JourneyAccumulator())
    assert [s.key for s in entry.skills] == [s.key for s in W._card(c).skills]


def test_a_card_built_with_no_deck_loaded_still_renders(monkeypatch):
    """Tests and tools build cards without the app's lifespan: no deck, no standing yet.
    That must give a card judged on its own rating, never a KeyError (A265 found it)."""
    from etl.feasibility import Card
    monkeypatch.setattr(W, "STATE", {})
    out = W._card(Card(fs_id=1, person_id="nobody", name="X", display=90, role="batter"))
    assert out.skills == []


def test_health_says_whether_the_skills_reached_the_deployment():
    assert W.health()["skills"] is True


# --------------------------------------------------------------------------- the page

def run_chips(mode, force, card):
    src = COMMON.read_text()
    fns = "".join(re.search(rf"function {n}\([\s\S]*?\n}}\n", src).group(0)
                  for n in ("esc", "skillChips"))
    tier = re.search(r"const SKILL_TIER = [^\n]*\n", src).group(0)
    mode_fn = "" if mode is None else f"function effectiveDraftMode(){{ return {json.dumps(mode)}; }}\n"
    script = (tier + mode_fn + fns +
              f"process.stdout.write(skillChips({json.dumps(card)}, {json.dumps(force)}));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return out.stdout


CARD = {"skills": [{"key": "anchor", "label": "Anchor <i>", "tier": 1, "disc": "bat",
                    "blurb": "Gets out <rarely>"}]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_page_shows_skills_only_where_it_shows_the_rating():
    assert run_chips("memory", False, CARD) == ""          # memory mode hides ratings
    shown = run_chips("stat", False, CARD)
    assert 'class="sk t1"' in shown and "Anchor &lt;i&gt;" in shown and "<i>" not in shown
    assert "&lt;rarely&gt;" in shown and "<rarely>" not in shown     # both escaped
    assert run_chips("memory", True, CARD) == shown          # a reveal shows them
    assert run_chips(None, True, CARD) == shown              # the auction page has no mode
    assert run_chips("stat", False, {"skills": []}) == ""
