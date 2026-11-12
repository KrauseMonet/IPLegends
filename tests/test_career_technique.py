"""[A287] Batting hand, bowling arm and bowling type: chosen at creation, cosmetic only."""
import pytest


from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import career_routes as R


def _setup(**kw):
    base = dict(name="A", role="spin", debut=2012)
    base.update(kw)
    return C.Setup(**base)


@pytest.mark.parametrize("arm,kind,word", [
    ("R", "finger", "Off-spin"), ("R", "wrist", "Leg-spin"),
    ("L", "finger", "Left-arm orthodox"), ("L", "wrist", "Left-arm wrist-spin")])
def test_a_spinner_is_named_by_arm_and_kind(arm, kind, word):
    assert C.technique_words(_setup(bowl_arm=arm, bowl_type=kind), "spin") == ["Right-handed bat", word]


def test_a_pace_bowler_and_a_batter():
    s = _setup(role="pace", bat_hand="L", bowl_arm="L", bowl_type="fastmedium")
    assert C.technique_words(s, "pace") == ["Left-handed bat", "Left-arm fast-medium"]
    # the engine's style decides: a batter who does not bowl shows no bowling at all
    assert C.technique_words(_setup(role="opener", bat_hand="L"), None) == ["Left-handed bat"]
    # an all-rounder chose only his arm; pace or spin comes from his spin
    assert C.technique_words(_setup(role="allrounder", bowl_arm="L"), "spin") == ["Right-handed bat", "Left-arm spin"]
    assert C.technique_words(_setup(role="allrounder"), "pace") == ["Right-handed bat", "Right-arm seam"]


@pytest.mark.parametrize("kw", [dict(bat_hand="X"), dict(bowl_arm=""), dict(bowl_type="fast"),
                                dict(role="opener", bowl_type="wrist"), dict(role="allrounder", bowl_type="finger")])
def test_a_type_must_fit_the_role(kw):
    with pytest.raises(C.CareerError):
        _setup(**kw).check()


def test_the_choices_survive_the_token_and_an_old_token_reads_right_handed():
    s = _setup(bat_hand="L", bowl_arm="L", bowl_type="wrist")
    s.check()
    career = type("Car", (), {"checkpoint": C.Checkpoint()})()
    token = R.make_token(7, s, career, [])
    _, back, _, _ = R.read_token(token)
    assert (back.bat_hand, back.bowl_arm, back.bowl_type) == ("L", "L", "wrist")
    # a token from before A287 carries nine setup fields
    import base64, json
    payload = token.rsplit(".", 1)[0]
    d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    d["setup"] = d["setup"][:9]
    old = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    _, back, _, _ = R.decode_payload(old)
    assert (back.bat_hand, back.bowl_arm, back.bowl_type) == ("R", "R", "")


def test_the_choice_changes_nothing_in_the_engine():
    """The same seed played left-handed with leg-spin and right-handed with off-spin is the
    same career, season for season: nothing the engine plays reads the cosmetic fields."""
    doc = read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    deck, model = deck_from(doc), model_from(doc)
    import random

    def career(setup):
        moves = []
        while True:
            c = C.play_career(deck, model, 11, setup, moves)
            if c.complete or len(c.seasons) >= 3:
                return c
            moves.append(C.policy_move(c, "random", random.Random(len(moves))))

    a = career(_setup(bat_hand="L", bowl_arm="L", bowl_type="wrist"))
    b = career(_setup(bowl_arm="R", bowl_type="finger"))
    assert len(a.seasons) >= 3
    assert a.checkpoint.to_dict() == b.checkpoint.to_dict()
