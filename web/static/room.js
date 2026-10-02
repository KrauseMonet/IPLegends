// The live room page: lobby, live draft, match reveal, result. Reached only by
// navigation from /rooms (create/join, which saves {code, playerId} to localStorage
// then sends the browser here) or a reload/bookmark of this page's own URL, in which
// case this page's own boot (bottom of file) verifies the saved session against the
// URL's own room code before resuming anything.

/* --- a room's own completed match: renderScorecard/copyRoomCode's data source --- */
let ROOM_MATCH_DATA = null;
// [A149] The room version ROOM_MATCH_DATA was fetched at, and when -- see pollRoomNow.
let ROOM_MATCH_AT = -1, ROOM_MATCH_FETCHED = 0;
const ROOM_MATCH_RECHECK_MS = 10000;

function showRoomScorecard(i){
  renderScorecard(ROOM_MATCH_DATA.results[i].result);
}

function copyRoomCode(){
  navigator.clipboard.writeText(ROOM_CODE)
    .then(() => slip('Copied — share this code with friends.'))
    .catch(() => slip(ROOM_CODE));
}

// The code is what somebody TYPES on /rooms's join tab; the link is what you paste into
// a chat. Both are kept because they serve different flows and neither replaces the
// other.
//
// The URL carries the room code and NOTHING ELSE, which is load-bearing rather than
// tidy. A99 deliberately put {code, playerId} in localStorage instead of the URL
// because `player_id` is this app's only proof of which seat you are -- there are no
// accounts in a room -- so a player_id in a shareable link would hand every recipient
// the ability to act AS that seat: pick their cards, leave, or call their toss. A
// recipient of this link is a stranger to the room and must stay one: /rooms/{code}
// finds no matching localStorage session and bounces them to /rooms?code=..., where
// they enter their own name and `join_room` mints them their own fresh player_id.
//
// Measured against all three states before the wording was written, because the link is
// NOT valid forever and copy that implied otherwise would be a lie: `join_room` refuses
// once `status != 'lobby'` ("this room has already started") and, separately, once the
// seats are taken ("this room is full"). Both were reproduced -- a lobby room with a
// free seat joins and mints a fresh player_id; a drafting room and a full room each
// bounce the recipient to /rooms?code=... with that exact reason shown as a toast, which
// is graceful rather than a 404. Hence "in the lobby with a seat free", covering both
// refusals: "still in the lobby" alone would over-promise on a full room.
function copyRoomLink(){
  const url = `${location.origin}/rooms/${ROOM_CODE}`;
  navigator.clipboard.writeText(url)
    .then(() => slip('Link copied — good while the room is in the lobby with a seat free.'))
    .catch(() => slip(url));
}

let ROOM_CODE = null, MY_PID = null, ROOM = null, ROOM_POLL = null, ROOM_PENDING = null;
// The highest room `version` (migration 030) this client has already applied. Several
// seats poll the same room on their own timers and a response can take longer than the
// 2s interval, so two requests are routinely in flight at once and can land in either
// order -- and every response used to be applied on arrival regardless of when the
// server actually read it. That is what made a pick appear and then vanish: the poll
// issued just before a pick landed could return just after it and roll the UI back onto
// pre-pick state, leaving a seat looking at its own turn with nothing selectable
// (ROOM_PENDING is only ever set by a real click, never restored by a render). Ordering
// by a server-assigned counter is the fix; arrival order is not information.
let ROOM_VERSION_SEEN = -1;
// The per-pick countdown. ROOM_TIMER_BASE is just {startedAt, max} -- when the server
// says this turn began, and how long a turn is. Everything else is derived from
// serverClock() below, so the countdown is a computation rather than a local decrement,
// and re-rendering it can never make it drift.
let ROOM_TIMER_TICK = null, ROOM_TIMER_BASE = null;

// Our estimate of the server's clock. Naively this is just `server_now - Date.now()`
// measured on any response -- but that difference also contains however long the
// response spent in flight, which varies from poll to poll, so re-measuring it on every
// poll makes the estimate jitter by exactly the amount the countdown used to jump by.
// (The original bug was the same quantity in a different place: a "seconds remaining"
// value stamped with its ARRIVAL time. Deriving from the server's clock instead is
// necessary but not sufficient -- measured, a 3s-delayed response still read 3s high.)
//
// So: NTP's two standard moves. Halve the round trip, on the assumption the two legs are
// roughly symmetric, which removes most of the delay rather than all of one leg. And keep
// the sample with the SMALLEST round trip seen rather than the most recent one, because
// the fastest exchange is the least contaminated -- which also means the estimate settles
// after the first few polls and then stops moving, so the displayed clock stops twitching.
let ROOM_CLOCK_OFFSET = 0, ROOM_CLOCK_BEST_RTT = Infinity;

function serverClock(){ return Date.now() / 1000 + ROOM_CLOCK_OFFSET; }

// Every room fetch goes through here rather than `api` directly, so a clock sample is
// taken from whatever response happens to be quickest -- polls and mutations alike.
async function roomApi(path, opts){
  const t0 = Date.now();
  const body = await api(path, opts);
  const t1 = Date.now();
  if (body && typeof body.server_now === 'number'){
    const rtt = (t1 - t0) / 1000;
    if (rtt < ROOM_CLOCK_BEST_RTT){
      ROOM_CLOCK_BEST_RTT = rtt;
      ROOM_CLOCK_OFFSET = body.server_now + rtt / 2 - t1 / 1000;
    }
  }
  return body;
}

// [A146] A live move -- a pick, a bid, a pass. Three things the plain fetch did not do,
// and each was a way a move the player made simply did not happen:
//  * It gives up after ROOM_MOVE_TIMEOUT_MS, not the page-load 15s: a 15-second lot cannot
//    afford a request that hangs for all of it.
//  * With `retry`, a request that never got an answer (a timeout, a dropped connection, a
//    503 while the room was busy) is sent once more. That is only safe because every
//    retried move names what it was about -- a pick its pick count, a bid its lot and
//    price -- so a repeat of a move that DID land is refused or ignored, never doubled.
//    And a refusal of the retry is exactly that case, so it is treated as success.
//  * A 409 refusal now carries the room as it really is, which is applied at once, so the
//    screen shows why ("outbid", "that lot has closed") instead of the stale state the
//    player clicked against sitting there until the next poll.
const ROOM_MOVE_TIMEOUT_MS = 7000;
async function roomPost(path, payload, retry){
  const opts = {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({player_id: MY_PID, ...payload}),
                timeoutMs: ROOM_MOVE_TIMEOUT_MS};
  for (let attempt = 0; ; attempt++){
    try {
      return await roomApi(path, opts);
    } catch(e){
      const room = e.status === 409 && e.body && e.body.room;
      if (room){
        applyRoom(room);
        if (attempt > 0) return room;     // the first attempt landed after all
        e.room = room;
        throw e;
      }
      const transient = e.timeout || e.status === 503 || e.status === undefined;
      if (retry && transient && attempt === 0){
        await new Promise(r => setTimeout(r, 400));
        continue;
      }
      if (transient) pollRoom(true);      // show where the room really is meanwhile
      throw e;
    }
  }
}
// Whose squad the "Batting order" column shows during someone else's turn: false = the
// active player's (the default), true = my own. Resets whenever the active player changes
// so a stale choice doesn't linger into the next person's turn.
let ROOM_VIEW_MINE = false, ROOM_VIEW_LAST_ACTIVE = null;
// Match phase: ROOM_REVEAL_ACTIVE is the current_matches entry we're animating for the
// viewer right now (or null) -- while set, incoming polls are ignored for rendering so
// a live over-by-over stepper is never restarted out from under the viewer; the fresh
// data still lands in ROOM_MATCH_DATA and is read the moment the reveal finishes.
// ROOM_REVEALED holds every playoff stage already animated, so a fixture is never
// re-revealed on a later poll. ROOM_SPECTATE_SHOWN gates the one-time "your run
// is over" choice screen. ROOM_LEAGUE_REVEALED_THROUGH is ROOM_REVEALED's own
// analogue for a league room's round-robin, which needs a COUNT rather than a stage
// name -- every one of its fixtures shares the literal stage "league", not a unique
// label like "Semi-final 1". All of them reset per room in enterRoom.
// Auto-advance. The host used to be a required CLICK on every step -- 74 of them for a
// ten-seat league room, each one blocking all nine other seats on "Waiting for the host
// to continue…". The tournament's result is already fully computed by then (revealing is
// presentation, A82), so the host paces it rather than gates it: the countdown starts as
// soon as this client is no longer mid-reveal and fires the same call the button did.
//
// Armed only on the host's own client, because only the host may make these calls -- and
// that is what keeps the reveal SHARED rather than every seat running off at its own
// pace. A host who closes the tab is covered separately and server-side, by
// `room_match.MATCH_STEP_TIMEOUT_S`, so nothing here is load-bearing against a freeze.
//
// `key` is what the countdown is FOR (a round label, or the league cursor position), so a
// poll landing mid-countdown re-renders without restarting it, and a genuinely new step
// re-arms it.
let ROOM_AUTO = null;          // {key, deadline, paused, fire}
let ROOM_AUTO_TICK = null;
// A knockout result wants a beat to be read; a league fixture is one row of a montage
// with 69 others behind it, so it moves briskly. Skip ahead exists for both.
const ROOM_AUTO_MS = 6000, ROOM_LEAGUE_AUTO_MS = 2000;

function roomDisarmAuto(){
  ROOM_AUTO = null;
  if (ROOM_AUTO_TICK){ clearInterval(ROOM_AUTO_TICK); ROOM_AUTO_TICK = null; }
}

function roomArmAuto(key, ms, fire){
  if (ROOM_AUTO && ROOM_AUTO.key === key) return;   // already counting for this step
  ROOM_AUTO = {key, deadline: Date.now() + ms, paused: false, fire};
  if (!ROOM_AUTO_TICK) ROOM_AUTO_TICK = setInterval(roomAutoTick, 250);
  roomAutoTick();
}

function roomAutoTick(){
  const el = $('#roomAutoLine');
  if (!ROOM_AUTO){ if (el) el.textContent = ''; return; }
  if (ROOM_AUTO.paused){ if (el) el.textContent = 'Paused'; return; }
  const left = Math.max(0, Math.ceil((ROOM_AUTO.deadline - Date.now()) / 1000));
  if (el) el.textContent = left > 0 ? `Continuing in ${left}…` : 'Continuing…';
  if (left > 0) return;
  const fire = ROOM_AUTO.fire;
  roomDisarmAuto();
  fire();
}

