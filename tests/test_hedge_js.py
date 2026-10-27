"""[A231] The page's resend on a fresh connection, run under node against a scripted network.

Vercel's Mumbai edge freezes a kept-open connection for 15+ seconds every minute or two;
a request on a NEW connection mostly gets through. So a room request with no answer after
HEDGE_AFTER_MS is sent again through the second hostname and the first answer wins.
These tests drive `apiOnce`/`apiHedged` from `common.js` itself with a fake `fetch` whose
answers are scripted per hostname, so each rule is checked on the code that ships. The
repo has no JavaScript harness (see test_bingo_js.py), so this is skipped without node.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

STATIC = pathlib.Path(__file__).resolve().parent.parent / "web" / "static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HEDGE_MS = 60          # the real 2,000 ms, shortened so a scenario takes milliseconds
SECOND = "https://iplegends.vercel.app"

HARNESS = r"""
const SCEN = __SCEN__;
const plans = SCEN.plans;
const log = [];
global.location = {host: SCEN.host || 'fineleg.app'};
global.window = ('override' in SCEN) ? {HEDGE_SECOND_ORIGIN: SCEN.override} : {};
const API_HEADERS = {};
const API_TIMEOUT_MS = 15000;
const notes = [];
function noteFailure(kind, method, path, status, started, extra){ notes.push({kind, status, ...(extra || {})}); }
let successes = 0;
function noteSuccess(){ successes++; }
global.fetch = (url, init) => {
  const host = url.startsWith(SCEN.second) ? 'second' : 'first';
  const b = (plans[host] || []).shift() || {hang: true};
  const entry = {host, url};
  log.push(entry);
  return new Promise((res, rej) => {
    init.signal.addEventListener('abort', () => {
      entry.aborted = true;
      const e = new Error('aborted'); e.name = 'AbortError'; rej(e);
    });
    if (b.hang) return;
    setTimeout(() => {
      if (init.signal.aborted) return;
      if (b.error) return rej(new TypeError('Failed to fetch'));
      const status = b.status || 200;
      res({status, ok: status < 400, statusText: 'x', json: async () => (b.body || {})});
    }, b.delay || 0);
  });
};
__SRC__
(async () => {
  const results = [];
  for (const call of SCEN.calls){
    const t0 = Date.now();
    try {
      const body = await apiOnce('/api/rooms/AB12CD', call);
      results.push({ok: true, body, ms: Date.now() - t0});
    } catch(e){
      results.push({ok: false, status: e.status || null, timeout: !!e.timeout,
                    network: !!e.network, body: e.body || null, ms: Date.now() - t0});
    }
  }
  await new Promise(r => setTimeout(r, 30));
  console.log(JSON.stringify({results, log, notes, successes,
                              preferSecond: HEDGE_PREFER_SECOND, secondFails: HEDGE_SECOND_FAILS,
                              second: hedgeSecondOrigin()}));
})();
"""


def source() -> str:
    src = (STATIC / "common.js").read_text()
    block = re.search(r"function timeoutError\(\)[\s\S]*?// --- end hedged requests", src).group(0)
    shortened, n = re.subn(r"const HEDGE_AFTER_MS = \d+;", f"const HEDGE_AFTER_MS = {HEDGE_MS};", block)
    assert n == 1, "HEDGE_AFTER_MS not found where expected"
    return shortened


def run(calls, plans, **extra):
    scen = {"calls": calls, "plans": plans, "second": SECOND, **extra}
    script = HARNESS.replace("__SCEN__", json.dumps(scen)).replace("__SRC__", source())
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


HEDGED = {"hedge": True}


def test_a_quick_answer_is_never_sent_twice():
    o = run([HEDGED], {"first": [{"delay": 5, "body": {"v": 1}}]})
    assert o["results"][0] == {"ok": True, "body": {"v": 1}, "ms": o["results"][0]["ms"]}
    assert [e["host"] for e in o["log"]] == ["first"]
    assert not [n for n in o["notes"] if n["kind"] == "hedge"]


def test_a_stuck_request_is_answered_through_the_second_hostname():
    o = run([HEDGED], {"first": [{"hang": True}], "second": [{"delay": 5, "body": {"v": "s"}}]})
    assert o["results"][0]["ok"] and o["results"][0]["body"] == {"v": "s"}
    first, second = o["log"]
    assert first["host"] == "first" and first.get("aborted")      # the frozen copy is dropped
    assert second["url"] == SECOND + "/api/rooms/AB12CD"
    assert o["results"][0]["ms"] >= HEDGE_MS                       # not before it was due
    assert {"kind": "hedge", "status": None, "won": "second"} in o["notes"]


def test_after_the_second_hostname_wins_the_next_request_starts_there():
    o = run([HEDGED, HEDGED],
            {"first": [{"hang": True}, {"delay": 5, "body": {"v": "f"}}],
             "second": [{"delay": 5, "body": {"v": "s1"}}, {"delay": 5, "body": {"v": "s2"}}]})
    assert o["preferSecond"] is True
    assert o["log"][2]["host"] == "second"                          # call two asks it first
    assert o["results"][1]["body"] == {"v": "s2"}


def test_when_the_first_answers_after_all_it_wins_and_nothing_moves():
    o = run([HEDGED], {"first": [{"delay": 150, "body": {"v": "f"}}],
                       "second": [{"delay": 600, "body": {"v": "s"}}]})
    assert o["results"][0]["body"] == {"v": "f"}
    assert o["log"][1]["host"] == "second" and o["log"][1].get("aborted")
    assert o["preferSecond"] is False
    assert {"kind": "hedge", "status": None, "won": "first"} in o["notes"]


def test_a_connection_that_fails_fast_is_resent_at_once_not_after_the_wait():
    o = run([HEDGED], {"first": [{"delay": 2, "error": True}],
                       "second": [{"delay": 2, "body": {"v": "s"}}]})
    assert o["results"][0]["ok"] and o["results"][0]["ms"] < HEDGE_MS


def test_a_server_error_is_resent_at_once_too():
    o = run([HEDGED], {"first": [{"delay": 2, "status": 503}],
                       "second": [{"delay": 2, "body": {"v": "s"}}]})
    assert o["results"][0]["ok"] and o["results"][0]["ms"] < HEDGE_MS


def test_a_refusal_from_either_copy_is_the_answer_and_carries_its_room():
    o = run([HEDGED], {"first": [{"hang": True}],
                       "second": [{"delay": 5, "status": 409, "body": {"detail": "outbid",
                                                                    "room": {"version": 9}}}]})
    r = o["results"][0]
    assert (r["ok"], r["status"], r["body"]["room"]) == (False, 409, {"version": 9})


def test_both_stuck_is_a_timeout_and_is_recorded_as_one():
    o = run([dict(HEDGED, timeoutMs=200)], {"first": [{"hang": True}], "second": [{"hang": True}]})
    r = o["results"][0]
    assert r["timeout"] and not r["ok"]
    assert [n["kind"] for n in o["notes"]].count("timeout") == 1
    assert {"kind": "hedge", "status": None, "won": "none"} in o["notes"]
    assert all(e.get("aborted") for e in o["log"])


def test_an_unreachable_second_hostname_is_given_up_after_two_failures():
    calls = [dict(HEDGED, timeoutMs=200)] * 2 + [HEDGED]
    o = run(calls, {"first": [{"hang": True}, {"hang": True}, {"delay": 5, "body": {"v": 3}}],
                    "second": [{"delay": 2, "error": True}, {"delay": 2, "error": True}]})
    assert o["secondFails"] == 2 and o["second"] is None
    assert o["results"][2]["body"] == {"v": 3}
    assert [e["host"] for e in o["log"][4:]] == ["first"]          # call three: one copy only


def test_any_answer_from_the_second_hostname_resets_its_failure_count():
    calls = [dict(HEDGED, timeoutMs=200), HEDGED, dict(HEDGED, timeoutMs=200)]
    o = run(calls, {"first": [{"hang": True}, {"hang": True}, {"hang": True}],
                    "second": [{"delay": 2, "error": True}, {"delay": 2, "body": {"v": 2}},
                               {"delay": 2, "error": True}]})
    assert o["secondFails"] == 1 and o["second"] == SECOND


def test_a_request_that_did_not_opt_in_is_never_sent_twice():
    o = run([{"timeoutMs": 200}], {"first": [{"hang": True}], "second": [{"delay": 2}]})
    assert [e["host"] for e in o["log"]] == ["first"]
    assert o["results"][0]["timeout"]


def test_there_is_no_second_hostname_off_the_real_site():
    o = run([dict(HEDGED, timeoutMs=200)], {"first": [{"hang": True}]}, host="localhost:8765")
    assert [e["host"] for e in o["log"]] == ["first"]


# --- what the room page opts in ------------------------------------------------------------

def test_the_room_page_resends_polls_and_only_the_moves_it_already_retries():
    """A duplicate is safe only for a move that names what it is about (A146); those are
    exactly the moves the page already retries, so the resend follows the same flag. A
    fill, a twelve, a retention or a Right to Match answer is never sent twice."""
    src = (STATIC / "room.js").read_text()
    post = re.search(r"async function roomPost[\s\S]*?\n}", src).group(0)
    assert re.search(r"hedge:\s*!!retry", post)
    assert re.search(r"/api/rooms/'\s*\+\s*ROOM_CODE[^;]*hedge:\s*true", src)       # the poll
    assert re.search(r"/match\?player_id=[^;]*hedge:\s*true", src)                  # the match poll
    retryable = re.search(r"AUCTION_RETRYABLE = new Set\(\[([^\]]*)\]\)", src).group(1)
    assert sorted(re.findall(r"'(\w+)'", retryable)) == sorted(
        ["bid", "limit", "pass", "offer", "answer", "withdraw", "ready"])
    for unsafe in ("fill", "twelve", "retain", "rtm"):
        assert f"'{unsafe}'" not in retryable
