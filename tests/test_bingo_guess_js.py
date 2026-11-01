"""Bingo's guess handler, run under node with the page's globals stubbed (A242).

A wrong guess read a player list (`BG.players`) that A183 had moved into `puzzle.js` and that was
never filled again, so every wrong guess threw. When the wrong guess was the ninth, the throw
skipped `bgFinish`: the grid looked finished but was never saved, ranked or added to the streak.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

JS = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "bingo.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

STUBS = """
const calls = {finish: [], slip: []};
const el = () => ({classList: {add(){}, toggle(){}}, value: 'x', innerHTML: 'x', focus(){}});
const $ = () => el();
const document = {querySelector: () => el()};
const PZ_JSON = {};
let PZ_PLAYERS = __PLAYERS__;
let BG = {grid: {seed: 1}, guesses: [], state: null, sel: 5, busy: false};
const api = async () => (__RESPONSE__);
const bgPersist = () => {}, bgRender = () => {};
const bgFinish = fresh => calls.finish.push(fresh);
const slip = m => calls.slip.push(m);
"""


def run_guess(correct: bool, finished: bool, players):
    src = JS.read_text()
    body = re.search(r"async function bgGuess[\s\S]*?\n}\n", src).group(0)
    response = {"correct": correct,
                "state": {"finished": finished, "guesses_left": 0 if finished else 3}}
    stubs = STUBS.replace("__PLAYERS__", json.dumps(players)).replace("__RESPONSE__", json.dumps(response))
    out = subprocess.run(
        ["node", "-e", f"{stubs}\n{body}\nbgGuess('r1').then(() => console.log(JSON.stringify(calls)))"],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_a_wrong_last_guess_still_finishes_the_grid():
    calls = run_guess(correct=False, finished=True, players=[{"id": "r1", "name": "R Dhawan"}])
    assert calls["finish"] == [True]
    assert calls["slip"] == ["R Dhawan doesn't fit. 0 guesses left."]


def test_a_wrong_guess_names_the_player():
    calls = run_guess(correct=False, finished=False, players=[{"id": "r1", "name": "R Dhawan"}])
    assert calls["finish"] == []
    assert calls["slip"] == ["R Dhawan doesn't fit. 3 guesses left."]


def test_a_wrong_guess_before_the_list_has_loaded_does_not_throw():
    calls = run_guess(correct=False, finished=True, players=None)
    assert calls["finish"] == [True]
    assert calls["slip"] == ["That player doesn't fit. 0 guesses left."]