function roomAutoPause(btn){
  if (!ROOM_AUTO) return;
  ROOM_AUTO.paused = !ROOM_AUTO.paused;
  // Restart the countdown from full on resume rather than resuming a stale deadline --
  // whoever hit pause wanted to look at something, and handing them one second back is
  // worse than handing them the whole beat again.
  if (!ROOM_AUTO.paused) ROOM_AUTO.deadline = Date.now() + ROOM_AUTO_MS;
  if (btn) btn.textContent = ROOM_AUTO.paused ? 'Resume' : 'Pause';
  roomAutoTick();
}

function roomAutoNow(){
  if (!ROOM_AUTO) return;
  const fire = ROOM_AUTO.fire;
  roomDisarmAuto();
  fire();
}

let ROOM_REVEAL_ACTIVE = null, ROOM_SPECTATE_SHOWN = false;
// [A149] Every playoff stage this viewer has already watched (or was never going to):
// a SET, because everyone now watches every match and a round can hold two at once.
// ROOM_REVEAL_SYNCED marks the first match payload of a visit, which is taken as
// already seen -- see roomSyncSeen.
let ROOM_REVEALED = new Set(), ROOM_REVEAL_SYNCED = false;
let ROOM_LEAGUE_REVEALED_THROUGH = 0;
// Bumped ONLY by a mutation (toss/advance/pick/kick/start) -- never by pollRoom itself.
// A poll captures the CURRENT generation before its request and only applies its result
// if nothing bumped it in the meantime, which is what stops a poll that was already in
// flight from clobbering a mutation's fresher result with stale pre-mutation state.
// Deliberately NOT bumped per poll: polling round-trips can run longer than the 2s
// interval, and if every poll bumped its own counter, an in-flight poll would always
// find a NEWER poll's bump waiting by the time it returns -- discarding every single
// response forever, since nothing ever catches up. Reads never compete with each
// other, only a genuine write needs to invalidate an older read.
let ROOM_GEN = 0;

// The room's OWN mode, not a caller's local preference (see migration 023's own note):
// every mode-gated render inside a room reads THIS, falling back to 'stat' before ROOM
// has loaded at all.
function roomDraftMode(){
  return (ROOM && ROOM.draft_mode) || 'stat';
}
function effectiveDraftMode(){
  return roomDraftMode();
}

// A minimal two-screen version of the old all-page `go()` -- this page only ever shows
// its own #room (lobby/draft/result/failed, toggled internally by renderRoom) or
// #reveal (the match toss/over-stepper), never home/draft/season.
function go(id){
  for (const s of ['room', 'reveal', 'analysis'])
    $('#' + s).classList.toggle('hide', s !== id);
  window.scrollTo(0, 0);
}

const ROOM_STORAGE_KEY = 'iplegends_room';

function saveRoomSession(code, playerId){
  try { localStorage.setItem(ROOM_STORAGE_KEY, JSON.stringify({code, playerId})); }
  catch(e){ /* private browsing, quota, disabled storage -- resuming just won't work */ }
}

function clearRoomSession(){
  try { localStorage.removeItem(ROOM_STORAGE_KEY); } catch(e){}
}

// Every "leave"/"done" action in a room funnels through here: a non-host seat still in
// the LOBBY is freed server-side (fire-and-forget -- the room is being abandoned either
// way), the saved session is cleared so a later reload doesn't try to resurrect it, and
// the browser goes home. Mirrors the single-page app's old go('home') exactly, minus
// the parts that were about switching to a DIFFERENT section of the same page.
function leaveRoomAndGoHome(){
  if (ROOM_CODE && ROOM && ROOM.status === 'lobby' && MY_PID !== ROOM.host_id){
    api(`/api/rooms/${ROOM_CODE}/leave`, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({player_id: MY_PID})}).catch(() => {});
  }
  clearRoomSession();
  location.href = '/';
}

// The shared nav's logo goes home; from inside a room "home" has to mean leaving it, or
// the saved session sends the visitor straight back here (index.html resumes it).
document.addEventListener('DOMContentLoaded', () => {
  const home = document.querySelector('.topnav [data-home]');
  if (home) home.addEventListener('click', e => { e.preventDefault(); leaveRoomAndGoHome(); });
});

function enterRoom(code, playerId){
  saveRoomSession(code, playerId);
  ROOM_CODE = code; MY_PID = playerId; ROOM_PENDING = null;
  ROOM_REVEAL_ACTIVE = null; ROOM_REVEALED = new Set(); ROOM_REVEAL_SYNCED = false;
  ROOM_SPECTATE_SHOWN = false;
  ROOM_LEAGUE_REVEALED_THROUGH = 0;
  // Reset per ROOM, not per session: versions are counted per room row, so a version
  // carried over from a room we just left would silently reject the new room's early
  // states until it happened to climb past it.
  ROOM_VERSION_SEEN = -1;
  ROOM_MATCH_AT = -1; ROOM_MATCH_FETCHED = 0;
  ROOM_POLL_FAILS = 0;
  ROOM_POLL_INFLIGHT = false; ROOM_POLL_STARTED = 0;
  ROOM_TIMER_BASE = null;
  // Re-measured per room rather than kept for the tab's lifetime: the best-RTT sample is
  // sticky by design, and a lucky sample from an earlier session is not evidence about
  // this one (the device may have changed network entirely between the two).
  ROOM_CLOCK_OFFSET = 0; ROOM_CLOCK_BEST_RTT = Infinity;
  go('room');
  pollRoom(true);
  if (ROOM_POLL) clearInterval(ROOM_POLL);
  ROOM_POLL = setInterval(pollRoom, ROOM_POLL_TICK_MS);
  if (ROOM_TIMER_TICK) clearInterval(ROOM_TIMER_TICK);
  ROOM_TIMER_TICK = setInterval(tickRoomTimer, 1000);
  watchRoomVisibility();
}

// Browsers throttle setInterval hard in a background tab (to roughly once a minute) and
// suspend it outright when a phone locks or the user switches apps -- so a seat that
// tabs away stops polling entirely, sees neither its own turn arriving nor the clock
// running out, and comes back to a screen that has been frozen for minutes and a pick
// that was auto-made for it. Nothing here used to notice that at all. Polling therefore
// stops on hide (it was not working anyway, and a throttled interval firing on a stale
// tab is just noise) and, on return, fetches IMMEDIATELY rather than waiting up to a
// further 2s to catch up.
let ROOM_VISIBILITY_BOUND = false;
function watchRoomVisibility(){
  if (ROOM_VISIBILITY_BOUND) return;
  ROOM_VISIBILITY_BOUND = true;
  document.addEventListener('visibilitychange', () => {
    if (!ROOM_CODE) return;
    if (document.hidden){
      if (ROOM_POLL){ clearInterval(ROOM_POLL); ROOM_POLL = null; }
    } else if (!ROOM_POLL && (!ROOM || ROOM.status !== 'failed')){
      // Not on a FAILED room: `renderRoomFailed` stops polling deliberately, and there
      // is nothing further to learn about a room that has stranded. Every other status
      // resumes, 'complete' included -- the match phase polls on this same interval.
      pollRoom(true);
      ROOM_POLL = setInterval(pollRoom, ROOM_POLL_TICK_MS);
      // The countdown is derived from the server's own clock rather than counted down
      // locally, so it needs no catch-up of its own here -- re-rendering it is enough,
      // and it will already show the correct (probably expired) value.
      tickRoomTimer();
    }
  });
  // A phone waking or a network coming back does not always fire visibilitychange, and
  // a poll that fires while offline fails silently and waits a full interval to retry.
  window.addEventListener('online', () => { if (ROOM_CODE) pollRoom(true); });
}

// Apply a room payload only if it is NEWER than whatever we last applied. Returns
// whether it was applied, so a caller that fetched something further off the back of it
// (the match payload) can drop that too rather than pairing fresh data with stale.
//
// Every path that receives a room object goes through here -- polls AND mutation
// responses alike. A mutation is not automatically fresher than a poll already applied:
// its own response was built before any poll issued after it, so ordering both by the
// same server-assigned counter is what makes the two safe to interleave at all.
function applyRoom(room){
  if (room.version <= ROOM_VERSION_SEEN) return false;
  ROOM_VERSION_SEEN = room.version;
  ROOM = room;
  if (room.status === 'drafting'){
    // Only what the server actually asserts -- when the turn began, and how long a turn
    // is. No arrival time is recorded, because the countdown must not depend on one.
    ROOM_TIMER_BASE = {
      startedAt: room.turn_started_at,
      max: room.timer_seconds || 30,
    };
  } else {
    ROOM_TIMER_BASE = null;
  }
  return true;
}

// [A146] Polling ticks every second but only fetches every second while a clock is live
// (a draft turn, a lot on the floor) -- everywhere else every two, as before. A second
// matters there: an opponent's bid seen two seconds late was a bid answered against a
// stale price, and a turn noticed two seconds late was two seconds off a 15-second clock.
//
// And never more than one poll in flight. A poll used to fire every interval whether or
// not the last had answered, so a slow moment -- a cold server, a phone on a weak signal
// -- stacked requests on top of each other, each one making the next slower: exactly the
// moment a room felt stuck. A poll that has had no answer for ROOM_POLL_STUCK_MS is given
// up on, so one lost request can never stop polling altogether.
const ROOM_POLL_TICK_MS = 1000, ROOM_POLL_IDLE_MS = 2000, ROOM_POLL_STUCK_MS = 8000;
let ROOM_POLL_STARTED = 0, ROOM_POLL_INFLIGHT = false;
function roomClockLive(){
  return !!ROOM && (ROOM.status === 'drafting' || ROOM.status === 'auctioning');
}

// `now` skips the idle-phase spacing (not the in-flight guard): a tab coming back, the
// network returning, a move that failed -- each wants the room immediately.
async function pollRoom(now){
  if (!ROOM_CODE) return;
  const since = Date.now() - ROOM_POLL_STARTED;
  if (ROOM_POLL_INFLIGHT && since < ROOM_POLL_STUCK_MS) return;
  if (now !== true && !roomClockLive() && since < ROOM_POLL_IDLE_MS - 100) return;
  ROOM_POLL_INFLIGHT = true;
  ROOM_POLL_STARTED = Date.now();
  const started = ROOM_POLL_STARTED;
  try { await pollRoomNow(); }
  finally { if (ROOM_POLL_STARTED === started) ROOM_POLL_INFLIGHT = false; }
}

