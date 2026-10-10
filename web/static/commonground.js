// Common Ground: two players, four minutes, name everyone who played with both. [A188]
//
// Same shape as Bingo and Guess the Player: the server owns the rules, the moves so far are the
// whole state, and each request sends them and gets the whole position back. What `puzzle.js`
// provides -- storage, streaks, name search, sharing -- is not repeated here.
//
// Two ways to play, and the difference is whose clock counts:
//   * Practice, and the daily while signed out: the page's. It starts when the player presses
//     Start, is kept as a wall-clock timestamp (so a reload resumes the same countdown rather
//     than restarting it), and every guess carries the milliseconds since then. Never ranked.
//   * The daily while signed in [A188]: the SERVER's. Start stamps the database's clock, every
//     guess is stamped on arrival, and the page only displays a countdown built from the
//     time-left each response reports -- re-synced on every one, so it cannot drift. Ranked.

const CG_KEY = 'iplegends_cground_v1';

let CG = {mode: 'daily', puzzle: null, started: null, moves: [], ended: null, state: null,
          matches: [], active: 0, busy: false, timer: null, ranked: false, rank: null, of: 0};

const cgStore = () => pzStore(CG_KEY);
const cgSave = patch => pzSave(CG_KEY, patch);

const cgClock = ms => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const cgLimitMs = () => CG.puzzle.limit_seconds * 1000;
const cgElapsed = () => Math.min(Math.max(0, Date.now() - CG.started), cgLimitMs());

function cgPersist(){
  const p = CG.puzzle;
  const rec = {started: CG.started, moves: CG.moves, ended: CG.ended};
  if (CG.mode === 'daily') cgSave({daily: Object.assign({date: p.date}, rec)});
  else cgSave({practice: Object.assign({seed: p.seed}, rec)});
}

function cgPaintStreak(){
  const best = cgStore().best;
  const streak = CG.mode === 'daily' && CG.puzzle
    ? pzStreakHtml(cgStore().days || [], CG.puzzle.date, 'pair') : '';
  $('#cgStreak').innerHTML = [streak, best ? `Your best score: <b>${best}</b>.` : '']
    .filter(Boolean).join(' ');
}

// --- loading ------------------------------------------------------------------------------

