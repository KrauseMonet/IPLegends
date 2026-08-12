// The room create/join screen. A successful create or join saves {code, playerId} to
// localStorage and navigates to /rooms/{code}, which does the actual live session --
// this page never shows a lobby or a draft itself.

let CHOSEN_FORMAT = 'final', CHOSEN_TIMER = 30, CHOSEN_ROOM_DRAFT_MODE = 'stat';
let CHOSEN_VISIBILITY = false;   // is_open -- closed (false) is the default, A95
let ROOM_OPEN_POLL = null;       // polls GET /api/rooms/open while the join tab is showing

function pickFormat(f){
  CHOSEN_FORMAT = f;
  document.querySelectorAll('#formatChoices .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.format === f));
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
    if (!rows.length){
      list.innerHTML = '<p class="room-note">No open rooms right now — ask your host for a code.</p>';
      return;
    }
    list.innerHTML = rows.map(r => `
      <div class="entry">
        <span class="nm">${ROOM_FORMAT_LABEL[r.format] || r.format}
          <em style="font-style:normal">· ${r.seats_filled} of ${r.seats_total} · ${r.timer_seconds}s
          · ${r.host_name}'s room</em></span>
        <span class="picks"><button class="act" onclick="joinOpenRoom('${r.code}', this)">Join</button></span>
      </div>`).join('');
  } catch(e){ /* a transient poll failure isn't worth interrupting the user over */ }
}

async function joinOpenRoom(code, ctrl){
  const name = $('#roomName').value.trim() || 'Player';
  await busyClick(ctrl, 'Joining…', async () => {
    try {
      const r = await api(`/api/rooms/${code}/join`, {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})});
      enterRoom(code, r.player_id);
    } catch(e){ slip(e.message); }
  });
}

async function createRoom(ctrl){
  const name = $('#roomName').value.trim() || 'Player';
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
  const name = $('#roomName').value.trim() || 'Player';
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
  loadMe();   // fire-and-forget -- the auth control fills in whenever it resolves
  const m = await loadMeta();
  const s = m.seasons;
  $('#deckPill').textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  $('#footStats').textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
}

boot().then(() => {
  // A room.html redirect here (no matching localStorage session, or a verify failure)
  // carries the code it couldn't resolve so the join tab can be pre-filled instead of
  // making the visitor retype it.
  const code = new URLSearchParams(location.search).get('code');
  if (code){
    pickSetupTab('join');
    $('#joinCode').value = code.toUpperCase();
  }
});
