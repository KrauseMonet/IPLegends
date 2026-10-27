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