async function pollRoomNow(){
  const myGen = ROOM_GEN;   // a mutation started after this poll was issued supersedes it
  try {
    // player_id identifies the caller so the server knows whose options (if anyone's)
    // to include -- only the currently active seat's own caller ever sees them.
    const room = await roomApi('/api/rooms/' + ROOM_CODE + '?player_id=' + encodeURIComponent(MY_PID),
                               {timeoutMs: ROOM_POLL_STUCK_MS});
    if (myGen !== ROOM_GEN) return;
    const fresh = applyRoom(room);
    // [A149] A poll whose room version is not new used to stop here, and the match data
    // was only ever fetched on a NEW version. That froze a room for good: when a poll
    // stored a new version but was then superseded by a mutation before it could store
    // the match data (the host's own countdown firing, say, and being refused), the page
    // held a match older than its version with no reason ever to ask again. And because
    // the server's own inactivity failsafe (A122) runs only when somebody asks for the
    // match, a room whose pages were all waiting on a version change nobody would make
    // never advanced at all. So the match is fetched when it is BEHIND the version it
    // belongs to, and re-checked every ROOM_MATCH_RECHECK_MS while the tournament runs.
    const matchStale = !!ROOM && ROOM.status === 'complete' && (
      ROOM_MATCH_AT < ROOM_VERSION_SEEN ||
      (!(ROOM_MATCH_DATA && ROOM_MATCH_DATA.complete)
        && Date.now() - ROOM_MATCH_FETCHED > ROOM_MATCH_RECHECK_MS));
    if (!fresh && !matchStale) return;     // an older read than one already applied
    roomOnline(true);
    if (ROOM.status === 'complete'){
      // The match phase keeps polling on the SAME interval as the draft -- a toss
      // winner, or the host advancing, needs every other seat's own screen to pick the
      // change up without a manual refresh.
      const at = ROOM_VERSION_SEEN;
      const m = await api(`/api/rooms/${ROOM_CODE}/match?player_id=${encodeURIComponent(MY_PID)}`);
      if (myGen !== ROOM_GEN) return;
      // The match payload is a SECOND request and races the same way the room one does,
      // so it needs the same ordering rather than being trusted for having arrived. It
      // carries no version of its own, but every match move is written through
      // `_save_room` (web/room_match.py) and so moves the room's -- meaning a room
      // version that advanced while this was in flight is exactly the signal that a
      // newer poll has already fetched a fresher match than this one.
      if (at !== ROOM_VERSION_SEEN) return;
      ROOM_MATCH_DATA = m;
      ROOM_MATCH_AT = at;
      ROOM_MATCH_FETCHED = Date.now();
    }
    renderRoom();
  } catch(e){
    if (e.status === 404){
      // The room is GONE, not unreachable -- swept past ROOM_TTL_HOURS, most likely.
      // Counting this as a connectivity failure would sit a "Reconnecting…" notice over
      // a room that is never coming back. Handled the same way `boot`'s own resume
      // handles a 404: clear the saved session for good and send them somewhere real.
      if (ROOM_POLL){ clearInterval(ROOM_POLL); ROOM_POLL = null; }
      clearRoomSession();
      slip('This room has expired.');
      location.href = '/rooms';
      return;
    }
    // A single failure is genuinely not worth interrupting anyone over -- but silently
    // swallowing EVERY one is what made a room that was actually erroring look exactly
    // like a room that was merely slow, for players and for anyone trying to debug it.
    // roomOnline counts consecutive failures and only says anything once they stop
    // looking like a blip.
    roomOnline(false);
  }
}

// Consecutive poll failures, and the banner they eventually earn. One dropped request on
// a phone changing cells is normal and says nothing; several in a row is worth showing,
// because the screen is otherwise frozen with no explanation.
let ROOM_POLL_FAILS = 0;
const ROOM_FAILS_BEFORE_WARNING = 3;
function roomOnline(ok){
  ROOM_POLL_FAILS = ok ? 0 : ROOM_POLL_FAILS + 1;
  const el = $('#roomOffline');
  if (!el) return;
  el.classList.toggle('hide', ROOM_POLL_FAILS < ROOM_FAILS_BEFORE_WARNING);
}

async function startRoomDraft(ctrl){
  await busyClick(ctrl, ROOM && ROOM.game !== 'draft' ? 'Opening the auction…' : 'Starting…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await roomApi(`/api/rooms/${ROOM_CODE}/start`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({player_id: MY_PID})});
      if (myGen !== ROOM_GEN) return;
      applyRoom(room);
      renderRoom();
    } catch(e){ slip(e.message); }
  });
}

function roomOpenSlots(me){
  const open = new Set(Array.from({length:11}, (_, i) => i + 1));
  open.add(12);
  me.order.forEach((c, i) => { if (c) open.delete(i + 1); });
  if (me.impact) open.delete(12);
  return open;
}

function roomTake(i, ctrl){
  // Always a two-step confirm, even when only one slot is eligible -- clicking "Take"
  // must never itself commit the pick. A single eligible row still gets highlighted
  // (roomOrderRow's own eligibility check already handles that), the player just has
  // to click it, the same as when there's a real choice among several rows.
  const me = ROOM.players.find(p => p.player_id === MY_PID);
  const card = me.deal.options[i];
  // `at` pins the selection to THIS deal. An index only means something against the deal
  // it was read from, and a turn can end under the player -- the clock's auto-pick -- so
  // renderRoomDraft drops any selection made at a different pick count.
  ROOM_PENDING = (ROOM_PENDING && ROOM_PENDING.index === i)
    ? null : {index: i, card, at: me.picks_made};
  renderRoom();
  if (ROOM_PENDING && STACKED_DRAFT.matches) roomOpenPickSheet(me);
}

// The phone slot picker (common.js), fed this seat's own order.
function roomOpenPickSheet(me){
  const {index, card} = ROOM_PENDING;
  const slots = [...roomOpenSlots(me)]
    .filter(s => s === 12 || card.positions.includes(s)).sort((a, b) => a - b);
  if (!slots.length) return;
  const overseas = [...me.order, me.impact].filter(c => c && c.overseas).length;
  const cap = META && META.overseas_cap;
  openSlotSheet({
    name: card.name,
    status: `${me.picks_made} of 12 chosen` + (cap ? ` · ${overseas} of ${cap} overseas` : ''),
    slots,
    onChoose: slot => roomSubmitPick(index, slot, null, ROOM_PENDING && ROOM_PENDING.at),
    onCancel: () => { ROOM_PENDING = null; renderRoom(); },
  });
}

function roomRowClick(slot, ctrl){
  if (!ROOM_PENDING) return;
  roomSubmitPick(ROOM_PENDING.index, slot, ctrl, ROOM_PENDING.at);
}

// `at` is this seat's pick count when the card was chosen [A146]: the server refuses the
// pick if the clock has since picked for this seat, rather than applying the index to a
// fresh deal the player never saw.
async function roomSubmitPick(index, slot, ctrl, at){
  await busyClick(ctrl, 'Taking…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await roomPost(`/api/rooms/${ROOM_CODE}/pick`,
        {index, slot, picks_made: (typeof at === 'number') ? at : null}, true);
      if (myGen !== ROOM_GEN) return;
      applyRoom(room);
      // Cleared unconditionally, not only when applyRoom accepted: the selection this
      // held has been submitted either way, and leaving it set would let a second click
      // resubmit an index against a deal the server has already moved past.
      ROOM_PENDING = null;
      renderRoom();
    } catch(e){
      if (e.room){ ROOM_PENDING = null; renderRoom(); }
      slip(e.message);
    }
  });
}

function renderRoom(){
  const r = ROOM;
  if (!r) return;
  $('#roomLobby').classList.toggle('hide', r.status !== 'lobby');
  $('#roomDraft').classList.toggle('hide', r.status !== 'drafting');
  $('#roomAuction').classList.toggle('hide', r.status !== 'auctioning');
  $('#roomResult').classList.toggle('hide', r.status !== 'complete');
  $('#roomFailed').classList.toggle('hide', r.status !== 'failed');

  if (r.status === 'lobby') renderRoomLobby(r);
  else if (r.status === 'drafting') renderRoomDraft(r);
  else if (r.status === 'auctioning') renderRoomAuction(r);
  else if (r.status === 'failed') renderRoomFailed(r);
  else renderRoomResult(r);
}

function renderRoomFailed(r){
  if (ROOM_POLL){ clearInterval(ROOM_POLL); ROOM_POLL = null; }
  if (ROOM_TIMER_TICK){ clearInterval(ROOM_TIMER_TICK); ROOM_TIMER_TICK = null; }
  $('#roomFailedCode').textContent = r.code;
  $('#roomFailedReason').textContent = r.failure_reason || '';
}

function renderRoomLobby(r){
  const auction = r.game !== 'draft';
  $('#lobbyCode').textContent = r.code;
  $('#lobbySeats').textContent = `${r.players.length} of ${r.seats}`;
  $('#lobbyFormat').textContent = !auction ? r.format.toUpperCase()
    : (r.game === 'mega' ? 'MEGA AUCTION · RETENTIONS & RTM' : 'AUCTION · LEAGUE');
  $('#lobbyTimer').textContent = auction ? '15s a lot, +5s a bid' : r.timer_seconds + 's per pick';
  $('#lobbyMode').textContent = auction ? '₹120 crore each'
                                        : (r.draft_mode === 'memory' ? 'Memory' : 'Stat');
  $('#lobbyStartBtn').textContent = auction ? 'Start auction' : 'Start draft';
  $('#lobbyFranchiseWrap').classList.toggle('hide', !auction);
  if (auction) renderLobbyFranchises(r);
  const mine = r.players.find(p => p.player_id === MY_PID);
  $('#lobbyKitWrap').classList.toggle('hide', auction || !mine || !mine.kit);
  if (!auction && mine && mine.kit) $('#lobbyKit').innerHTML = roomKitStrip(mine);
  const amHost = MY_PID === r.host_id;
  $('#lobbyPlayers').innerHTML = r.players.map(p => {
    const isHost = p.player_id === r.host_id;
    // Only the host sees this, and never against their own seat -- rooms.kick_player
    // refuses both cases too, this just keeps the button from ever being offered.
    const kickBtn = (amHost && !isHost)
      ? `<span class="picks"><button class="act" onclick="kickRoomPlayer('${p.player_id}', this)"
           >Kick</button></span>` : '';
    const fr = p.franchise ? ` ${chip(p.franchise)}` : '';
    // The team name only where it says something the player's own name does not.
    const team = p.kit && p.kit.name !== p.name ? ` <em class="kit-team">${esc(p.kit.name)}</em>` : '';
    return `<div class="entry"><span class="nm">${roomSeatMark(p)}${esc(p.name)}${isHost
      ? ' <em style="color:var(--gold);font-style:normal">· host</em>' : ''}${fr}${team}</span>${kickBtn}</div>`;
  }).join('');
  $('#lobbyStartBtn').classList.toggle('hide', !amHost);
}

