// The live room page: lobby, live draft, match reveal, result. Reached only by
// navigation from /rooms (create/join, which saves {code, playerId} to localStorage
// then sends the browser here) or a reload/bookmark of this page's own URL, in which
// case this page's own boot (bottom of file) verifies the saved session against the
// URL's own room code before resuming anything.

/* --- a room's own completed match: renderScorecard/copyRoomCode's data source --- */
let ROOM_MATCH_DATA = null;

function showRoomScorecard(i){
  renderScorecard(ROOM_MATCH_DATA.results[i].result);
}

function copyRoomCode(){
  navigator.clipboard.writeText(ROOM_CODE)
    .then(() => slip('Copied — share this code with friends.'))
    .catch(() => slip(ROOM_CODE));
}

let ROOM_CODE = null, MY_PID = null, ROOM = null, ROOM_POLL = null, ROOM_PENDING = null;
// The per-pick countdown ticks locally once a second between polls, instead of only
// updating (and visibly jumping by 2) whenever a poll response lands. ROOM_TIMER_BASE
// is the last server-authoritative reading -- {value, max, at} -- and every poll
// refreshes it; ROOM_TIMER_TICK renders the extrapolated value every second in between.
let ROOM_TIMER_TICK = null, ROOM_TIMER_BASE = null;
// Whose squad the "Batting order" column shows during someone else's turn: false = the
// active player's (the default), true = my own. Resets whenever the active player changes
// so a stale choice doesn't linger into the next person's turn.
let ROOM_VIEW_MINE = false, ROOM_VIEW_LAST_ACTIVE = null;
// Match phase: ROOM_REVEAL_ACTIVE is the current_matches entry we're animating for the
// viewer right now (or null) -- while set, incoming polls are ignored for rendering so
// a live over-by-over stepper is never restarted out from under the viewer; the fresh
// data still lands in ROOM_MATCH_DATA and is read the moment the reveal finishes.
// ROOM_REVEALED_STAGE remembers the last stage we've already animated, so a fixture is
// never re-revealed on a later poll. ROOM_SPECTATE_SHOWN gates the one-time "your run
// is over" choice screen. ROOM_LEAGUE_REVEALED_THROUGH is ROOM_REVEALED_STAGE's own
// analogue for a league room's round-robin, which needs a COUNT rather than a stage
// name -- every one of its fixtures shares the literal stage "league", not a unique
// label like "Semi-final 1". All four reset per room in enterRoom.
let ROOM_REVEAL_ACTIVE = null, ROOM_REVEALED_STAGE = null, ROOM_SPECTATE_SHOWN = false;
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
  for (const s of ['room', 'reveal']) $('#' + s).classList.toggle('hide', s !== id);
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

function enterRoom(code, playerId){
  saveRoomSession(code, playerId);
  ROOM_CODE = code; MY_PID = playerId; ROOM_PENDING = null;
  ROOM_REVEAL_ACTIVE = null; ROOM_REVEALED_STAGE = null; ROOM_SPECTATE_SHOWN = false;
  ROOM_LEAGUE_REVEALED_THROUGH = 0;
  go('room');
  pollRoom();
  if (ROOM_POLL) clearInterval(ROOM_POLL);
  ROOM_POLL = setInterval(pollRoom, 2000);
  if (ROOM_TIMER_TICK) clearInterval(ROOM_TIMER_TICK);
  ROOM_TIMER_TICK = setInterval(tickRoomTimer, 1000);
}

