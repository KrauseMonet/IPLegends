"""Pins `web.app._kind`, the rule deciding which icon a card shows.

Before this test existed, the icon was derived from `has_bat and has_bowl` -- true the
moment BOTH disciplines carry any rating at all. That was a fair test back when a
discipline was only ever rated after clearing A33's volume floor, but A65 removed that
floor everywhere, so a bowler who faced a single tail-end ball now gets a (heavily
shrunk) batting rating too, and the old test tagged him all-rounder for it. Measured
against the live deck: Bumrah, Hazlewood and Cummins seasons with single-digit balls
faced all read "allrounder" under the old rule, and only 284 of the 1,816 cards it
called all-rounder actually clear A26's calibrated per-match-average thresholds.

`_kind` now just returns `card.role`, which already IS that calibrated test (etl/roles.py,
A26) -- there is no second, separate volume rule to maintain here.
"""

from __future__ import annotations

from etl.feasibility import Card
from web.app import _kind


def _card(role: str | None, bat: float | None, bowl: float | None) -> Card:
    return Card(fs_id=1, person_id="p", name="p", role=role, bat=bat, bowl=bowl)


def test_a_bowler_rated_off_a_token_few_balls_faced_is_not_an_allrounder():
    """Bumrah 2014: 3 balls faced, 238 legal balls bowled. `role_for` (A26) calls this
    `bowler` because 3 balls in ~14 matches is far under BAT_MIN; the card must agree."""
    assert _kind(_card("bowler", bat=0.23, bowl=0.13)) == "bowler"


def test_a_batter_rated_off_a_handful_of_bowled_balls_is_not_an_allrounder():
    """The mirror case: a specialist batter who turns his arm over for a couple of overs
    a season must not read as a genuine dual threat either."""
    assert _kind(_card("batter", bat=0.4, bowl=-0.1)) == "batter"


def test_a_genuine_dual_threat_still_reads_allrounder():
    """Narine 2024 clears BOTH A26 averages on real volume (488 runs, a rated bowling
    season) -- the case the old rule was protecting is not lost by deferring to `role`."""
    assert _kind(_card("allrounder", bat=0.3, bowl=0.4)) == "allrounder"


def test_a_keeper_is_a_keeper_even_when_rated_in_both_disciplines():
    assert _kind(_card("keeper", bat=0.1, bowl=0.1)) == "keeper"


def test_a_card_with_no_role_at_all_is_unrated_rather_than_guessed():
    """Defensive: `squad_members.role` is NOT NULL (A27) so this should not occur via
    `load_deck`, but `_kind` must not fabricate a role for a `Card` built any other way."""
    assert _kind(_card(None, bat=0.1, bowl=0.1)) == "unrated"
