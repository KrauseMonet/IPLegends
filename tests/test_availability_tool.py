"""tools.availability (A216): the arithmetic that turns regular seasons into the tier tables,
on hand-built rows -- the query itself needs the archive and is run by hand."""

from __future__ import annotations

from tools import availability as A

TIERS = (88, 82, 0)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        assert params == {"min_seasons": A.MIN_SEASONS, "bat": A.MIN_BAT_BALLS,
                          "bowl": A.MIN_BOWL_BALLS}
        return self

    def fetchall(self):
        return self.rows


TEAM = [f"m{i}" for i in range(14)]


def row(rating, overseas, missed=()):
    return ("p", 1, [m for i, m in enumerate(TEAM) if i not in missed], TEAM, overseas, rating)


def test_quantiles_are_nearest_rank_at_twenty_steps():
    q = A.quantiles([i / 100 for i in range(101)])
    assert len(q) == 20 and q[0] == 0.05 and q[9] == 0.5 and q[-1] == 1.0
    assert A.quantiles([0.5, 1.0]) == (0.5,) * 10 + (1.0,) * 10


def test_seasons_split_by_tier_and_passport_and_unknowns_stay_out():
    rows = [row(90, False)] * 3 + [row(84, True, (0, 1))] * 2 + [row(70, False, (5,))] \
        + [row(None, False, (13,))] + [row(95, None)]
    pooled, cells, by_tier, ends, unknown = A.measure(FakeConn(rows), TIERS)
    assert len(pooled) == 8 and unknown == 1
    assert len(cells[(88, False)]) == 3 and len(cells[(82, True)]) == 2
    assert cells[(82, True)] == [12 / 14] * 2
    assert len(cells[(0, False)]) == 2                  # 70, and a season with no rating
    # missing the first match or the last touches an end; missing match 5 does not
    assert ends == {True: [2, 2], False: [1, 2]}


def test_a_thin_cell_takes_its_tier_pooled_across_passports():
    cells = {(88, False): [1.0] * A.MIN_CELL, (88, True): [0.5]}
    by_tier = {88: [1.0] * A.MIN_CELL + [0.5], 82: [0.9], 0: [0.8]}
    out, notes = A.tables(cells, by_tier, TIERS)
    assert out[(88, False)] == (1.0,) * 20
    assert out[(88, True)] == A.quantiles(by_tier[88])
    assert any(n.startswith("(88, True)") for n in notes)
