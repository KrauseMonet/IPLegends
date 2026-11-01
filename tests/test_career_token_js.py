"""[A243] The career page never stores anything that is not a career token.

A career exists only as its signed token in the browser (A199). On 2026-11-01 a response
that arrived cut off read as `{}`: the page saved the token as nothing, every retry went
out without one (four 422s in Vercel's log) and the reload found no career at all. These
tests run the page's own storage functions from `career.js` under node, with a fake
localStorage and a scripted `api`. Skipped without node, like the other JS tests.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

STATIC = pathlib.Path(__file__).resolve().parent.parent / "web" / "static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

GOOD, OLD, NEW = "eyJh.sig1", "eyJo.sig0", "eyJu.sig2"

HARNESS = r"""
const SCEN = __SCEN__;
const mem = {};
global.localStorage = {getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }};
if (SCEN.store) mem['iplegends_career_v1'] = JSON.stringify(SCEN.store);
const calls = [];
async function api(path, opts){
  calls.push({path, body: JSON.parse(opts.body), retry: opts.retry});
  const a = SCEN.answers.shift();
  if (a.error){ const e = new Error(a.error); if (a.status) e.status = a.status; throw e; }
  return a.body;
}
__SRC__
(async () => {
  const out = {};
  try {
    if (SCEN.do === 'post') out.value = await crPost('/api/career/move', {token: GOOD_TOKEN, move: 'o0'});
    else out.value = await crResume();
  } catch(e){ out.error = e.message; }
  out.store = JSON.parse(mem['iplegends_career_v1'] || '{}');
  out.calls = calls;
  console.log(JSON.stringify(out));
})();
"""


def source() -> str:
    src = (STATIC / "career.js").read_text()
    head = re.search(r"const CR_KEY[\s\S]*?\nconst crNum", src).group(0).rsplit("\n", 1)[0]
    resume = re.search(r"async function crResume\(\)[\s\S]*?\n}\n", src).group(0)
    return head + "\n" + resume


def run(**scen):
    script = (HARNESS.replace("__SCEN__", json.dumps(scen)).replace("__SRC__", source())
              .replace("GOOD_TOKEN", json.dumps(GOOD)))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_an_answer_with_no_token_is_refused_and_the_career_is_kept():
    """The incident itself: `{}` came back where a career should have."""
    o = run(do="post", store={"token": GOOD}, answers=[{"body": {}}])
    assert "error" in o
    assert o["store"] == {"token": GOOD}


def test_a_new_token_is_kept_with_the_one_it_replaces():
    o = run(do="post", store={"token": GOOD}, answers=[{"body": {"token": NEW}}])
    assert o["store"] == {"token": NEW, "prev": GOOD}
    assert o["calls"][0]["retry"] is True        # a career move is pure, so it may be resent


def test_a_reload_falls_back_to_the_previous_token():
    o = run(store={"token": NEW, "prev": OLD},
            answers=[{"error": "That career could not be read.", "status": 400},
                     {"body": {"token": OLD}}])
    assert o["value"] == {"token": OLD}
    assert [c["body"]["token"] for c in o["calls"]] == [NEW, OLD]
    assert o["store"]["token"] == OLD


def test_a_failed_connection_on_reload_drops_nothing():
    o = run(store={"token": GOOD, "prev": OLD}, answers=[{"error": "Couldn't reach the server"}])
    assert "error" in o
    assert o["store"] == {"token": GOOD, "prev": OLD}


def test_nothing_that_is_not_a_token_is_ever_sent():
    o = run(store={"token": None, "prev": "undefined"}, answers=[])
    assert o["value"] is None and o["calls"] == []


def test_every_career_request_that_returns_a_career_goes_through_crpost():
    src = (STATIC / "career.js").read_text()
    raw = re.findall(r"api\('/api/career/([a-z]+)'", src)
    assert set(raw) <= {"options", "save"}, raw
    assert "crSave({token: d.token})" not in src