// --- team kits [A151] ------------------------------------------------------------------------
//
// Each drafted side in a draft room wears a kit -- chosen, or a distinct default the server
// hands out -- and everyone sees everyone's. Auction and filler seats are franchises and
// show their crest instead.

function roomSeatMark(p){
  if (p.crest) return crestImg(p.crest, 'row-crest');
  if (p.kit) return kitBadge(p.kit, 'kit-badge-row');
  return '';
}

// Editable until the host starts the matches; the server refuses after that (kits lock so
// a side cannot change its name partway through a tournament it is on the scoreboard of).
function roomKitStrip(me){
  const locked = ROOM_MATCH_DATA && !ROOM_MATCH_DATA.awaiting_start;
  return kitStripHtml(me.kit, 'Your team', locked ? null : 'editRoomKit()');
}

function editRoomKit(){
  const me = ROOM && ROOM.players.find(p => p.player_id === MY_PID);
  if (!me) return;
  openKitEditor({
    kit: me.kit, title: 'Your team kit',
    note: 'Everyone in this room sees it. It locks when the matches start.',
    onSave: async (k) => {
      const myGen = ++ROOM_GEN;
      const room = await roomApi(`/api/rooms/${ROOM_CODE}/kit`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({player_id: MY_PID, ...k})});
      // Kept as your own kit too, so the next room (and solo) start with it.
      saveMyKit(k).catch(() => {});
      if (myGen !== ROOM_GEN) return;
      applyRoom(room);
      renderRoom();
      if (ROOM_MATCH_DATA && ROOM_MATCH_DATA.awaiting_start) renderRoomStartReview(ROOM_MATCH_DATA);
    },
  });
}

// --- auction rooms [A139] --------------------------------------------------------------------

function renderLobbyFranchises(r){
  const held = {};
  r.players.forEach(p => { if (p.franchise) held[p.franchise] = p; });
  $('#lobbyFranchises').innerHTML = FRANCHISES.map(([s, name]) => {
    const owner = held[s];
    const mine = owner && owner.player_id === MY_PID;
    const taken = owner && !mine;
    return franchiseButton(s, name,
      `${taken ? 'disabled' : ''} onclick="chooseRoomFranchise('${s}', this)" title="${name}"`,
      `<span class="auc-fr-owner">${owner ? esc(owner.name) : ''}</span>`)
      .replace('class="auc-fr ', `class="auc-fr${mine ? ' sel' : ''}${taken ? ' taken' : ''} `);
  }).join('');
}

async function chooseRoomFranchise(short, ctrl){
  await busyClick(ctrl, null, async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await roomApi(`/api/rooms/${ROOM_CODE}/franchise`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({player_id: MY_PID, short})});
      if (myGen !== ROOM_GEN) return;
      applyRoom(room);
      renderRoom();
    } catch(e){ slip(e.message); }
  });
}

// auction.js draws the floor; this only feeds it. An exchange that is still animating is
// never cut off by the next update -- but updates are COALESCED rather than queued [A146].
// Each exchange takes ~2s to play and polls arrive every second, so a queue of them fell
// further behind the real room with every opponent bid: the floor showed a price seconds
// old, a bid against it came back "outbid", and the room looked frozen. Now only the
// newest view waits; when the current animation ends it plays straight to that one, the
// bids in between compressed into the same couple of seconds.
let AUCTION_NEXT = null, AUCTION_DRAIN = null;
let AUCTION_SEEN = 0;
function queueAuctionView(view){
  AUCTION_NEXT = view;
  if (!AUCTION_DRAIN){
    AUCTION_DRAIN = (async () => {
      try {
        while (AUCTION_NEXT){
          const v = AUCTION_NEXT;
          AUCTION_NEXT = null;
          try { await apply(v, !!A); } catch(e){ slip(e.message); }
        }
      } finally { AUCTION_DRAIN = null; }
    })();
  }
  return AUCTION_DRAIN;
}
function renderRoomAuction(r){
  if (!r.auction || r.version <= AUCTION_SEEN) return;
  AUCTION_SEEN = r.version;
  queueAuctionView(r.auction);
}

// Only these are retried: each names its lot, so a repeat can never land on another one.
const AUCTION_RETRYABLE = new Set(['bid', 'limit', 'pass']);

// The two hooks auction.js calls in room mode.
window.roomAuctionPost = async (path, body) => {
  const myGen = ++ROOM_GEN;
  let room;
  try {
    room = await roomPost(`/api/rooms/${ROOM_CODE}/auction/${path}`, body,
                          AUCTION_RETRYABLE.has(path));
  } catch(e){
    // A refusal carries the room as it now is: draw it before the message shows, so
    // "outbid" arrives with the new price already on the floor.
    if (e.room && myGen === ROOM_GEN){
      AUCTION_SEEN = Math.max(AUCTION_SEEN, e.room.version);
      if (e.room.status === 'auctioning' && e.room.auction) await queueAuctionView(e.room.auction);
      else renderRoom();
    }
    throw e;
  }
  if (myGen !== ROOM_GEN) return null;
  AUCTION_SEEN = Math.max(AUCTION_SEEN, room.version);
  if (room.status !== 'auctioning'){ renderRoom(); return null; }
  // Through the same queue as a poll's update, so your own exchange never plays over the
  // top of one still animating. Returns null: the queue has already drawn it.
  await queueAuctionView(room.auction);
  return null;
};
window.roomAuctionDeadline = () => (ROOM ? ROOM.turn_started_at - serverClock() : 0);

async function kickRoomPlayer(targetId, ctrl){
  await busyClick(ctrl, 'Kicking…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await roomApi(`/api/rooms/${ROOM_CODE}/kick`, {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID, target_id: targetId})});
      if (myGen !== ROOM_GEN) return;
      applyRoom(room);
      renderRoom();
    } catch(e){ slip(e.message); }
  });
}

let LAST_ROOM_DEAL_FS = null;   // fs_id last shown in the room's deal card -- shared by
                                 // both branches below since they write the same elements

function showRoomDeal(deal, fallbackText){
  if (deal){
    if (deal.fs_id !== LAST_ROOM_DEAL_FS){
      LAST_ROOM_DEAL_FS = deal.fs_id;
      rollDeal($('#roomDealYear'), $('#roomDealTeam'), deal.season_year, deal.franchise, deal.crest);
    }
  } else {
    LAST_ROOM_DEAL_FS = null;
    clearTimeout(ROLL_TIMERS.get($('#roomDealTeam')));
    $('#roomDealYear').textContent = '';
    $('#roomDealTeam').textContent = fallbackText;
  }
}

// Renders the countdown from ROOM_TIMER_BASE, which holds the server's own turn-start
// time and our measured offset from its clock -- so this is a straight computation of
// "how much of the turn is left", not a local decrement that drifts. Called once a
// second by ROOM_TIMER_TICK, and once more immediately whenever a fresh poll lands (via
// renderRoomDraft) so a correction is never left waiting up to a second to appear.
function tickRoomTimer(){
  if (!ROOM_TIMER_BASE) return;
  const el = $('#roomTimer');
  if (!el) return;
  const {startedAt, max} = ROOM_TIMER_BASE;
  // Nothing here depends on when any response arrived, which is the whole point: flight
  // time varies from poll to poll and used to be absorbed silently into the reading.
  const remaining = Math.max(0, Math.round(max - (serverClock() - startedAt)));
  el.textContent = remaining;
  const urgent = remaining > 0 && remaining <= 5;
  el.classList.toggle('urgent', urgent);
  const bar = $('#roomTimerBar');
  if (bar){
    const pct = max > 0 ? Math.max(0, Math.min(100, remaining / max * 100)) : 0;
    bar.style.width = pct + '%';
    bar.parentElement.classList.toggle('urgent', urgent);
  }
}

