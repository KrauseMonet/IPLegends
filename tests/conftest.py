"""Shared fixtures."""

import pytest


@pytest.fixture(scope="module")
def no_incidents():
    """Build a Career with no mid-season incidents (A230), for tests of rules written before
    them -- key moments, make-or-breaks, contracts -- whose fixtures are real seasons found by
    search, and which an incident would move. The incidents are tested in
    `test_career_story.py`. The season cache is cleared on the way in and out, since it does
    not know whether incidents were on."""
    from game import career as C
    from game import career_incidents as I

    with pytest.MonkeyPatch.context() as mp:
        C._SEASON_CACHE.clear()
        mp.setattr(I, "candidate", lambda *a, **kw: (None, 0))
        mp.setattr(I, "SLUMP_CHANCE", 0.0)
        yield
    C._SEASON_CACHE.clear()


@pytest.fixture(autouse=True)
def career_seeds_open(monkeypatch):
    """Choosing a career's seed is for admins (A235), but most career tests start from a fixed
    seed to get a repeatable career, signed out. They are not testing who may choose one, so
    the gate is opened for them; `test_career_seed.py` restores the real gate and tests it."""
    from web import career_routes as R

    monkeypatch.setattr(R, "seed_allowed", lambda request: True)