async function cgFetchPuzzle(mode, fresh){
  if (mode === 'daily') return api('/api/ground/today');
  const urlSeed = new URLSearchParams(location.search).get('seed');
  const saved = cgStore().practice;
  const seed = !fresh && urlSeed ? urlSeed : (!fresh && saved ? saved.seed : null);
  return api('/api/ground' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

async function cgMode(mode, fresh){
  if (CG.busy) return;
  cgStopTick();
  CG.mode = mode;
  document.querySelectorAll('#cgTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
  try {
    const puzzle = await cgFetchPuzzle(mode, fresh);
    await cgLoad(puzzle, fresh);
    history.replaceState(null, '', mode === 'daily' ? '/common' : '/common?seed=' + puzzle.seed);
  } catch(e){ slip(e.message); }
}

async function cgLoad(puzzle, fresh){
  CG.puzzle = puzzle;
  // A signed-out finish shows every answer, and the pair is the same for everybody, so a browser
  // that has already finished today's pair signed out is not offered a ranked attempt at it:
  // the second look would be a look at the answers. (Honesty still has to carry the rest -- a
  // friend can say the answers aloud -- but this stops the accidental route.)
  CG.peeked = CG.mode === 'daily' && cgStore().anonDone === puzzle.date;
  CG.signedIn = await pzSignedIn();
  CG.ranked = CG.mode === 'daily' && CG.signedIn && !CG.peeked;
  CG.rank = null; CG.of = 0; CG.points = 0; CG.bonus = 0; CG.rarity = null;
  if (CG.ranked){ await cgLoadRanked(); return; }
  const s = cgStore();
  const saved = CG.mode === 'daily'
    ? (s.daily && s.daily.date === puzzle.date ? s.daily : null)
    : (!fresh && s.practice && String(s.practice.seed) === String(puzzle.seed) ? s.practice : null);
  CG.started = saved ? saved.started || null : null;
  CG.moves = saved ? saved.moves || [] : [];
  CG.ended = saved ? saved.ended || null : null;
  CG.state = null;
  $('#cgDone').classList.add('hide');
  if (CG.started){
    // The clock may have run out while the page was closed: that attempt is over, and the
    // server is told it ended at the limit rather than at the (later) moment of this reload.
    if (!CG.ended && Date.now() - CG.started >= cgLimitMs()) CG.ended = {t: cgLimitMs()};
    try { CG.state = await cgReplay(); }
    catch(e){ CG.started = null; CG.moves = []; CG.ended = null; }   // a list the server refuses is dropped
  }
  cgPersist();
  cgRender();
  if (CG.state && CG.state.finished) cgFinish(false);
  else if (CG.started) cgStartTick();
}

// The signed-in daily: the server holds the attempt, so there is nothing to restore from this
// browser -- ask it where this player stands.
async function cgLoadRanked(){
  CG.started = null; CG.moves = []; CG.ended = null; CG.state = null;
  $('#cgDone').classList.add('hide');
  try { cgApplyRanked(await api('/api/ground/ranked')); }
  catch(e){ slip(e.message); }
  cgRender();
  if (CG.state && CG.state.finished) cgFinish(false);
  else if (CG.started) cgStartTick();
}

// Take the server's reading of the attempt: the position, and how much of the clock is left,
// from which the page's own start time is rebuilt (so the countdown is the server's).
function cgApplyRanked(r){
  CG.rank = r.rank; CG.of = r.of; CG.points = r.points; CG.bonus = r.bonus; CG.rarity = r.rarity;
  if (!r.started){ CG.started = null; CG.state = null; CG.moves = []; return; }
  CG.state = r.state;
  CG.started = Date.now() - (cgLimitMs() - r.remaining_ms);
  CG.moves = r.state.found.map(f => ({id: f.person_id, t: f.t || 0}))
    .concat(r.state.wrong.map(w => ({id: w.person_id, t: w.t}))).sort((a, b) => a.t - b.t);
  CG.ended = r.finished ? {t: 0} : null;
}

async function cgPost(extra){
  return api('/api/ground/play', {method: 'POST', retry: true, headers: PZ_JSON,
    body: JSON.stringify(Object.assign({seed: CG.puzzle.seed, guesses: CG.moves}, extra))});
}

async function cgReplay(){
  const r = await cgPost(CG.ended ? {end: true, end_t: CG.ended.t} : {});
  return r.state;
}

// --- the clock ----------------------------------------------------------------------------

function cgStopTick(){ if (CG.timer){ clearInterval(CG.timer); CG.timer = null; } }

function cgStartTick(){
  cgStopTick();
  cgTick();
  CG.timer = setInterval(cgTick, 200);
}

function cgTick(){
  if (!CG.started || (CG.state && CG.state.finished)) { cgStopTick(); return; }
  const left = cgLimitMs() - (Date.now() - CG.started);
  $('#cgTime').textContent = cgClock(left);
  $('#cgBar').style.width = Math.max(0, Math.min(100, left / cgLimitMs() * 100)) + '%';
  $('#cgClock').classList.toggle('urgent', left <= 30000);
  if (left <= 0) cgTimeUp();
}

async function cgTimeUp(){
  cgStopTick();
  if (CG.busy || (CG.state && CG.state.finished)) return;
  CG.ended = {t: cgLimitMs()};
  await cgEnd();
}

async function cgGiveUp(){
  if (CG.busy || !CG.started || (CG.state && CG.state.finished)) return;
  cgStopTick();
  CG.ended = {t: cgElapsed()};
  await cgEnd();
}

async function cgEnd(){
  CG.busy = true;
  try {
    if (CG.ranked){
      const r = await api('/api/ground/ranked/play', {method: 'POST', retry: true, headers: PZ_JSON,
        body: JSON.stringify({end: true})});
      cgApplyRanked(r); cgRender(); cgFinish(true);
      return;
    }
    CG.state = await cgReplay();
    cgPersist(); cgRender(); cgFinish(true);
  } catch(e){ CG.ended = null; slip(e.message); if (CG.started) cgStartTick(); }
  finally { CG.busy = false; }
}

// --- rendering ----------------------------------------------------------------------------

function cgCrests(urls){
  return urls.map(u => `<img class="gp-club" src="${esc(u)}" alt="" width="22" height="22">`).join('');
}

function cgPlayerHtml(p){
  return `<b>${esc(p.name)}</b><span class="gp-clubs">${cgCrests(p.crests)}</span>`;
}

function cgRender(fresh){
  const p = CG.puzzle, st = CG.state;
  const started = !!CG.started;
  $('#cgIntro').classList.toggle('hide', started);
  $('#cgPlay').classList.toggle('hide', !started);
  $('#cgIntroTime').textContent = cgClock(cgLimitMs());
  $('#cgIntroRules').textContent =
    `Two well-known players appear, and ${p.total} other players were a teammate of both. `
    + `Each one you name scores ${p.points_per_teammate}; each wrong guess costs ${p.penalty_per_wrong}. `
    + `Name all ${p.total} and every second left on the clock is worth ${p.bonus_per_second} more.`;
  $('#cgNote').innerHTML = CG.ranked
    ? `<b>Ranked.</b> The clock is the server's, and it starts when you press Start.`
    : CG.mode !== 'daily' ? `Practice. It isn't ranked, and the clock is yours.`
    : CG.peeked ? `<b>Not ranked.</b> You already played today's pair in this browser while signed out, so
        a second attempt can't be ranked.`
    : `<b>Not ranked.</b> You are signed out, so this attempt is not recorded.
        <a href="#" onclick="openAuthModal('login');return false">Sign in</a> to be on today's board.`;
  const chal = $('#cgChallenge');
  const shared = new URLSearchParams(location.search).get('seed');
  chal.classList.toggle('hide', !(shared && CG.mode === 'practice' && !started));
  if (!chal.classList.contains('hide')) chal.innerHTML = `Somebody sent you <b>${esc(p.label)}</b>. Can you beat them to it?`;
  cgPaintStreak();
  if (!started) return;

  $('#cgA').innerHTML = cgPlayerHtml(p.a);
  $('#cgB').innerHTML = cgPlayerHtml(p.b);
  const found = st ? st.found : [], wrong = st ? st.wrong : [];
  $('#cgFound').textContent = found.length;
  $('#cgOf').textContent = `of ${p.total}`;
  $('#cgPoints').textContent = st ? st.score.points : 0;
  const over = !!(st && st.finished);
  $('#cgPicker').classList.toggle('hide', over);
  $('#cgStopRow').classList.toggle('hide', over);
  // The newest find first, so it sits just under the box that made it.
  $('#cgFoundList').innerHTML = found.slice().reverse().map((a, k) => `
    <div class="cg-hit${fresh && k === 0 ? ' fresh' : ''}">
      <div class="cg-hit-name"><b>${esc(a.name)}</b><span class="gp-clubs">${cgCrests(a.crests)}</span></div>
      <div class="cg-hit-with"><i>${esc(p.a.name)}</i> ${a.with_a.map(esc).join(' · ')}</div>
      <div class="cg-hit-with"><i>${esc(p.b.name)}</i> ${a.with_b.map(esc).join(' · ')}</div>
    </div>`).join('');
  $('#cgWrongList').innerHTML = wrong.length
    ? wrong.map(w => `<span class="xi-chip${w.kind === 'neither' ? '' : ' other'}" title="${esc(w.name + ' ' + cgWhy(w.kind))}">${esc(w.name)}</span>`).join('') : '';
}

function cgWhy(kind){
  const p = CG.puzzle;
  return kind === 'only_a' ? `played with ${p.a.name}, never with ${p.b.name}`
       : kind === 'only_b' ? `played with ${p.b.name}, never with ${p.a.name}`
       : 'played with neither of them';
}

// --- guessing -----------------------------------------------------------------------------

function cgFilter(){
  const tried = new Set(CG.moves.map(m => m.id));
  const pair = new Set([CG.puzzle.a.person_id, CG.puzzle.b.person_id]);
  const m = pzMatches($('#cgInput').value,
    p => pair.has(p.id) ? 'one of the pair' : tried.has(p.id) ? 'tried' : '');
  CG.matches = m.list.map(x => x.p); CG.active = m.active;
  $('#cgResults').innerHTML = pzResultsHtml(m, 'cgGuess',
    'Nobody by that name. Names are as the scorecard prints them: V Kohli.');
}

function cgKey(e){
  if (!CG.matches.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    CG.active = (CG.active + (e.key === 'ArrowDown' ? 1 : -1) + CG.matches.length) % CG.matches.length;
    document.querySelectorAll('#cgResults button').forEach((b, k) => b.classList.toggle('act-row', k === CG.active));
  } else if (e.key === 'Enter'){
    e.preventDefault();
    const p = CG.matches[CG.active];
    if (p) cgGuess(p.id);
  }
}

async function cgGuess(pid){
  if (CG.busy || !CG.started || (CG.state && CG.state.finished)) return;
  const t = cgElapsed();                                  // when it was typed, not when it came back
  if (t >= cgLimitMs()){ cgTimeUp(); return; }
  CG.busy = true;
  try {
    let r;
    if (CG.ranked){
      r = await api('/api/ground/ranked/play', {method: 'POST', retry: true, headers: PZ_JSON,
        body: JSON.stringify({guess: pid})});
      cgApplyRanked(r);
      if (r.late) slip('Time is up.');
    } else {
      r = await cgPost({guess: {id: pid, t}});
      CG.moves.push({id: pid, t});
      CG.state = r.state;
      cgPersist();
    }
    $('#cgInput').value = ''; $('#cgResults').innerHTML = ''; CG.matches = [];
    cgRender(r.hit);
    if (r.hit === false){
      const name = (PZ_PLAYERS.find(p => p.id === pid) || {}).name || 'That player';
      slip(`${name}: ${cgWhy(r.kind)}. −${CG.puzzle.penalty_per_wrong}.`);
      const box = $('#cgPicker'); box.classList.remove('shake'); void box.offsetWidth; box.classList.add('shake');
    }
    if (CG.state.finished) cgFinish(true);
    else $('#cgInput').focus({preventScroll: true});
  } catch(e){
    if (/time is up/i.test(e.message)){ CG.busy = false; cgTimeUp(); return; }
    slip(e.message);
  }
  finally { CG.busy = false; }
}

async function cgStart(){
  if (CG.started || CG.busy) return;
  if (CG.ranked){
    CG.busy = true;
    try {
      cgApplyRanked(await api('/api/ground/ranked/start', {method: 'POST', retry: true}));
    } catch(e){ slip(e.message); CG.busy = false; return; }
    CG.busy = false;
    cgRender();
    if (CG.state && CG.state.finished){ cgFinish(false); return; }
    cgStartTick();
    $('#cgInput').focus({preventScroll: true});
    return;
  }
  CG.started = Date.now();
  CG.moves = []; CG.ended = null; CG.state = null;
  cgPersist();
  cgRender();
  cgStartTick();
  $('#cgInput').focus({preventScroll: true});
}

// --- finishing ----------------------------------------------------------------------------

function cgTitle(st){
  const sc = st.score;
  if (st.reason === 'all') return sc.wrong === 0 ? 'Flawless' : 'Full house';
  const share = sc.found / sc.total;
  return share >= .75 ? 'Almost the lot' : share >= .5 ? 'Good company' : sc.found ? 'A start' : 'Not this time';
}

function cgFinish(fresh){
  cgStopTick();
  const st = CG.state, p = CG.puzzle, sc = st.score;
  if (CG.mode === 'daily'){
    const days = cgStore().days || [];
    if (!days.includes(p.date)) cgSave({days: days.concat(p.date).sort().slice(-400)});
  }
  if (sc.points > (cgStore().best || 0)) cgSave({best: sc.points});
  if (CG.mode === 'daily' && !CG.ranked) cgSave({anonDone: p.date});    // see cgLoad: no ranked second look
  $('#cgPlay').classList.add('hide');
  $('#cgDone').classList.remove('hide');
  $('#cgDoneLabel').textContent = `COMMON GROUND · ${p.label.toUpperCase()}`;
  $('#cgDoneTitle').textContent = cgTitle(st);
  $('#cgFinal').textContent = sc.points;
  const time = st.reason === 'time' ? 'time up' : cgClock(st.elapsed_ms) + ' taken';
  $('#cgStats').innerHTML = `<span><b>${sc.found}/${sc.total}</b> found</span>`
    + `<span><b>${sc.wrong}</b> wrong</span><span><b>${time}</b></span>`;
  $('#cgSum').innerHTML = [
    `<span>${sc.found} × ${p.points_per_teammate}</span><b>+${sc.base}</b>`,
    sc.wrong ? `<span>${sc.wrong} wrong × ${p.penalty_per_wrong}</span><b>−${sc.penalty}</b>` : '',
    st.reason === 'all' ? `<span>${cgClock(cgLimitMs() - st.elapsed_ms)} left on the clock</span><b>+${sc.bonus}</b>` : '',
  ].filter(Boolean).map(x => `<div>${x}</div>`).join('');
  const rows = st.found.map(a => cgRevealRow(a, true)).concat(st.unfound.map(a => cgRevealRow(a, false)));
  $('#cgReveal').innerHTML = `<div class="gp-name">Everyone who played with both ${esc(p.a.name)} and ${esc(p.b.name)}:</div>${rows.join('')}`;
  $('#cgSharePreview').textContent = st.share || '';
  cgPaintStreak();
  cgRankPanel();
  if (fresh) $('#cgDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

// Where the attempt placed, and today's board. Only a ranked attempt has a place; a signed-out
// daily gets the way to earn one, and practice gets neither.
async function cgRankPanel(){
  const el = $('#pzRank'), board = $('#pzBoard'), rar = $('#pzRarity');
  if (CG.mode !== 'daily'){ el.innerHTML = ''; board.innerHTML = ''; rar.innerHTML = ''; return; }
  const st = CG.state;
  el.innerHTML = CG.ranked && CG.rank
    ? `<div class="pzr-chip"><i>Today's rank</i><b>#${CG.rank}</b> of ${CG.of}<span>${CG.points} pts${
        CG.bonus ? ` · ${st.score.points} + ${CG.bonus} rarity` : ''}</span></div>`
    : CG.signedIn ? `<div class="pzr-note">Not ranked: this pair was played in this browser while signed out.</div>`
    : `<div class="pzr-note">Sign in to be ranked on today's board.
        <button class="act minor" onclick="openAuthModal('login')">Sign in</button></div>`;
  pzLoadBoard('common', '#pzBoard', CG.puzzle.date);
  if (CG.ranked){ rar.innerHTML = pzRarityHtml(CG.rarity); return; }
  // Signed out: still counted toward rarity, under this browser's id, and never ranked.
  rar.innerHTML = '';
  rar.innerHTML = pzRarityHtml(await pzCountPicks('common', {seed: CG.puzzle.seed, moves: CG.moves,
    end_t: CG.state && CG.state.reason !== 'all' && CG.ended ? CG.ended.t : null}));
}

// Signing in on this page redraws it. A player looking at a finished signed-out attempt is not
// ranked for it (the clock of an attempt the server did not time is not trusted, and `anonDone`
// stops a second, informed look); anyone else is offered a ranked attempt.
document.addEventListener('signedin', () => { if (CG.puzzle) cgMode(CG.mode); });

function cgRevealRow(a, got){
  return `<div class="cg-hit ${got ? 'got' : 'missed'}">
    <div class="cg-hit-name"><b>${esc(a.name)}</b><span class="gp-clubs">${cgCrests(a.crests)}</span></div>
    <div class="cg-hit-with"><i>${esc(CG.puzzle.a.name)}</i> ${a.with_a.map(esc).join(' · ')}</div>
    <div class="cg-hit-with"><i>${esc(CG.puzzle.b.name)}</i> ${a.with_b.map(esc).join(' · ')}</div></div>`;
}

const cgShare = () => pzShare(CG.state && CG.state.share);
const cgLink = () => pzCopyLink(`${PZ_ORIGIN}/common?seed=${CG.puzzle.seed}`);

// --- boot ---------------------------------------------------------------------------------

(async function cgBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const seeded = new URLSearchParams(location.search).get('seed');
  await cgMode(seeded ? 'practice' : 'daily');
  pzPlayers().catch(() => {});       // ready before the first keystroke, off the critical path
})();