function renderRoomDraft(r){
  const me = r.players.find(p => p.player_id === MY_PID);
  const active = r.players.find(p => p.player_id === r.active_player_id);
  const amActive = !!me && r.active_player_id === MY_PID;
  // A selection outlives its deal if the turn ends under it (the clock's auto-pick), and
  // its index would then name somebody in the NEXT deal -- a player never chosen. Dropped,
  // and the phone picker closed with it, the moment it stops being this deal's.
  if (ROOM_PENDING && (!amActive || !me.deal || me.picks_made !== ROOM_PENDING.at)){
    ROOM_PENDING = null;
    closeSlotSheet();
  }
  $('#roomRoundNow').textContent = `round ${r.round + 1}/${r.rounds_total}`;
  tickRoomTimer();
  $('#roomCodeStamp').textContent = 'room ' + r.code;

  if (amActive){
    // My turn: the deal (with options) is mine, and my own order sheet is what's
    // useful to see while I decide -- unchanged from before the turn-based redesign.
    $('#roomDealLabel').textContent = 'Dealt to you';
    $('#roomDealLabel').classList.add('mine');   // your own turn: the timer carries the urgency
    $('#roomOrderLabel').textContent = 'Batting order';
    showRoomDeal(me.deal, me.done ? 'Your twelve is set' : '—');
    const blocked = (me.deal && me.deal.blocked) || [];
    $('#roomOptionsLabel').textContent = me.deal
      ? `Available (${me.deal.options.length} of ${me.deal.options.length + blocked.length})`
      : 'Available';
    const live = !me.deal ? '' : me.deal.options.map((card, i) => {
      const pending = ROOM_PENDING && ROOM_PENDING.index === i ? ' pending' : '';
      const nmClick = roomDraftMode() === 'stat' ? ` clickable" onclick="showRoomStat(${i})"` : '"';
      return `<div class="entry${pending}">
        <span class="nm${nmClick}>${ICON[card.kind] || ''}${keeperBadge(card)}<span class="flag ${card.overseas ? '' : 'home'}"
          title="${card.overseas ? 'overseas' : 'domestic'}"></span>${card.name}${ratingBadge(card)}</span>
        <span class="picks"><button class="take" onclick="roomTake(${i}, this)">${pending ? 'Choose a row' : 'Take'}</button></span>
      </div>`;
    }).join('');
    // The rest of the squad, greyed with the reason -- A64's own rule, brought to
    // rooms: a keeper already spoken for should still read as a keeper on the roster,
    // not make the deal look thin.
    const dead = blocked.length ? `<div class="roster-head">Unavailable</div>` +
      blocked.map((b, i) => {
        const nmClick = roomDraftMode() === 'stat'
          ? ` clickable" onclick="showRoomBlockedStat(${i})"` : '"';
        return `<div class="entry off">
          <span class="nm${nmClick}>${ICON[b.kind] || ''}${keeperBadge(b)}<span class="flag ${b.overseas ? '' : 'home'}"></span>${b.name}${ratingBadge(b)}</span>
          <span class="why">${b.blocked || ''}</span>
        </div>`;
      }).join('') : '';
    $('#roomOptions').innerHTML = live + dead;

    const open = me.deal ? roomOpenSlots(me) : new Set();
    const rows = me.order.map((got, i) => roomOrderRow(i + 1, got, `${i + 1}`, open));
    rows.push(roomOrderRow(12, me.impact, 'IMP', open, true));
    $('#roomOrderSheet').innerHTML = rows.join('');
    $('#roomViewToggle').classList.add('hide');
  } else {
    // Someone else's turn: their franchise/season is visible, never their options
    // (the server already redacts `options` to [] for anyone but the active seat's
    // own caller). The order-sheet column defaults to their team-so-far, since mine
    // doesn't change while I wait -- but a toggle lets me check my own squad too.
    if (r.active_player_id !== ROOM_VIEW_LAST_ACTIVE){
      ROOM_VIEW_MINE = false;
      ROOM_VIEW_LAST_ACTIVE = r.active_player_id;
    }
    const showMine = ROOM_VIEW_MINE && !!me;
    const shown = showMine ? me : active;

    $('#roomDealLabel').textContent = active ? `${active.name} is picking` : 'Waiting…';
    $('#roomDealLabel').classList.remove('mine');
    $('#roomOptionsLabel').textContent = '';
    $('#roomOrderLabel').textContent = showMine ? 'Your team so far'
      : (active ? `${active.name}'s team so far` : 'Batting order');
    showRoomDeal(active && active.deal, r.status === 'complete' ? 'Draft complete' : '—');
    $('#roomOptions').innerHTML = '<div class="note">'
      + (active ? `${esc(active.name)} is choosing -- options stay hidden until it's their turn to show.`
                : 'Waiting on the rest of the room…') + '</div>';

    const rows = (shown ? shown.order : Array(11).fill(null))
      .map((got, i) => roomOrderRow(i + 1, got, `${i + 1}`, new Set(), false, true));
    rows.push(roomOrderRow(12, shown ? shown.impact : null, 'IMP', new Set(), true, true));
    $('#roomOrderSheet').innerHTML = rows.join('');

    const toggle = $('#roomViewToggle');
    if (active && me && active.player_id !== me.player_id){
      toggle.classList.remove('hide');
      toggle.textContent = showMine ? `View ${active.name}'s squad` : 'View your squad';
    } else {
      toggle.classList.add('hide');
    }
  }

  $('#roomOthers').innerHTML = r.players.filter(p => p.player_id !== MY_PID).map(p => {
    const isActive = p.player_id === r.active_player_id;
    // A filler seat (is_cpu) is never active -- it never takes a turn at all, a real
    // historical franchise-season's own eleven from the moment the room starts drafting
    // (p.name is already that squad's name, p.picks_made already reads 12/12) -- so
    // there's no "waiting"/"done" distinction left to make for one.
    const role = isActive ? 'ACTIVE' : (p.is_cpu ? 'HISTORICAL' : (p.done ? 'DONE' : 'WAITING'));
    return `<div class="line ${p.done ? 'set' : ''} ${isActive ? 'active-turn' : ''}">
      <span class="role">${role}</span>
      <span class="who">${roomSeatMark(p)}${esc(p.name)}</span>
      <span class="club">${isActive && p.deal ? (p.deal.franchise + ' ' + p.deal.season_year) : ''}</span>
      <span class="fig">${p.picks_made}/12</span>
    </div>`;
  }).join('');
}

function toggleRoomView(){
  ROOM_VIEW_MINE = !ROOM_VIEW_MINE;
  if (ROOM) renderRoomDraft(ROOM);
}

function roomOrderRow(slot, got, label, open, isImpact, readOnly, forceReveal){
  const classes = ['orderline'];
  if (got) classes.push('filled');
  if (isImpact) classes.push('impactrow');
  if (!readOnly && ROOM_PENDING){
    const eligible = !got && open.has(slot)
      && (slot === 12 || ROOM_PENDING.card.positions.includes(slot));
    classes.push(eligible ? 'eligible' : 'ineligible');
  }
  const whoClick = (!readOnly && got && roomDraftMode() === 'stat')
    ? ` clickable" onclick="event.stopPropagation(); showRoomOrderStat(${slot})"` : '"';
  const label2 = got ? (ICON[got.kind] || '') + keeperBadge(got) + got.name + ratingBadge(got, forceReveal)
                     : (isImpact ? 'no impact player' : 'to be named');
  const rowClick = readOnly ? '' : ` onclick="roomRowClick(${slot}, this)"`;
  return `<div class="${classes.join(' ')}"${rowClick}>
    <span class="num">${label}</span>
    <span class="who${whoClick}>${label2}</span>
  </div>`;
}

function showRoomStat(i){
  const me = ROOM.players.find(p => p.player_id === MY_PID);
  if (me && me.deal) showStat(me.deal.options[i]);
}

function showRoomBlockedStat(i){
  const me = ROOM.players.find(p => p.player_id === MY_PID);
  if (me && me.deal) showStat(me.deal.blocked[i]);
}

function showRoomOrderStat(slot){
  const me = ROOM.players.find(p => p.player_id === MY_PID);
  if (me) showStat(slot === 12 ? me.impact : me.order[slot - 1]);
}

function renderRoomResult(r){
  // Unlike the draft phase, the match phase keeps polling (see pollRoom) -- more than
  // one seat can have something to do next (whoever wins a toss, then always the
  // host), so there is no single caller whose own action ends the wait.
  if (ROOM_TIMER_TICK){ clearInterval(ROOM_TIMER_TICK); ROOM_TIMER_TICK = null; }
  $('#roomResultCode').textContent = r.code;
  if (ROOM_MATCH_DATA){
    showRoomMatch(ROOM_MATCH_DATA);
    return;
  }
  // The draft's own last pick can flip status to 'complete' without the match phase
  // having been fetched yet (roomSubmitPick has no reason to know about it) -- fetch
  // once here rather than leaving the panel blank until the next scheduled poll. A
  // read, like pollRoom -- captures the generation, never bumps it.
  const myGen = ROOM_GEN;
  api(`/api/rooms/${ROOM_CODE}/match?player_id=${encodeURIComponent(MY_PID)}`)
    .then(m => { if (myGen !== ROOM_GEN) return; ROOM_MATCH_DATA = m; showRoomMatch(m); })
    .catch(() => {});
}

// A room's group stage is up to 70 matches, most of them between two OTHER sides --
// dumping all of them in one flat list (the design this replaced) meant scrolling past
// 70+ undifferentiated rows to find your own eleven's story, or the champion banner,
// and every decided match showed the SAME green "W" regardless of who actually won,
// since nothing distinguished "somebody won" from "you won". `roomYourResults` and
// `roomPlayoffsHtml` split the two things a viewer actually wants -- their own
// group-stage form, and the small, universally-relevant knockout bracket -- mirroring
// solo's own result screen (season.js's `renderTableAndForm`'s `yourForm` /
// `renderVerdictAndBracket`'s `bracket`) exactly, including the personalised win/loss
// badge neither of those needed to invent since solo's own side is always
// home-or-away-resolvable by construction.

function roomYourResults(m){
  // Returns {html, won, lost} -- the CALLER's own group-stage matches only (`stage ===
  // 'league'`), never the other ~56 of 70 that don't involve them. `i` is preserved as
  // the ORIGINAL index into `m.results`, since showRoomScorecard(i) indexes that array
  // directly, not whatever subset ends up rendered here.
  const mine = [];
  m.results.forEach((e, i) => { if (e.stage === 'league' && e.result.yours) mine.push([i, e.result]); });
  let won = 0, lost = 0;
  const html = mine.map(([i, r]) => {
    const myShort = r.you_home ? r.home : r.away;
    const themSide = r.you_home ? resolveSide(r.away, r.away_crest, r.away_kit)
                                : resolveSide(r.home, r.home_crest, r.home_kit);
    const mineScore = r.you_home ? r.home_score : r.away_score;
    const theirsScore = r.you_home ? r.away_score : r.home_score;
    const k = r.winner === myShort ? 'w' : (r.winner === null ? '' : 'l');
    if (k === 'w') won++; else if (k === 'l') lost++;
    return `<div class="fx" onclick="showRoomScorecard(${i})">
      <span class="wl ${k}">${k ? k.toUpperCase() : 'T'}</span>
      <span>v ${sideMark(themSide)}${esc(themSide.name)}</span><span class="sc">${mineScore} · ${theirsScore}</span></div>`;
  }).join('');
  return {html, won, lost};
}

function roomPlayoffsHtml(m){
  // Every playoff-stage fixture (`stage !== 'league'`) -- small and universally
  // relevant, so shown to everyone rather than filtered to "yours" the way the group
  // stage is. Badged from the CALLER's own perspective when they played in it, a
  // neutral dot otherwise, exactly like solo's own bracket.
  const rows = [];
  m.results.forEach((e, i) => { if (e.stage !== 'league') rows.push([i, e]); });
  return rows.map(([i, e]) => {
    const r = e.result;
    const myShort = r.yours ? (r.you_home ? r.home : r.away) : null;
    const k = !r.yours ? '' : (r.winner === myShort ? 'w' : (r.winner === null ? '' : 'l'));
    return `<div class="tie-stage">${e.stage}</div>
      <div class="fx" onclick="showRoomScorecard(${i})">
        <span class="wl ${k}">${r.yours ? (k ? k.toUpperCase() : 'T') : '·'}</span>
        <span>${roomRowSide(r.home, r.home_crest, r.home_kit)} v ${roomRowSide(r.away, r.away_crest, r.away_kit)}</span>
        <span class="sc">${r.home_score} · ${r.away_score}</span></div>
      <div class="fx" style="border:0;padding-top:2px"><span></span>
        <span class="sc" style="font-style:italic">${r.margin}</span><span></span></div>`;
  }).join('');
}

function roomRowSide(short, crest, kit){
  const side = resolveSide(short, crest, kit);
  return `${sideMark(side)}${esc(side.name)}`;
}

function roomTableHtml(m){
  // Shared by the in-progress view and the completed one -- `m.table` is populated by
  // the backend from the moment the round-robin settles all the way through the
  // playoffs (RoomMatchOut.table's own doc comment), not only once complete, so this
  // markup has to work identically in both places rather than living only in one.
  // [A153] The IPL-style table is reveal.js's, shared with solo. A Q is shown only once
  // every group fixture has been revealed: mid-reveal the top four are not yet settled.
  if (!m.table) return '';
  const settled = !(m.league_revealed != null && m.league_revealed < m.league_total);
  return standingsHtml(m.table, {complete: settled, champion: m.complete ? m.champion : null});
}

