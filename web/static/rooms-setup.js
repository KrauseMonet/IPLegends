// The room create/join screen. A successful create or join saves {code, playerId} to
// localStorage and navigates to /rooms/{code}, which does the actual live session --
// this page never shows a lobby or a draft itself.

// Null until the visitor picks one -- the format gates everything after it, so there is
// no defensible default: a pre-selected "Final" would silently decide the shape of the
// game for anyone who skimmed past the list.
let CHOSEN_FORMAT = null, CHOSEN_TIMER = 30, CHOSEN_ROOM_DRAFT_MODE = 'stat';
let CHOSEN_VISIBILITY = false;   // is_open -- closed (false) is the default, A95
let ROOM_OPEN_POLL = null;       // polls GET /api/rooms/open while the join tab is showing

// The format list is `.pick` rows now, not `.room-choice` buttons -- it is the screen's
// one real decision, so it gets the numerals and descriptions while the preference
// toggles below stay small.
function pickFormat(f){
  // Clicking the open row again shuts it -- the choice is a toggle, so a visitor can back
  // out of the whole configuration step without having to pick something else instead.
  if (CHOSEN_FORMAT === f){ closeRoomConfig(); return; }

  const panel = $('#roomConfig');
  const wasOpen = CHOSEN_FORMAT !== null;
  const first = !wasOpen;
  CHOSEN_FORMAT = f;
  document.querySelectorAll('#formatChoices .pick')
    .forEach(b => b.classList.toggle('sel', b.dataset.format === f));
  $('#formatChoices').classList.add('picked');   // retires the "choose one" prompt

  const openUnderChoice = () => {
    // The panel is authored once and MOVED, so there is exactly one name field, one set
    // of settings and one create button however many times the format changes.
    const row = document.querySelector(`#formatChoices .pick[data-format="${f}"]`);
    row.insertAdjacentElement('afterend', panel);
    drawFormatWheel();
    // Two frames: one for the browser to register the node at its new position with
    // grid-template-rows still 0fr, the next to flip it -- set in the same frame as the
    // move, the transition has no start value to run from and the panel simply appears.
    requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add('open')));
  };

  if (wasOpen){
    // Collapse under the old row before re-opening under the new one; moving a full-height
    // panel between rows without closing it teleports a 400px block up or down the page.
    panel.classList.remove('open');
    setTimeout(openUnderChoice, 300);
  } else {
    openUnderChoice();
  }
  if (first) revealIntoView();
}

function closeRoomConfig(){
  CHOSEN_FORMAT = null;
  document.querySelectorAll('#formatChoices .pick').forEach(b => b.classList.remove('sel'));
  $('#formatChoices').classList.remove('picked');   // the prompt comes back
  $('#roomConfig').classList.remove('open');
}

function revealIntoView(){
  const panel = $('#roomConfig');
  // After the transition, so the measurement is of the opened height rather than zero.
  setTimeout(() => {
    const bottom = panel.getBoundingClientRect().bottom;
    if (bottom > window.innerHeight){
      panel.scrollIntoView({behavior: 'smooth', block: 'end'});
    }
  }, 460);
}

// One filled marker -- the host is the first fielder set. The rest of the ring is what
// they still have to fill, which is the actual question this screen answers.
const WHEEL_NOTE = {
  final: 'seats · you and one more',
  cup: 'seats · you and three more',
  league: 'seats · a full field, you and nine',
};

function drawFormatWheel(){
  const wrap = $('#formatWheel');
  if (!wrap) return;
  // No format yet: an empty ground, no markers, no figure -- there is no room to count
  // the seats of. `fieldWheel(0, 0)` draws the rope, circle and pitch and nothing else.
  // The wheel lives inside the reveal now, so it is only ever drawn for a chosen format --
  // there is no empty-ground state left to render.
  if (!CHOSEN_FORMAT) return;
  const seats = FORMAT_SEATS[CHOSEN_FORMAT];
  wrap.innerHTML = fieldWheel(seats, 1);
  $('#wheelSeats').textContent = seats;
  $('#wheelNote').textContent = WHEEL_NOTE[CHOSEN_FORMAT];
}

