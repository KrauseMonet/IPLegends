"""A51. The four-overseas cap read as evidence, and the line between suspicion and proof.

`team_sheet_evidence` has no schema and no constraint behind it, so by the standing rule in
CLAUDE.md it is protected by this file alone. It is also the exact shape of thing that
breaks quietly: a wrong version still returns two plausible-looking sets, and the only
symptom is a nationality nobody questions.

The first version of it did break this way. It read the proof inside XIs it had just called
contradicted, which proved Warner and Pollard domestic, and every internal figure stayed
plausible while it did so.
"""

from __future__ import annotations

from etl.derive_people import HOME_NATION, OVERSEAS_PER_XI, team_sheet_evidence


class FakeConn:
    """`real_xis` is one query; the evidence walk is the part worth testing."""

    def __init__(self, xis: list[list[str]]):
        self._xis = xis

    def execute(self, _sql: str):
        return self

    def fetchall(self):
        return [([p for p in xi],) for xi in self._xis]


AUS, SA, WI, SL = "Australia", "South Africa", "West Indies", "Sri Lanka"


def test_four_overseas_team_mates_prove_an_unknown_eleventh_man_domestic():
    """The cap leaves no room, so this settles a blank row without anyone guessing.

    This is how both remaining blanks in `nationality.csv` were resolved: each had played
    in an XI already holding four known overseas players, so no judgement was involved.
    """
    xi = ["o1", "o2", "o3", "o4", "blank"]
    nat = {"o1": AUS, "o2": SA, "o3": WI, "o4": SL}          # 'blank' has no answer yet
    _, proven = team_sheet_evidence(FakeConn([xi]), nat)
    assert "blank" in proven
    assert not (proven & {"o1", "o2", "o3", "o4"}), "the four cannot prove themselves"


def test_the_proof_cannot_reach_the_culprit_it_is_looking_for():
    """The limitation, pinned so nobody reads more into `must_be_domestic` than is there.

    A player wrongly labelled overseas makes his own XI contradicted, and the proof skips
    contradicted XIs -- so it can confirm a blank or a domestic player and can never
    correct a wrong overseas one. That is why S Sohal and the other twelve are OFFERED in
    the CSV for a human rather than resolved in code, and why check 21 reports rather than
    repairs. Widening the proof to reach them is exactly the change that proved Warner
    domestic.
    """
    xi = ["o1", "o2", "o3", "o4", "culprit"]
    nat = {"o1": AUS, "o2": SA, "o3": WI, "o4": SL,
           "culprit": "United States of America"}       # really domestic, emigrated later
    contradicted, proven = team_sheet_evidence(FakeConn([xi]), nat)
    assert "culprit" in contradicted, "he is named as a suspect"
    assert "culprit" not in proven, "but never resolved without a human"


def test_the_proof_is_not_drawn_from_an_xi_that_is_itself_contradicted():
    """The bug that shipped first. Five overseas means one of the five is already wrong, so
    'four others are overseas' is the claim under suspicion -- and reading it anyway proves
    every player in the XI domestic, including the four genuine internationals."""
    xi = ["o1", "o2", "o3", "o4", "o5"]
    nat = {"o1": AUS, "o2": SA, "o3": WI, "o4": SL, "o5": "Australia"}
    contradicted, proven = team_sheet_evidence(FakeConn([xi]), nat)
    assert proven == set(), "nothing may be proven inside a contradicted XI"
    assert set(contradicted) == set(xi), "suspicion names all five, since any could be wrong"


def test_an_associate_label_cannot_feed_the_proof():
    """An associate answer is the kind A51 exists to doubt, so letting one into the four
    would let a wrong answer certify the next one.

    The XI here is legal on the count -- four overseas, so the proof actually runs -- but
    one of the four is an associate label. Three full members and an associate is not four.
    """
    xi = ["o1", "o2", "o3", "assoc", "blank"]
    nat = {"o1": AUS, "o2": SA, "o3": WI, "assoc": "Seychelles"}
    contradicted, proven = team_sheet_evidence(FakeConn([xi]), nat)
    assert contradicted == {}, "four overseas is legal, so the proof path is the one tested"
    assert "blank" not in proven, "three full members and an associate is not four"


def test_a_legal_xi_produces_no_suspicion_at_all():
    """The check has to be able to pass, or it is an alarm rather than a test."""
    xi = ["o1", "o2", "o3", "o4"] + [f"h{i}" for i in range(7)]
    nat = {"o1": AUS, "o2": SA, "o3": WI, "o4": SL}
    nat.update({f"h{i}": HOME_NATION for i in range(7)})
    contradicted, proven = team_sheet_evidence(FakeConn([xi]), nat)
    assert contradicted == {}
    assert proven == {f"h{i}" for i in range(7)}


def test_an_unknown_nationality_is_neither_suspected_nor_counted():
    """A23/A49 at this layer: a gap is check 19's business, and this check is for
    contradictions. An unknown must not be able to make an XI look illegal."""
    xi = ["o1", "o2", "o3", "o4", "unknown"]
    nat = {"o1": AUS, "o2": SA, "o3": WI, "o4": SL}          # 'unknown' absent entirely
    contradicted, proven = team_sheet_evidence(FakeConn([xi]), nat)
    assert contradicted == {}, "an unknown may not push an XI over the cap"
    assert "unknown" in proven, "but the cap still proves him domestic"


def test_the_cap_is_four_and_the_fifth_is_what_trips_it():
    nat = {f"o{i}": AUS for i in range(OVERSEAS_PER_XI)}
    legal, _ = team_sheet_evidence(FakeConn([list(nat)]), nat)
    assert legal == {}
    nat[f"o{OVERSEAS_PER_XI}"] = AUS
    illegal, _ = team_sheet_evidence(FakeConn([list(nat)]), nat)
    assert len(illegal) == OVERSEAS_PER_XI + 1