function roomWaitingHtml(m, myMatch){
  // A league room's own group stage: paced one fixture at a time (or all at once via
  // Skip ahead), mutually exclusive with the knockout-round logic below it since this
  // phase always has advance_ready=false -- the round-robin never has anyone's toss to
  // wait on, only the host's own pacing.
  if (m.league_revealed != null && m.league_revealed < m.league_total){
    // The host no longer CLICKS through seventy fixtures -- the countdown does it, and
    // the buttons are there to override it rather than to drive it.
    const actions = m.you_decide_league_reveal
      ? `<div class="foot-actions room-actions">
           <button class="act lead" onclick="roomAutoNow()">Continue now</button>
           <button class="act" onclick="roomAutoPause(this)">Pause</button>
           <button class="act" onclick="roomSkipTo('group_stage', this)">Skip group stage</button>
           <button class="act" onclick="roomSkipTo('tournament', this)">Skip to end</button>
         </div>
         <div class="margin" id="roomAutoLine"></div>`
      : `<div class="margin">Up next shortly…</div>`;
    const leagueTableHtml = roomTableHtml(m);
    return `<div class="report compact">
      <div class="over-line">League: ${m.league_revealed} of ${m.league_total} played</div>
      <div class="margin">Playing…</div>
      ${actions}
    </div>
    ${leagueTableHtml ? `<div class="room-gap">${leagueTableHtml}</div>` : ''}`;
  }

  // Every fixture in the current round that hasn't resolved yet -- several can be open
  // at once (a cup's two semis, a league's Qualifier 1 + Eliminator), and a resolved
  // one needs no placeholder here at all: it already appears in the results list below
  // the moment it finishes, exactly like any earlier round's match.
  const pendingRows = m.current_matches.filter(cm => cm.result === null).map(cm => {
    const isMine = cm.a_pid === MY_PID || cm.b_pid === MY_PID;
    const waitingOn = cm.pending_toss_winner_pid === cm.a_pid ? cm.a_name : cm.b_name;
    const a = {name: cm.a_name, crest: cm.a_crest, kit: cm.a_kit};
    const b = {name: cm.b_name, crest: cm.b_crest, kit: cm.b_kit};
    return `<div class="fx">
      <span>${cm.stage}${isMine ? ' (you)' : ''}</span>
      <span>${sideMark(a)}${esc(a.name)} v ${sideMark(b)}${esc(b.name)}</span>
      <span class="sc">${esc(waitingOn)} to call the toss</span>
    </div>`;
  }).join('');

  const advanceHtml = m.advance_ready
    ? (m.you_decide_advance
        ? `<div class="foot-actions room-actions">
             <button class="act lead" onclick="roomAutoNow()">Continue now</button>
             <button class="act" onclick="roomAutoPause(this)">Pause</button>
             <button class="act" onclick="roomSkipTo('tournament', this)">Skip to end</button>
           </div>
           <div class="margin" id="roomAutoLine"></div>`
        // Not "waiting for the host" any more: nobody is waiting ON anyone, the next
        // round is simply coming. The wording used to describe a dependency that is no
        // longer there, which is its own small piece of the room feeling stuck.
        : `<div class="margin">Next round shortly…</div>`)
    : '';

  const tableHtml = roomTableHtml(m);

  return `<div class="report compact">
    <div class="over-line">${m.round_label || ''}</div>
    ${pendingRows || '<div class="margin">Playing…</div>'}
    ${advanceHtml}
  </div>
  ${tableHtml ? `<div class="room-gap">${tableHtml}</div>` : ''}`;
}

function roomSpectateChoiceHtml(){
  return `<div class="report compact">
    <div class="call">Your run in this tournament is over</div>
    <div class="foot-actions room-actions">
      <button class="act lead" onclick="roomChooseSpectateExit('card')">See your journey card</button>
      <button class="act" onclick="roomChooseSpectateExit('follow')">Follow the tournament</button>
    </div>
    <div class="margin" id="roomAutoLine"></div>
  </div>`;
}

function roomChooseSpectateExit(choice){
  ROOM_SPECTATE_SHOWN = true;
  showRoomMatch(ROOM_MATCH_DATA);
  if (choice === 'card') showRoomJourneyCard();
}

// The over-by-over stepper is the SAME engine solo play uses (reveal.js's OVER_STEP,
// startOverStepper, etc.) -- a room viewer is never in more than one currently-open
// fixture at once, so the existing singleton is reused as-is, just fed room data and a
// room-specific onDone.

// [A149] Everyone watches every match, not just their own. This finds the next fixture
// this viewer still owes a reveal, or null:
//   * their OWN fixture's toss call, first -- only they can make it;
//   * then any resolved playoff fixture not yet watched, their own ahead of the rest, so a
//     round holding two (Qualifier 1 and the Eliminator, a cup's two semis) plays both,
//     one after the other.
// A resolved fixture is looked for in `current_matches` AND in `results`, because the
// tournament's very LAST fixture in every format completes the room in the same response
// that resolves it and so leaves `current_matches` empty (A83); `results` still carries
// it, full innings and all. The league's seventy group matches are paced by their own
// counter in showRoomMatch, and all share the stage name "league", so they are left out
// here entirely while the group stage is still being revealed.
function roomNextToReveal(m){
  if (m.league_revealed != null && m.league_revealed < m.league_total) return null;
  const isMine = f => f.a_pid !== undefined ? (f.a_pid === MY_PID || f.b_pid === MY_PID)
                                            : !!f.result.yours;
  const cur = m.current_matches.find(isMine);
  if (cur && cur.result === null && cur.you_decide_toss) return cur;
  const done = [...m.current_matches.filter(f => f.result),
                ...m.results.filter(e => e.stage !== 'league')];
  const unseen = done.filter(f => !ROOM_REVEALED.has(f.stage));
  return unseen.find(isMine) || unseen[0] || null;
}

// The first match payload of a visit -- a reload, or rejoining mid-tournament -- marks
// every playoff fixture from an EARLIER round as already seen, so coming back does not
// replay the whole bracket. The round still open is left unseen and plays as normal.
function roomSyncSeen(m){
  if (ROOM_REVEAL_SYNCED) return;
  ROOM_REVEAL_SYNCED = true;
  const open = new Set(m.current_matches.map(f => f.stage));
  (m.results || []).forEach(e => { if (!open.has(e.stage)) ROOM_REVEALED.add(e.stage); });
}

function roomEnterReveal(myMatch){
  ROOM_REVEAL_ACTIVE = myMatch;
  hideAllRevealScreens();
  go('reveal');
  if (myMatch.result === null) showRoomTossScreen(myMatch);
  else roomStartReveal(myMatch);
}

function showRoomTossScreen(myMatch){
  $('#tossStage').textContent = myMatch.stage;
  const tossMark = (crest, kit) => crest ? crestImg(crest, 'toss-crest')
    : (kit ? kitBadge(kit, 'kit-badge-toss') : '');
  $('#tossOpponent').innerHTML = `${tossMark(myMatch.a_crest, myMatch.a_kit)}${esc(myMatch.a_name)} v ${
    tossMark(myMatch.b_crest, myMatch.b_kit)}${esc(myMatch.b_name)} -- elect to bat or bowl first.`;
  $('#tossScreen').classList.remove('hide');
}

async function roomSubmitTossReveal(elects, ctrl){
  const stage = ROOM_REVEAL_ACTIVE.stage;
  await busyClick(ctrl, elects === 'bat' ? 'Batting…' : 'Bowling…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const m = await api(`/api/rooms/${ROOM_CODE}/match/toss`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID, stage, elects})});
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
      const fresh = roomNextToReveal(m);
      if (fresh && fresh.stage === stage) roomStartReveal(fresh);
      else { ROOM_REVEAL_ACTIVE = null; go('room'); showRoomMatch(m); }
    } catch(e){ slip(e.message); }
  });
}

function roomStartReveal(myMatch){
  ROOM_REVEAL_ACTIVE = myMatch;
  roomShowRevealSkips();
  const r = myMatch.result;
  const steps = [];
  const home = resolveSide(r.home, r.home_crest, r.home_kit);
  const away = resolveSide(r.away, r.away_crest, r.away_kit);
  if (r.home_innings) steps.push([r.home_innings, `${myMatch.stage} · ${home.name} batting`, null,
    {bat: home, bowl: away}]);
  // Built from r.home_innings/r.home directly, not steps[0] -- home always bats first in
  // this engine, but indexing into steps would attach the wrong innings as "prior" in the
  // (currently unreached) case where home_innings is ever absent.
  if (r.away_innings) steps.push([r.away_innings, `${myMatch.stage} · ${away.name} batting`,
    r.home_innings ? { innings: r.home_innings, battingLabel: home.name } : null,
    {bat: away, bowl: home}]);
  let i = 0;
  (function next(){
    if (i >= steps.length){ roomFinishReveal(myMatch); return; }
    const [innings, label, priorContext, matchup] = steps[i++];
    startOverStepper(innings, label, next, priorContext, matchup);
  })();
}

// Solo has had "Skip this match" since A90; a room had only "skip to the end of this
// INNINGS", so a viewer wanting out of a match they had lost interest in clicked twice
// per match and could never jump one. Needs no server call, unlike solo's own version:
// a room's whole result is already client-side by the time the reveal starts, so this
// just abandons the innings chain and lands where the chain would have landed anyway.
// Solo offers "skip the group stage" and "skip to the end" from inside the reveal, so a
// viewer who has seen enough does not have to sit through the rest of an animation first.
// A room gets the same two, restricted to the HOST: they resolve the tournament for every
// seat, where "Skip innings"/"Skip match" beside them touch nothing but this screen.
// Group-stage skipping is offered only where a group stage exists.
function roomShowRevealSkips(){
  const amHost = !!ROOM && ROOM.host_id === MY_PID;
  const hasGroup = !!ROOM_MATCH_DATA && ROOM_MATCH_DATA.league_total != null
                    && ROOM_MATCH_DATA.league_revealed < ROOM_MATCH_DATA.league_total;
  const g = $('#roomSkipGroupBtn'), e = $('#roomSkipEndBtn');
  if (g) g.classList.toggle('hide', !(amHost && hasGroup));
  if (e) e.classList.toggle('hide', !amHost);
}

function roomSkipThisMatch(){
  if (!ROOM_REVEAL_ACTIVE) return;
  clearOverTimer();
  OVER_STEP = null;
  roomFinishReveal(ROOM_REVEAL_ACTIVE);
}

