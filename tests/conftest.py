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
        mp.setattr(C, "COVER_CHANCE", 0.0)   # a teammate's breakdown (A247) is one too
        yield
    C._SEASON_CACHE.clear()


@pytest.fixture(autouse=True)
def coach_off(request, monkeypatch):
    """The coach's offers (A238) are a new decision in a season, so a hand-written move list
    from before them would meet a question it does not answer. They are off for every test
    except a module that sets `COACH_TESTS = True` (`test_career_coach.py`), which tests them.
    The season cache needs no clearing: a changed role is part of its key."""
    if getattr(request.module, "COACH_TESTS", False):
        return
    from game import career as C

    monkeypatch.setattr(C, "COACH_ON", False)


@pytest.fixture(autouse=True)
def career_seeds_open(monkeypatch):
    """Choosing a career's seed is for admins (A235), but most career tests start from a fixed
    seed to get a repeatable career, signed out. They are not testing who may choose one, so
    the gate is opened for them; `test_career_seed.py` restores the real gate and tests it."""
    from web import career_routes as R

    monkeypatch.setattr(R, "seed_allowed", lambda request: True)


@pytest.fixture(autouse=True)
def phase_off(request, monkeypatch):
    """Phase specialisation (A248) gives every career player a lean from his templates, which
    moves every seeded season the older career tests pin. It is off for every test except a
    module that sets `PHASE_TESTS = True` (`test_career_phase.py`). The season cache needs no
    clearing: the lean is part of its key."""
    if getattr(request.module, "PHASE_TESTS", False):
        return
    from game import career as C

    monkeypatch.setattr(C, "PHASE_ON", False)


@pytest.fixture(autouse=True)
def keeper_off(request, monkeypatch):
    """The coach's offer to take the gloves (A255) is a new question in a batter's season, so
    the coach tests' seeded careers from before it would meet it. Off for every test except a
    module that sets `KEEPER_TESTS = True` (`test_career_bands.py`)."""
    if getattr(request.module, "KEEPER_TESTS", False):
        return
    from game import career as C

    monkeypatch.setattr(C, "KEEPER_ASK_CHANCE", 0.0)


@pytest.fixture(autouse=True)
def leans_off(request, monkeypatch):
    """The templates' own leans on your career player (A277) move every seeded season the older
    career tests pin. Off for every test except a module that sets `LEANS_TESTS = True`
    (`test_career_leans.py`). The season cache needs no clearing: the leans are in its key."""
    if getattr(request.module, "LEANS_TESTS", False):
        return
    from game import career as C

    monkeypatch.setattr(C, "LEANS_ON", False)


@pytest.fixture(autouse=True)
def drills_off(request, monkeypatch):
    """A mentor passing on his real skill (A281) is an event card's outcome, not the coach's,
    so it would move the older seeded careers even with the coach off. Off for every test
    except a module that sets `DRILL_TESTS = True` (`test_career_drills.py`)."""
    if getattr(request.module, "DRILL_TESTS", False):
        return
    from game import career as C

    monkeypatch.setattr(C, "DRILLS_ON", False)


@pytest.fixture(autouse=True, scope="session")
def focus_off_everywhere():
    """[A285] The pre-season focus asks a question every season, which the older seeded
    careers' move lists never answer. Off for the whole session, so a MODULE-scoped fixture
    that builds a career sees it off too (A251's trap: those run before any per-test switch).
    A module that tests it sets `FOCUS_TESTS = True` and turns it on itself, module-wide."""
    from game import career_focus

    was = career_focus.FOCUS_ON
    career_focus.FOCUS_ON = False
    yield
    career_focus.FOCUS_ON = was


@pytest.fixture(autouse=True)
def focus_off(request, monkeypatch):
    """...and per test, so a test that turned it on cannot leak it to the next."""
    from game import career_focus

    monkeypatch.setattr(career_focus, "FOCUS_ON", bool(getattr(request.module, "FOCUS_TESTS", False)))


@pytest.fixture(scope="session", autouse=True)
def cycle_off_everywhere():
    """[A288] The contract cycle replaces the yearly offers ("o0") that every older seeded
    career's move list starts with. Off for the whole session (A251's module-fixture trap);
    a module that tests it sets `CYCLE_TESTS = True` and turns it on itself."""
    from game import career

    was = career.CYCLE_ON
    career.CYCLE_ON = False
    yield
    career.CYCLE_ON = was


@pytest.fixture(autouse=True)
def cycle_off(request, monkeypatch):
    """...and per test, so a test that turned it on cannot leak it to the next."""
    from game import career

    monkeypatch.setattr(career, "CYCLE_ON", bool(getattr(request.module, "CYCLE_TESTS", False)))