async function pollRoom(){
  if (!ROOM_CODE) return;
  const myGen = ROOM_GEN;   // captured, never bumped -- a poll never competes with another poll
  try {
    // player_id identifies the caller so the server knows whose options (if anyone's)
    // to include -- only the currently active seat's own caller ever sees them.
    const room = await api('/api/rooms/' + ROOM_CODE + '?player_id=' + encodeURIComponent(MY_PID));
    if (myGen !== ROOM_GEN) return;   // a mutation started after this poll was issued
    ROOM = room;
    // Resync the local ticker to the server's own reading here, on the poll that
    // actually fetched it -- never inside renderRoomDraft, which also re-runs off the
    // same cached ROOM object (toggleRoomView) and would otherwise snap the countdown
    // back to a stale value on every such re-render instead of continuing smoothly.
    if (room.status === 'drafting'){
      ROOM_TIMER_BASE = {value: room.seconds_remaining, max: room.timer_seconds || 30, at: performance.now()};
    }
    if (ROOM.status === 'complete'){
      // The match phase keeps polling on the SAME interval as the draft -- a toss
      // winner, or the host advancing, needs every other seat's own screen to pick the
      // change up without a manual refresh.
      const m = await api(`/api/rooms/${ROOM_CODE}/match?player_id=${encodeURIComponent(MY_PID)}`);
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
    }
    renderRoom();
  } catch(e){ /* a transient poll failure isn't worth interrupting the user over */ }
}

async function startRoomDraft(ctrl){
  await busyClick(ctrl, 'Starting…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await api(`/api/rooms/${ROOM_CODE}/start`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({player_id: MY_PID})});
      if (myGen !== ROOM_GEN) return;
      ROOM = room;
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
  ROOM_PENDING = (ROOM_PENDING && ROOM_PENDING.index === i) ? null : {index: i, card};
  renderRoom();
}

function roomRowClick(slot, ctrl){
  if (!ROOM_PENDING) return;
  roomSubmitPick(ROOM_PENDING.index, slot, ctrl);
}

async function roomSubmitPick(index, slot, ctrl){
  await busyClick(ctrl, 'Taking…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await api(`/api/rooms/${ROOM_CODE}/pick`, {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID, index, slot})});
      if (myGen !== ROOM_GEN) return;
      ROOM = room;
      ROOM_PENDING = null;
      renderRoom();
    } catch(e){ slip(e.message); }
  });
}

function renderRoom(){
  const r = ROOM;
  if (!r) return;
  $('#roomLobby').classList.toggle('hide', r.status !== 'lobby');
  $('#roomDraft').classList.toggle('hide', r.status !== 'drafting');
  $('#roomResult').classList.toggle('hide', r.status !== 'complete');
  $('#roomFailed').classList.toggle('hide', r.status !== 'failed');

  if (r.status === 'lobby') renderRoomLobby(r);
  else if (r.status === 'drafting') renderRoomDraft(r);
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
  $('#lobbyCode').textContent = r.code;
  $('#lobbySeats').textContent = `${r.players.length} of ${r.seats}`;
  $('#lobbyFormat').textContent = r.format.toUpperCase();
  $('#lobbyTimer').textContent = r.timer_seconds + 's per pick';
  $('#lobbyMode').textContent = r.draft_mode === 'memory' ? 'Memory' : 'Stat';
  const amHost = MY_PID === r.host_id;
  $('#lobbyPlayers').innerHTML = r.players.map(p => {
    const isHost = p.player_id === r.host_id;
    // Only the host sees this, and never against their own seat -- rooms.kick_player
    // refuses both cases too, this just keeps the button from ever being offered.
    const kickBtn = (amHost && !isHost)
      ? `<span class="picks"><button class="act" onclick="kickRoomPlayer('${p.player_id}', this)"
           >Kick</button></span>` : '';
    return `<div class="entry"><span class="nm">${p.name}${isHost
      ? ' <em style="color:var(--gold);font-style:normal">· host</em>' : ''}</span>${kickBtn}</div>`;
  }).join('');
  $('#lobbyStartBtn').classList.toggle('hide', !amHost);
}