// Create and join each have their own name field (they are separate panels), so every
// caller reads whichever one belongs to the visible panel rather than a single shared id.
function currentName(){
  const joining = !$('#roomJoinPanel').classList.contains('hide');
  const el = joining ? $('#roomNameJoin') : $('#roomName');
  return (el && el.value.trim()) || 'Player';
}
function pickTimer(t){
  CHOSEN_TIMER = t;
  document.querySelectorAll('#timerChoices .room-choice')
    .forEach(b => b.classList.toggle('sel', +b.dataset.timer === t));
}
function pickRoomDraftMode(mode){
  CHOSEN_ROOM_DRAFT_MODE = mode;
  document.querySelectorAll('#roomModeChoices .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
}
function pickVisibility(open){
  CHOSEN_VISIBILITY = open;
  document.querySelectorAll('#visibilityChoices .room-choice')
    .forEach(b => b.classList.toggle('sel', (b.dataset.visibility === 'open') === open));
}

// Two explicit tabs on the same screen rather than one long page -- the join tab needs
// room to show a live, polled list of open rooms, which a single combined form had no
// good place for.
function pickSetupTab(tab){
  document.querySelectorAll('#setupTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.tab === tab));
  $('#roomCreatePanel').classList.toggle('hide', tab !== 'create');
  $('#roomJoinPanel').classList.toggle('hide', tab !== 'join');
  // The strip under the title says what THIS tab is for -- it read "pick your format"
  // on the join tab, where there is no format to pick.
  $('#setupStripLeft').textContent = tab === 'join' ? 'Join a game' : 'Pick your format';
  if (tab === 'join'){
    roomListOpenRooms();
    if (ROOM_OPEN_POLL) clearInterval(ROOM_OPEN_POLL);
    ROOM_OPEN_POLL = setInterval(roomListOpenRooms, 4000);
  } else {
    stopRoomOpenPoll();
  }
}
function stopRoomOpenPoll(){
  if (ROOM_OPEN_POLL){ clearInterval(ROOM_OPEN_POLL); ROOM_OPEN_POLL = null; }
}

const ROOM_FORMAT_LABEL = {final: 'Final · 2', cup: 'Cup · 4', league: 'League · 10'};

// A public, anonymous browse list (A62 -- no accounts) -- fetched fresh each poll rather
// than reusing a room's own polling machinery, since there is no room yet to poll.
async function roomListOpenRooms(){
  const list = $('#openRoomsList');
  if (!list) return;
  try {
    const rows = await api('/api/rooms/open');
    const count = $('#openRoomsCount');
    // A live count with a pulsing dot, the same signal a lobby anywhere else gives -- it
    // is the one genuinely changing number on this screen, so it earns the accent.
    if (count) count.innerHTML = rows.length
      ? `<span class="live">${rows.length} live</span>` : 'None right now';
    if (!rows.length){
      list.innerHTML = '<p class="room-note">No open rooms right now — ask your host for a code.</p>';
      return;
    }
    // The mini wheel IS the seat count -- how full a room is reads faster as a part-set
    // field than as "3 of 4", and it ties the list to the picker above it.
    list.innerHTML = rows.map(r => `
      <div class="room-row">
        <span style="display:flex;align-items:center;gap:14px">
          ${fieldWheel(r.seats_total, r.seats_filled, {mini: true})}
          <span>
            <span class="rr-name">${ROOM_FORMAT_LABEL[r.format] || r.format}</span>
            <span class="rr-meta">${r.seats_filled} of ${r.seats_total} seats · ${r.timer_seconds}s
              · ${r.host_name}'s room</span>
          </span>
        </span>
        <button class="act" onclick="joinOpenRoom('${r.code}', this)">Enter →</button>
      </div>`).join('');
  } catch(e){ /* a transient poll failure isn't worth interrupting the user over */ }
}

async function joinOpenRoom(code, ctrl){
  const name = currentName();
  await busyClick(ctrl, 'Joining…', async () => {
    try {
      const r = await api(`/api/rooms/${code}/join`, {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})});
      enterRoom(code, r.player_id);
    } catch(e){ slip(e.message); }
  });
}

async function createRoom(ctrl){
  // The button lives inside the panel the format choice opens, so this is unreachable in
  // practice -- checked anyway rather than trusting a UI state to hold an invariant the
  // request depends on.
  if (!CHOSEN_FORMAT){ slip('Pick a format first.'); return; }
  const name = currentName();
  await busyClick(ctrl, 'Creating…', async () => {
    try {
      const r = await api('/api/rooms', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({format: CHOSEN_FORMAT, timer_seconds: CHOSEN_TIMER,
          host_name: name, draft_mode: CHOSEN_ROOM_DRAFT_MODE, is_open: CHOSEN_VISIBILITY})});
      enterRoom(r.room.code, r.player_id);
    } catch(e){ slip(e.message); }
  });
}

async function joinRoom(ctrl){
  const code = $('#joinCode').value.trim().toUpperCase();
  const name = currentName();
  if (!code){ slip('Enter a room code.'); return; }
  await busyClick(ctrl, 'Joining…', async () => {
    try {
      const r = await api(`/api/rooms/${code}/join`, {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})});
      enterRoom(code, r.player_id);
    } catch(e){ slip(e.message); }
  });
}

// localStorage, not the URL -- `player_id` must never sit in a shareable link (anyone
// with the link could act as you if it did). room.html reads this same key to resume.
const ROOM_STORAGE_KEY = 'iplegends_room';

function saveRoomSession(code, playerId){
  try { localStorage.setItem(ROOM_STORAGE_KEY, JSON.stringify({code, playerId})); }
  catch(e){ /* private browsing, quota, disabled storage -- resuming just won't work */ }
}

function enterRoom(code, playerId){
  stopRoomOpenPoll();
  saveRoomSession(code, playerId);
  location.href = '/rooms/' + code;
}

async function boot(){
  // Still fire-and-forget for the auth control itself, but chained here too: a signed-in
  // visitor's own name field starts filled with their username rather than blank, since
  // that's who they almost always want to play as. Only when the field is still empty --
  // never overwrites something already typed -- and left editable either way.
  loadMe().then(() => {
    if (!ME || !ME.username) return;
    // Both panels' name fields, since either could be the one the visitor lands on.
    for (const id of ['#roomName', '#roomNameJoin']){
      const el = $(id);
      if (el && !el.value.trim()) el.value = ME.username;
    }
  });
  const m = await loadMeta();
  const s = m.seasons;
  $('#deckPill').textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  $('#footStats').textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
}

boot().then(() => {
  drawFormatWheel();
  // A room.html redirect here (no matching localStorage session, or a verify failure)
  // carries the code it couldn't resolve so the join tab can be pre-filled instead of
  // making the visitor retype it.
  const code = new URLSearchParams(location.search).get('code');
  if (code){
    pickSetupTab('join');
    $('#joinCode').value = code.toUpperCase();
  }
});