function roomFinishReveal(myMatch){
  ROOM_REVEAL_ACTIVE = null;
  ROOM_REVEALED.add(myMatch.stage);
  go('room');
  showRoomMatch(ROOM_MATCH_DATA);
}

// The squad-review screen every seat sees once the draft itself finishes, before a
// single ball is bowled -- `ROOM.players` (kept fresh every poll regardless of room
// status, unlike ROOM_MATCH_DATA which only starts existing once status is 'complete')
// already carries this seat's own order/impact/ratings, per `_room_player_out`'s own
// `done` gate, so nothing new needs fetching. Ratings are always shown here (`true` as
// roomOrderRow's forceReveal) -- this is a reveal, not a leak, regardless of the room's
// own draft_mode.
function renderRoomStartReview(m){
  const me = ROOM.players.find(p => p.player_id === MY_PID);
  const el = $('#roomMatchBody');
  if (!me){
    el.innerHTML = '<div class="report compact"><div class="margin">Waiting for the host to continue…</div></div>';
    return;
  }
  const rows = me.order.map((got, i) => roomOrderRow(i + 1, got, `${i + 1}`, new Set(), false, true, true));
  rows.push(roomOrderRow(12, me.impact, 'IMP', new Set(), true, true, true));
  const actions = m.you_decide_start
    ? `<div class="foot-actions room-actions">
         <button class="act lead" onclick="roomStartMatches(this)">Continue</button>
       </div>`
    : `<div class="margin">Waiting for the host to continue…</div>`;
  el.innerHTML = `${me.kit ? roomKitStrip(me) : ''}<div class="report compact">
      <div class="over-line">Your squad</div>
      <div class="ledger" style="grid-template-columns:repeat(3,1fr)">${teamRatingsHtml(me)}</div>
    </div>
    <div class="room-gap">${rows.join('')}</div>
    ${actions}`;
}

function showRoomMatch(m){
  // "Done" only ever appears once the match itself (not just the draft) is actually
  // over -- shown throughout the whole live tournament, it was one click away from
  // ending a still-competing or still-spectating player's session with no way back in,
  // before they ever reached the results screen they were waiting for.
  $('#roomDoneBtn').classList.toggle('hide', !m.complete);

  // Mid-animation for the viewer's own match -- a poll landing here mid-reveal must
  // not disturb it; ROOM_MATCH_DATA is already fresh, roomFinishReveal reads it once
  // the stepper is actually done.
  // A countdown must never outlive the screen it belongs to: the host watching their own
  // match must not have the next round advance out from under them.
  if (ROOM_REVEAL_ACTIVE){ roomDisarmAuto(); return; }
  roomSyncSeen(m);

  // Every seat reviews its own finished twelve -- and the three ratings that come with
  // it -- before a single ball is bowled. Nothing below this point (not even a first
  // toss) has been resolved yet while this holds, mirroring how a league's group-stage
  // reveal is checked ahead of the ordinary match-waiting branches below it.
  // The squad-review gate is a deliberate look-at-your-team moment rather than a paced
  // step, so nothing counts down behind it.
  if (m.awaiting_start){ roomDisarmAuto(); renderRoomStartReview(m); return; }

  // A league room's group-stage reveal: checked before roomNextToReveal, since a
  // round-robin fixture isn't participant-scoped the way a knockout fixture is -- the
  // shared pacing cursor (host-driven Continue/Skip ahead, same mechanism and same
  // performance profile as before) walks every viewer through the same seventy
  // fixtures together, and every viewer watches each one as the cursor lands on it.
  // ROOM_LEAGUE_REVEALED_THROUGH (not ROOM_REVEALED) tracks how far THIS client
  // has already watched, since every round-robin entry shares the literal stage
  // "league" and can't be told apart by name the way "Semi-final 1" can.
  if (m.league_revealed != null && m.league_next_result &&
      m.league_revealed > ROOM_LEAGUE_REVEALED_THROUGH){
    ROOM_LEAGUE_REVEALED_THROUGH = m.league_revealed;
    // [A149] Every viewer watches the fixture the cursor just landed on, not only the two
    // who played in it (A100's rule, reversed at the user's request). The host's own
    // auto-advance is disarmed while any reveal runs, so the room moves on once the host
    // has watched it -- and a viewer who wants less can Skip match or change the speed,
    // both of which touch only their own screen.
    roomDisarmAuto();
    roomEnterReveal(m.league_next_result);   // the exact same playoff reveal engine, unmodified
    return;
  }

  // Checked BEFORE `m.complete`, not after: the tournament's very last fixture can
  // resolve and complete the whole room in the same response (roomNextToReveal's own
  // comment has the why), so a straight `if (m.complete)` check here would show that
  // fixture's scoreline with no reveal at all, for both sides.
  const toReveal = roomNextToReveal(m);
  if (toReveal){ roomDisarmAuto(); roomEnterReveal(toReveal); return; }

  if (m.complete){ roomDisarmAuto(); showRoomMatchComplete(m); return; }

  const myMatch = m.current_matches.find(cm => cm.a_pid === MY_PID || cm.b_pid === MY_PID);
  const el = $('#roomMatchBody');
  // 'final'/'cup' have no 'league' stage at all, so `yourHtml` is naturally empty there
  // and this collapses to just the (small, in-progress) playoffs bracket -- the same
  // split used once the room completes, so the shape doesn't change out from under a
  // viewer the moment their room finishes.
  const yourHtml = roomYourResults(m).html;
  const playoffsHtml = roomPlayoffsHtml(m);
  const gapBody = (yourHtml ? `<div class="col-head" style="margin:20px 0 0"><span>Your matches</span></div>${yourHtml}` : '')
    + (playoffsHtml ? `<div class="col-head" style="margin:20px 0 0"><span>🏆 Playoffs</span></div>${playoffsHtml}` : '');
  const gap = gapBody ? `<div class="room-gap">${gapBody}</div>` : '';

  if (m.you_are_out && !ROOM_SPECTATE_SHOWN){
    el.innerHTML = roomSpectateChoiceHtml() + gap;
    // [A149] A knocked-out HOST still paces the room. This used to disarm, so the whole
    // room waited on the host answering "your run is over" (or on the server's 90-second
    // fallback) -- and with everyone watching every match, a knocked-out host is the
    // ordinary case, not a corner.
    roomSyncAuto(m);
    return;
  }

  el.innerHTML = roomWaitingHtml(m, myMatch) + gap;
  roomSyncAuto(m);
}

// Arm (or clear) the countdown to match whatever step the room is now on. Called only
// after a waiting screen has actually been rendered, so `#roomAutoLine` exists for the
// ticker to write into, and only for the host, since only the host may make these calls.
//
// Every OTHER path through showRoomMatch -- mid-reveal, the start review, a completed
// room -- disarms instead: a countdown left running behind a screen that no longer has a
// step to advance would fire into nothing, and worse, would fire while the host is still
// watching their own match.
function roomSyncAuto(m){
  if (m.league_revealed != null && m.league_revealed < m.league_total){
    if (!m.you_decide_league_reveal){ roomDisarmAuto(); return; }
    const target = m.league_revealed + 1;
    roomArmAuto(`league:${m.league_revealed}`, ROOM_LEAGUE_AUTO_MS,
      () => roomLeagueAdvance(target, null));
    return;
  }
  if (m.advance_ready && m.you_decide_advance){
    roomArmAuto(`round:${m.round_label}`, ROOM_AUTO_MS, () => roomAdvanceMatch(null));
    return;
  }
  roomDisarmAuto();
}

// Guards the save call against firing on every 2s poll once the room is complete --
// `showRoomMatchComplete` re-runs on every one of those. Reset to false only by
// `roomPlayAgain` succeeding (A84: a fresh seed on the same code is a genuinely new
// game), never by a plain poll, so a replayed room can be saved again as the distinct
// game it is.
let ROOM_SAVE_ATTEMPTED = false;

async function maybeSaveRoomResult(){
  if (!ME || !ME.account_id) return;
  if (ROOM_SAVE_ATTEMPTED) return;
  ROOM_SAVE_ATTEMPTED = true;
  try {
    await api(`/api/rooms/${ROOM_CODE}/save`, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({player_id: MY_PID})});
  } catch(e){ /* best-effort -- a failed save must never interrupt the result screen */ }
}

// The champion as a side to draw: the table row where there is one (a league), else the
// final's winning side, which carries its crest or kit; your own kit if it is yours.
function roomChampionSide(m){
  if (!m.champion) return null;
  if (m.you_champion){
    const me = ROOM && ROOM.players.find(p => p.player_id === MY_PID);
    if (me && (me.kit || me.crest)) return {name: me.kit ? me.kit.name : m.champion,
                                            crest: me.crest || null, kit: me.kit || null};
  }
  const row = (m.table || []).find(r => r.name === m.champion);
  if (row) return {name: row.name, crest: row.crest, kit: row.kit};
  // The last fixture is the final, and its winner is the champion. A result names sides
  // by their short form, not the champion's display name, so it is matched by winner.
  const last = m.results.length ? m.results[m.results.length - 1].result : null;
  if (last && last.winner && last.winner === last.home)
    return {name: m.champion, crest: last.home_crest, kit: last.home_kit};
  if (last && last.winner && last.winner === last.away)
    return {name: m.champion, crest: last.away_crest, kit: last.away_kit};
  return {name: m.champion};
}