async function kickRoomPlayer(targetId, ctrl){
  await busyClick(ctrl, 'Kicking…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await api(`/api/rooms/${ROOM_CODE}/kick`, {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({player_id: MY_PID, target_id: targetId})});
      if (myGen !== ROOM_GEN) return;
      ROOM = room;
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
      rollDeal($('#roomDealYear'), $('#roomDealTeam'), deal.season_year, deal.franchise);
    }
  } else {
    LAST_ROOM_DEAL_FS = null;
    clearTimeout(ROLL_TIMERS.get($('#roomDealTeam')));
    $('#roomDealYear').textContent = '';
    $('#roomDealTeam').textContent = fallbackText;
  }
}

// Renders the countdown purely from ROOM_TIMER_BASE, extrapolated by wall-clock time
// since it was last set from a real server reading -- called once a second by
// ROOM_TIMER_TICK, and once more immediately whenever a fresh poll lands (via
// renderRoomDraft) so a resync is never left waiting up to a second to appear.
function tickRoomTimer(){
  if (!ROOM_TIMER_BASE) return;
  const el = $('#roomTimer');
  if (!el) return;
  const {value, max, at} = ROOM_TIMER_BASE;
  const elapsed = (performance.now() - at) / 1000;
  const remaining = Math.max(0, Math.round(value - elapsed));
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
  $('#roomRoundNow').textContent = `round ${r.round + 1}/${r.rounds_total}`;
  tickRoomTimer();
  $('#roomCodeStamp').textContent = 'room ' + r.code;

  if (amActive){
    // My turn: the deal (with options) is mine, and my own order sheet is what's
    // useful to see while I decide -- unchanged from before the turn-based redesign.
    $('#roomDealLabel').textContent = 'Dealt to you';
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

    $('#roomDealLabel').textContent = active ? `${active.name}'s turn` : 'Waiting…';
    $('#roomOptionsLabel').textContent = '';
    $('#roomOrderLabel').textContent = showMine ? 'Your team so far'
      : (active ? `${active.name}'s team so far` : 'Batting order');
    showRoomDeal(active && active.deal, r.status === 'complete' ? 'Draft complete' : '—');
    $('#roomOptions').innerHTML = '<div class="note">'
      + (active ? `${active.name} is choosing -- options stay hidden until it's their turn to show.`
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
      <span class="who">${p.name}</span>
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
    const them = r.you_home ? r.away : r.home;
    const mineScore = r.you_home ? r.home_score : r.away_score;
    const theirsScore = r.you_home ? r.away_score : r.home_score;
    const k = r.winner === myShort ? 'w' : (r.winner === null ? '' : 'l');
    if (k === 'w') won++; else if (k === 'l') lost++;
    return `<div class="fx" onclick="showRoomScorecard(${i})">
      <span class="wl ${k}">${k ? k.toUpperCase() : 'T'}</span>
      <span>v ${them}</span><span class="sc">${mineScore} · ${theirsScore}</span></div>`;
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
        <span>${r.home} v ${r.away}</span>
        <span class="sc">${r.home_score} · ${r.away_score}</span></div>
      <div class="fx" style="border:0;padding-top:2px"><span></span>
        <span class="sc" style="font-style:italic">${r.margin}</span><span></span></div>`;
  }).join('');
}

function roomTableHtml(m){
  // Shared by the in-progress view and the completed one -- `m.table` is populated by
  // the backend from the moment the round-robin settles all the way through the
  // playoffs (RoomMatchOut.table's own doc comment), not only once complete, so this
  // markup has to work identically in both places rather than living only in one.
  if (!m.table) return '';
  return `<table class="standings">
    <thead><tr><th>#</th><th>Side</th><th class="n">P</th><th class="n">W</th><th class="n">L</th>
        <th class="n">Pts</th><th class="n">NRR</th></tr></thead>
    <tbody>
    ${m.table.map(row => `<tr class="${row.you ? 'you' : ''} ${row.pos === 4 ? 'cut' : ''}">
      <td class="n" style="text-align:left">${row.pos}</td>
      <td>${teamBadge(row.short, row.you)}${row.name}${row.you ? '<span class="you-pill">you</span>' : ''}</td>
      <td class="n">${row.played}</td><td class="n">${row.won}</td><td class="n">${row.lost}</td>
      <td class="n pts">${row.points}</td>
      ${nrrCell(row.nrr)}</tr>`).join('')}
    </tbody>
  </table>`;
}

function roomWaitingHtml(m, myMatch){
  // A league room's own group stage: paced one fixture at a time (or all at once via
  // Skip ahead), mutually exclusive with the knockout-round logic below it since this
  // phase always has advance_ready=false -- the round-robin never has anyone's toss to
  // wait on, only the host's own pacing.
  if (m.league_revealed != null && m.league_revealed < m.league_total){
    const actions = m.you_decide_league_reveal
      ? `<div class="foot-actions room-actions">
           <button class="act lead" onclick="roomLeagueAdvance(${m.league_revealed + 1}, this)">Continue</button>
           <button class="act" onclick="roomLeagueAdvance(${m.league_total}, this)">Skip ahead</button>
         </div>`
      : `<div class="margin">Waiting for the host to continue…</div>`;
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
    return `<div class="fx">
      <span>${cm.stage}${isMine ? ' (you)' : ''}</span>
      <span>${cm.a_name} v ${cm.b_name}</span>
      <span class="sc">${waitingOn} to call the toss</span>
    </div>`;
  }).join('');

  const advanceHtml = m.advance_ready
    ? (m.you_decide_advance
        ? `<div class="foot-actions room-actions">
             <button class="act lead" onclick="roomAdvanceMatch(this)">Continue</button>
             <button class="act" onclick="roomSkipAhead(this)">Skip ahead</button>
           </div>`
        : `<div class="margin">Waiting for the host to continue…</div>`)
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

// Finds the viewer's own fixture that still needs its reveal played, or null if there
// isn't one. `current_matches` covers every ordinary case (toss still pending, or
// resolved but not yet advanced past) -- but the tournament's very LAST fixture in
// every format (`replay_room_matches`'s final branch for 'final'/'cup'/'league' alike)
// skips the paused, advance-gated stopover every earlier round gets and jumps straight
// to `complete`, emptying `current_matches` in the same response that resolved it. Without
// this fallback that fixture's scoreline would appear for both sides with no reveal at
// all -- `results` still carries it, with full innings data, so it's read from there
// instead once it's no longer in `current_matches`. Shared by showRoomMatch (the polling
// path, for whichever side didn't call the toss) and roomSubmitTossReveal (the side that
// did), so both agree on where a fixture's result can still be found.
function roomMyMatchToReveal(m){
  // While the league group stage is still being revealed, every one of its seventy
  // fixtures shares the literal stage string "league" (ROOM_LEAGUE_REVEALED_THROUGH's
  // own comment has the why), so the `ROOM_REVEALED_STAGE !== last.stage` check below
  // can only ever fire once for the viewer's OWN first league fixture and then goes
  // permanently inert -- it cannot tell two different league fixtures apart. The
  // dedicated league branch in showRoomMatch (ROOM_LEAGUE_REVEALED_THROUGH, a real
  // counter) is what actually paces that phase; this function must sit out entirely
  // while it's running rather than risk firing on a stale cached fixture.
  if (m.league_revealed != null && m.league_revealed < m.league_total) return null;
  const cur = m.current_matches.find(cm => cm.a_pid === MY_PID || cm.b_pid === MY_PID);
  if (cur && cur.result === null && cur.you_decide_toss) return cur;
  if (cur && cur.result && ROOM_REVEALED_STAGE !== cur.stage) return cur;
  if (cur) return null;   // it's mine but still waiting on someone else's toss
  const mine = m.results.filter(e => e.result.yours);
  const last = mine[mine.length - 1];
  return (last && ROOM_REVEALED_STAGE !== last.stage) ? last : null;
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
  $('#tossOpponent').textContent = `${myMatch.a_name} v ${myMatch.b_name} -- elect to bat or bowl first.`;
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
      const fresh = roomMyMatchToReveal(m);
      if (fresh && fresh.stage === stage) roomStartReveal(fresh);
      else { ROOM_REVEAL_ACTIVE = null; go('room'); showRoomMatch(m); }
    } catch(e){ slip(e.message); }
  });
}

function roomStartReveal(myMatch){
  ROOM_REVEAL_ACTIVE = myMatch;
  const r = myMatch.result;
  const steps = [];
  if (r.home_innings) steps.push([r.home_innings, `${myMatch.stage} · ${r.home} batting`, null]);
  // Built from r.home_innings/r.home directly, not steps[0] -- home always bats first in
  // this engine, but indexing into steps would attach the wrong innings as "prior" in the
  // (currently unreached) case where home_innings is ever absent.
  if (r.away_innings) steps.push([r.away_innings, `${myMatch.stage} · ${r.away} batting`,
    r.home_innings ? { innings: r.home_innings, battingLabel: r.home } : null]);
  let i = 0;
  (function next(){
    if (i >= steps.length){ roomFinishReveal(myMatch); return; }
    const [innings, label, priorContext] = steps[i++];
    startOverStepper(innings, label, next, priorContext);
  })();
}

function roomFinishReveal(myMatch){
  ROOM_REVEAL_ACTIVE = null;
  ROOM_REVEALED_STAGE = myMatch.stage;
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
  el.innerHTML = `<div class="report compact">
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
  if (ROOM_REVEAL_ACTIVE) return;

  // Every seat reviews its own finished twelve -- and the three ratings that come with
  // it -- before a single ball is bowled. Nothing below this point (not even a first
  // toss) has been resolved yet while this holds, mirroring how a league's group-stage
  // reveal is checked ahead of the ordinary match-waiting branches below it.
  if (m.awaiting_start){ renderRoomStartReview(m); return; }

  // A league room's group-stage reveal: checked before roomMyMatchToReveal, since a
  // round-robin fixture isn't participant-scoped the way a knockout fixture is -- the
  // shared pacing cursor (host-driven Continue/Skip ahead, same mechanism and same
  // performance profile as before) walks every viewer through the same seventy
  // fixtures together, but the ANIMATION is personal: only a viewer who actually played
  // in the fixture the cursor just landed on gets the ball-by-ball reveal for it.
  // Everyone else just watches the table tick up. `result.yours` already exists
  // per-caller on every result (`_room_result_out`).
  // ROOM_LEAGUE_REVEALED_THROUGH (not ROOM_REVEALED_STAGE) tracks how far THIS client
  // has already watched, since every round-robin entry shares the literal stage
  // "league" and can't be told apart by name the way "Semi-final 1" can.
  if (m.league_revealed != null && m.league_next_result &&
      m.league_revealed > ROOM_LEAGUE_REVEALED_THROUGH){
    ROOM_LEAGUE_REVEALED_THROUGH = m.league_revealed;
    if (m.league_next_result.result.yours){
      roomEnterReveal(m.league_next_result);   // the exact same playoff reveal engine, unmodified
      return;
    }
    // Not the viewer's own fixture -- fall through to the ordinary shared waiting
    // view (table + progress + Continue/Skip ahead) below instead of forcing them
    // through a match they have no stake in. roomMyMatchToReveal is safely inert
    // during this phase (its own guard, above), so falling through here cannot
    // misfire into an unrelated animation.
  }

  // Checked BEFORE `m.complete`, not after: the tournament's very last fixture can
  // resolve and complete the whole room in the same response (roomMyMatchToReveal's own
  // comment has the why), so a straight `if (m.complete)` check here would show that
  // fixture's scoreline with no reveal at all, for both sides.
  const toReveal = roomMyMatchToReveal(m);
  if (toReveal){ roomEnterReveal(toReveal); return; }

  if (m.complete){ showRoomMatchComplete(m); return; }

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
    return;
  }

  el.innerHTML = roomWaitingHtml(m, myMatch) + gap;
}

function showRoomMatchComplete(m){
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
      <div class="call ${res.winner ? 'won' : ''}">${res.winner ? res.winner + ' win' : 'Tied'}</div>
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
      <div class="call won room-banner">${m.champion} win the cup</div>
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
  el.innerHTML = `<div class="report compact">
      <div class="call won room-banner">${m.champion} win the league</div>
    </div>
    <div class="season">
      <div>
        <div class="panel-glow"></div>
        <div class="panel-head"><h3>League table</h3>
          <div class="stat"><b>${m.table.length}</b><span>teams</span></div></div>
        ${roomTableHtml(m)}
        ${capRowHtml(m.orange_cap, m.orange_cap_runs, m.purple_cap, m.purple_cap_wickets)}
      </div>
      <div>
        <div class="panel-glow"></div>
        ${your.html ? `<div class="panel-head"><h3>Your matches</h3>
          <div class="stat"><b>${your.won} won</b><span>${your.lost} lost</span></div></div>${your.html}` : ''}
        ${playoffsHtml ? `<div class="col-head" style="margin:26px -18px 0">
          <span>🏆 Playoffs</span></div>${playoffsHtml}` : ''}
      </div>
    </div>
    <div class="foot-actions room-actions">${playAgainBtn}${journeyBtn}</div>`;
}

async function roomPlayAgain(ctrl){
  await busyClick(ctrl, 'Resetting…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      const room = await api(`/api/rooms/${ROOM_CODE}/play-again`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({player_id: MY_PID})});
      if (myGen !== ROOM_GEN) return;
      ROOM = room;
      // A fresh lobby has no match of its own yet -- cleared explicitly rather than
      // left stale, so a later completion can never be masked by this game's leftovers
      // (renderRoomResult's own "if ROOM_MATCH_DATA, skip the fetch" shortcut would
      // otherwise show the game just finished instead of the new one).
      ROOM_MATCH_DATA = null;
      ROOM_PENDING = null; ROOM_REVEAL_ACTIVE = null; ROOM_REVEALED_STAGE = null;
      ROOM_SPECTATE_SHOWN = false; ROOM_LEAGUE_REVEALED_THROUGH = 0;
      renderRoom();
    } catch(e){ slip(e.message); }
  });
}

