"""Flashback's quiz (A147): every answer checked against the deck independently.

Each check recomputes the right answer from the raw cards rather than trusting anything
the generator carried along, so a builder that picked the wrong card, or asked a question
with two right answers, fails here. Runs on the committed deck snapshot, no database.
"""

from __future__ import annotations

import re

import pytest

from game import quiz
from tools import snapshot_deck

DOC = snapshot_deck.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot committed")

SEEDS = range(1, 201)


@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(DOC)


@pytest.fixture(scope="module")
def quizzes(deck):
    return [quiz.make_quiz(deck, s) for s in SEEDS]


def _cards(deck):
    return [c for cs in deck.cards_by_fs.values() for c in cs]


def _squad(deck, franchise, year):
    squads = [cs for cs in deck.cards_by_fs.values()
              if cs and cs[0].franchise == franchise and cs[0].season_year == year]
    assert len(squads) == 1, (franchise, year)
    return squads[0]


def _all(quizzes, kind):
    out = [q for z in quizzes for q in z.questions if q.kind == kind]
    assert out, f"no {kind} question in {len(SEEDS)} quizzes"
    return out


def test_a_seed_is_the_quiz(deck):
    a, b = quiz.make_quiz(deck, 77), quiz.make_quiz(deck, 77)
    assert a == b
    assert a != quiz.make_quiz(deck, 78)


def test_ten_questions_varied_and_never_the_same_kind_twice_running(quizzes):
    for z in quizzes:
        kinds = [q.kind for q in z.questions]
        assert len(kinds) == quiz.QUIZ_LENGTH
        assert all(a != b for a, b in zip(kinds, kinds[1:])), kinds
        assert len(set(kinds)) == len(quiz.KINDS), "every kind before any repeats"


def test_every_question_has_distinct_options_and_one_answer_among_them(quizzes):
    for z in quizzes:
        for q in z.questions:
            labels = [o.label for o in q.options]
            assert len(set(labels)) == len(labels), (q.prompt, labels)
            assert 0 <= q.answer < len(labels)


def test_top_scorer_and_wicket_taker_are_the_strict_leaders(deck, quizzes):
    for kind, stat in (("top_scorer", "bat_runs"), ("top_wickets", "bowl_wickets")):
        for q in _all(quizzes, kind):
            squad = _squad(deck, q.franchise, q.year)
            values = sorted((getattr(c, stat) or 0 for c in squad), reverse=True)
            assert values[0] > values[1], f"{q.prompt}: a shared lead has two right answers"
            leader = max(squad, key=lambda c: getattr(c, stat) or 0)
            assert q.options[q.answer].label == leader.name, q.prompt
            names = {c.name for c in squad}
            assert all(o.label in names for o in q.options), "every option really played"


def test_which_team_names_the_one_franchise_he_played_for_that_year(deck, quizzes):
    cards = _cards(deck)
    for q in _all(quizzes, "which_team"):
        name, year = re.match(r"Who did (.+) play for in (\d{4})\?", q.prompt).groups()
        teams = {c.franchise for c in cards if c.name == name and c.season_year == int(year)}
        assert teams == {q.options[q.answer].label}, q.prompt
        assert all(o.label not in teams for i, o in enumerate(q.options) if i != q.answer)


def test_odd_one_out_really_did_not_play_there(deck, quizzes):
    cards = _cards(deck)
    for q in _all(quizzes, "odd_one_out"):
        squad = {c.name for c in _squad(deck, q.franchise, q.year)}
        odd = q.options[q.answer].label
        assert odd not in squad, q.prompt
        assert all(o.label in squad for i, o in enumerate(q.options) if i != q.answer)
        # never a man who ever played for this franchise, in any season
        lineage = quiz._lineage(q.franchise)
        assert not any(c.name == odd and quiz._lineage(c.franchise) == lineage for c in cards)


def test_more_runs_names_the_bigger_season(deck, quizzes):
    for q in _all(quizzes, "more_runs"):
        stat = "bat_runs" if "runs" in q.prompt else "bowl_wickets"
        value = []
        for o in q.options:
            name = o.label.rsplit(" (", 1)[0]
            card = next(c for c in _squad(deck, o.franchise, o.year) if c.name == name)
            value.append(getattr(card, stat))
        assert value[q.answer] > value[1 - q.answer], q.prompt


def test_stat_year_offers_only_his_own_real_seasons(deck, quizzes):
    cards = _cards(deck)
    for q in _all(quizzes, "stat_year"):
        what, name, year = re.match(r"How many (runs|wickets) did (.+) (?:make|take) in (\d{4})\?",
                                    q.prompt).groups()
        stat = "bat_runs" if what == "runs" else "bowl_wickets"
        mine = [c for c in cards if c.name == name]
        this = next(c for c in mine if c.season_year == int(year))
        number = lambda label: int(label.split()[0])
        assert number(q.options[q.answer].label) == getattr(this, stat), q.prompt
        totals = {getattr(c, stat) for c in mine}
        assert all(number(o.label) in totals for o in q.options), "every option his own"


def test_season_clues_are_that_seasons_real_leaders(deck, quizzes):
    for q in _all(quizzes, "season"):
        year = int(q.options[q.answer].label)
        squads = [cs for cs in deck.cards_by_fs.values() if cs[0].season_year == year
                  and quiz._lineage(cs[0].franchise) == q.franchise]
        assert len(squads) == 1
        top = max(squads[0], key=lambda c: c.bat_runs or 0)
        assert q.clues[0] == f"Leading run-scorer: {top.name}, {top.bat_runs} runs"
        decoys = {int(o.label) for o in q.options} - {year}
        real = {cs[0].season_year for cs in deck.cards_by_fs.values()
                if quiz._lineage(cs[0].franchise) == q.franchise}
        assert decoys <= real, "a decoy year is a real season of the same franchise"


def test_no_option_carries_a_team_that_would_give_the_answer_away(quizzes):
    """A crest is drawn from an option's franchise. On a season question only the right
    option could carry one, and on a stat question the crests would show which seasons
    were at which team -- either way a crest would answer the question."""
    for z in quizzes:
        for q in z.questions:
            if q.kind in ("season", "stat_year"):
                assert all(o.franchise is None for o in q.options), q.prompt
            if q.kind == "season":
                assert q.year is None and q.year_hidden