function showRoomMatchComplete(m){
  maybeSaveRoomResult();
  // A room has no seed on the wire, and "Play again" reuses the code, so the run is told
  // apart by its own final scores as well -- the same room replayed is a new celebration.
  const last = m.results.length ? m.results[m.results.length - 1].result : null;
  celebrateChampion({
    key: `room:${ROOM_CODE}:${m.champion}:${last ? last.home_score + '|' + last.away_score : ''}`,
    side: roomChampionSide(m), mine: !!m.you_champion,
  });
  const el = $('#roomMatchBody');

  const journeyBtn = m.squad
    ? `<button class="act" onclick="showRoomJourneyCard()">Journey card</button>` : '';
  // Lead action, same as solo's own "Draft again" on its result screen -- any seated
  // player (not just the host) can fire it; the room resets in place (same code, same
  // seats) and everyone else's next poll picks the reset up on its own (roomPlayAgain's
  // own comment has the why), so this button doesn't need to coordinate with anyone.
  const playAgainBtn = `<button class="act lead" onclick="roomPlayAgain(this)">Play again</button>`;

  if (m.format === 'final'){
    const res = m.results[0].result;
    el.innerHTML = `<div class="report compact">
      <div class="call ${res.winner ? 'won' : ''}">${res.winner
        ? esc(resolveSide(res.winner, null, res.winner === res.home ? res.home_kit : res.away_kit).name) + ' win'
        : 'Tied'}</div>
      <div class="figures">${res.home_score} · ${res.away_score}</div>
      <div class="margin">${res.margin}</div>
      <div class="foot-actions room-actions">
        ${playAgainBtn}
        <button class="act" onclick="showRoomScorecard(0)">Scorecard</button>
        ${journeyBtn}
      </div></div>`;
    return;
  }

  if (m.format === 'cup'){
    // Every cup match IS a playoff match (a cup has no 'league' stage at all), so
    // roomPlayoffsHtml alone covers the whole tournament here -- grouped by stage with
    // its margin line, not the flat "stage: home v away" rows this replaced.
    el.innerHTML = `<div class="report compact">
      <div class="call won room-banner">${esc(m.champion)} win the cup</div>
      </div>
      ${roomPlayoffsHtml(m)}
      <div class="foot-actions room-actions">${playAgainBtn}${journeyBtn}</div>`;
    return;
  }

  // The league banner sits at the TOP here, matching 'cup'/'final' and solo's own
  // report-first layout. The table and the two results sections mirror solo's own
  // two-column result screen (season.js's `.season`): your own group-stage form on the
  // right (14 matches, not 70), the small universally-relevant playoffs bracket
  // beneath it, never the full round-robin dumped in one flat list.
  const your = roomYourResults(m);
  const playoffsHtml = roomPlayoffsHtml(m);
  // [A153] The same verdict block as solo: your finishing badge, or your champion's moment.
  const me = ROOM && ROOM.players.find(p => p.player_id === MY_PID);
  const myRow = m.table.find(r => r.you);
  const champRow = m.table.find(r => r.name === m.champion);
  const you = me ? {name: me.kit ? me.kit.name : (myRow ? myRow.name : me.name),
                    crest: me.crest || (myRow && myRow.crest) || null, kit: me.kit || null} : null;
  const hero = seasonHeroHtml({
    youChampion: m.you_champion, you, row: myRow, teams: m.table.length,
    champion: champRow ? {name: champRow.name, crest: champRow.crest, kit: champRow.kit}
                       : {name: m.champion},
    finishLine: myRow ? `Finished ${myRow.pos}${['th','st','nd','rd'][(myRow.pos % 100 - 20) % 10]
      || ['th','st','nd','rd'][myRow.pos] || 'th'} in the league, then won the final.` : '',
  });
  el.innerHTML = `${hero}
    <div class="season">
      <div class="season-wide">
        <div class="panel-glow"></div>
        <div class="panel-head"><h3>Points table</h3>
          <div class="stat"><b>${m.table.length}</b><span>teams</span></div></div>
        ${roomTableHtml(m)}
      </div>
      <div>
        <div class="panel-glow"></div>
        ${your.html ? `<div class="panel-head"><h3>Your matches</h3>
          <div class="stat"><b>${your.won} won</b><span>${your.lost} lost</span></div></div>${your.html}` : ''}
      </div>
      <div>
        <div class="panel-glow"></div>
        <div class="panel-head"><h3>Honours</h3></div>
        ${capRowHtml(m.orange_cap, m.orange_cap_runs, m.purple_cap, m.purple_cap_wickets,
                     m.orange_cap_side, m.purple_cap_side)}
        ${playoffsHtml ? `<div class="col-head" style="margin:26px -18px 0">
          <span>🏆 Playoffs</span></div>${playoffsHtml}` : ''}
      </div>
    </div>
    <div class="foot-actions room-actions">${playAgainBtn}${journeyBtn}
      <button class="act" onclick="openRoomAnalysis(this)">Season analysis</button></div>`;
}


// Only ever reached from the league branch above -- `final` and `cup` are one and three
// matches, and a Manhattan over three innings is a scorecard drawn sideways (the API
// refuses those formats for the same reason).
async function openRoomAnalysis(ctrl){
  await busyClick(ctrl, 'Reading the season…', async () => {
    try {
      renderAnalysis(await api(
        `/api/rooms/${ROOM_CODE}/analysis?player_id=${encodeURIComponent(MY_PID)}`),
        $('#anHost'));
      go('analysis');
    } catch(e){ slip(e.message || 'Could not read the season.'); }
  });
}

async function roomPlayAgain(ctrl){
  await busyClick(ctrl, 'Resetting…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await roomApi(`/api/rooms/${ROOM_CODE}/play-again`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({player_id: MY_PID})});
      if (myGen !== ROOM_GEN) return;
      applyRoom(room);
      // A fresh lobby has no match of its own yet -- cleared explicitly rather than
      // left stale, so a later completion can never be masked by this game's leftovers
      // (renderRoomResult's own "if ROOM_MATCH_DATA, skip the fetch" shortcut would
      // otherwise show the game just finished instead of the new one).
      ROOM_MATCH_DATA = null; ROOM_MATCH_AT = -1; ROOM_MATCH_FETCHED = 0;
      ROOM_PENDING = null; ROOM_REVEAL_ACTIVE = null;
      ROOM_REVEALED = new Set(); ROOM_REVEAL_SYNCED = false;
      ROOM_SPECTATE_SHOWN = false; ROOM_LEAGUE_REVEALED_THROUGH = 0;
      ROOM_SAVE_ATTEMPTED = false;
      renderRoom();
    } catch(e){ slip(e.message); }
  });
}

function showRoomJourneyCard(){
  // ROOM_MATCH_DATA already carries the journey card's own numbers once complete --
  // no second fetch, same reasoning as solo's own showJourneyCard.
  if (!ROOM_MATCH_DATA || !ROOM_MATCH_DATA.squad){ slip('Play the match first.'); return; }
  // [A151] Your own side, as the room knows it: a franchise's crest in an auction room, the
  // kit in a draft room.
  const me = ROOM && ROOM.players.find(p => p.player_id === MY_PID);
  const side = me ? {name: me.kit ? me.kit.name : (me.franchise || me.name),
                     crest: me.crest || null, kit: me.kit || null} : null;
  drawJourneyCard(ROOM_MATCH_DATA, 'ROOM ' + ROOM_CODE, side);
  $('#cardOverlay').classList.remove('hide');
}

async function roomAdvanceMatch(ctrl){
  await busyClick(ctrl, 'Continuing…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const m = await api(`/api/rooms/${ROOM_CODE}/match/advance`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID})});
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
      showRoomMatch(m);
    } catch(e){ slip(e.message); }
  });
}

async function roomStartMatches(ctrl){
  await busyClick(ctrl, 'Starting…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const m = await api(`/api/rooms/${ROOM_CODE}/match/start`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID})});
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
      showRoomMatch(m);
    } catch(e){ slip(e.message); }
  });
}

// `through` is the TARGET cursor, not an increment, so one round trip moves it any
// distance -- the auto-advance countdown sends `revealed + 1` per fixture. Skipping the
// group stage no longer comes through here at all: it needs to resolve whatever the room
// is waiting on rather than only the reveal cursor, so it goes to `roomSkipTo`.
async function roomLeagueAdvance(through, ctrl){
  await busyClick(ctrl, 'Revealing…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const m = await api(`/api/rooms/${ROOM_CODE}/match/league-advance`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID, through})});
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
      showRoomMatch(m);
    } catch(e){ slip(e.message); }
  });
}

// 'group_stage' | 'tournament'. Replaces the old client-side advance loop, which walked
// `/match/advance` while `advance_ready` held -- and `advance_ready` is false whenever a
// fixture is still waiting on a real toss, so it stalled there and could never actually
// reach the end of a tournament. One server call now resolves whatever the room is waiting
// on, of whatever kind, so "skip to the end" means it.
async function roomSkipTo(target, ctrl){
  const label = target === 'group_stage' ? 'Skipping group stage…' : 'Simulating…';
  await busyClick(ctrl, label, async () => {
    const myGen = ++ROOM_GEN;
    // Any countdown belongs to the step we are about to blow past; leaving it armed would
    // fire an advance into a room that has already moved on.
    roomDisarmAuto();
    try {
      const m = await api(`/api/rooms/${ROOM_CODE}/match/skip`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID, target})});
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
      // A skip is a decision to stop watching, so it must not land back in a reveal.
      // ROOM_REVEALED/ROOM_LEAGUE_REVEALED_THROUGH are what showRoomMatch consults
      // to decide whether a fixture still owes this viewer an animation; without moving
      // them forward, skipping to the end would immediately start playing the final.
      ROOM_REVEAL_ACTIVE = null;
      if (m.league_total != null) ROOM_LEAGUE_REVEALED_THROUGH = m.league_total;
      (m.results || []).forEach(e => ROOM_REVEALED.add(e.stage));
      go('room');
      showRoomMatch(m);
    } catch(e){ slip(e.message); }
  });
}

async function boot(){
  loadMe();   // fire-and-forget -- the auth control fills in whenever it resolves
  const m = await loadMeta();
  renderDeckStats(m);
}

function roomCodeFromPath(){
  const parts = location.pathname.split('/').filter(Boolean);   // ['rooms', 'ABC123']
  return (parts[0] === 'rooms' && parts[1]) ? parts[1].toUpperCase() : null;
}

// This page is resume-only -- it never creates or joins a room itself (that's /rooms's
// job). A visitor lands here either seconds after rooms.html's own create/join saved a
// matching session and navigated over, or on a reload/bookmark. Either way the server
// is the only source of truth: a saved session is verified against the URL's own code
// before anything is shown, and any mismatch or failure bounces to /rooms with the code
// pre-filled rather than guessing.
restoreOverSpeed();
boot().then(async () => {
  const urlCode = roomCodeFromPath();
  if (!urlCode){ location.href = '/rooms'; return; }
  let saved;
  try { saved = JSON.parse(localStorage.getItem(ROOM_STORAGE_KEY) || 'null'); }
  catch(e){ saved = null; }
  if (!saved || !saved.code || saved.code !== urlCode || !saved.playerId){
    location.href = '/rooms?code=' + encodeURIComponent(urlCode);
    return;
  }
  let room;
  try {
    room = await api(`/api/rooms/${urlCode}?player_id=${encodeURIComponent(saved.playerId)}`);
  } catch(e){
    if (e.status === 404) clearRoomSession();
    location.href = '/rooms?code=' + encodeURIComponent(urlCode);
    return;
  }
  if (!room.players.some(p => p.player_id === saved.playerId)){
    clearRoomSession();   // the seat itself is gone (kicked, or a stale entry) -- for good
    location.href = '/rooms?code=' + encodeURIComponent(urlCode);
    return;
  }
  enterRoom(urlCode, saved.playerId);
});