function showRoomJourneyCard(){
  // ROOM_MATCH_DATA already carries the journey card's own numbers once complete --
  // no second fetch, same reasoning as solo's own showJourneyCard.
  if (!ROOM_MATCH_DATA || !ROOM_MATCH_DATA.squad){ slip('Play the match first.'); return; }
  drawJourneyCard(ROOM_MATCH_DATA, 'ROOM ' + ROOM_CODE);
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

// `through` is the TARGET cursor, not an increment -- Continue sends revealed + 1,
// Skip ahead sends the group stage's own total, so this is one round trip either way
// rather than a client-side loop (unlike roomSkipAhead, which loops a handful of ROUND
// advances -- looping 70 times here would be both slower and pointless, since the
// server already knows how to jump straight to any cursor in one call).
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

// Bulk-advances through as many already-fully-resolved rounds as the server will allow
// in one go -- never resolves anyone's own toss for them (that stays a real, live
// decision), it only removes the repeated "Continue" clicks a room otherwise needs once
// per round that turned out to have nothing left to wait on.
async function roomSkipAhead(ctrl){
  await busyClick(ctrl, 'Skipping ahead…', async () => {
    const myGen = ++ROOM_GEN;
    try {
      let m = ROOM_MATCH_DATA, guard = 0;
      while (!m.complete && m.advance_ready && m.you_decide_advance && guard++ < 10){
        m = await api(`/api/rooms/${ROOM_CODE}/match/advance`, {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({player_id: MY_PID})});
      }
      if (myGen !== ROOM_GEN) return;
      ROOM_MATCH_DATA = m;
      showRoomMatch(m);
    } catch(e){ slip(e.message); }
  });
}

async function boot(){
  const m = await loadMeta();
  const s = m.seasons;
  $('#deckPill').textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  $('#footStats').textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
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
