// Three to Make: one target, ninety seconds, three players whose figures add up to it. [A196]
//
// Same shape as Common Ground, and for the same reason: whose clock counts decides whether a
// result can be ranked.
//   * Practice, and the daily while signed out: the page's. The start is kept as a wall-clock
//     timestamp so a reload resumes the same countdown, and the lock-in carries the ms since then.
//   * The daily while signed in: the SERVER's. Start stamps the database's clock, every change to
//     the trio is sent and held there (so a clock that runs out keeps it), and the page only
//     displays a countdown rebuilt from the time-left each response reports.
//
// The players' figures are never on this page until the trio is locked in: the puzzle carries
// the target and the stat's name, and the figures arrive only with the result.

const TR_KEY = 'iplegends_trio_v1';

let TR = {mode: 'daily', puzzle: null, started: null, picks: [], result: null, ended: null,
          matches: [], active: 0, busy: false, timer: null, ranked: false, rank: null, of: 0,
          rarity: null, signedIn: false, peeked: false};

const trStore = () => pzStore(TR_KEY);
const trSave = patch => pzSave(TR_KEY, patch);
pzOnReset(TR_KEY, () => trMode('daily'));      // an admin's replay [A195]

const trLimitMs = () => TR.puzzle.limit_seconds * 1000;
const trElapsed = () => Math.min(Math.max(0, Date.now() - TR.started), trLimitMs());
const trClock = ms => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
// A countdown rounds UP (it reads 0:01 until the very end); a time TAKEN rounds down, as the
// server's share line does, so the two never disagree about the same attempt.
const trTaken = ms => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const trNum = n => Number(n).toLocaleString('en-IN');
const trName = pid => (PZ_PLAYERS && PZ_PLAYERS.find(p => p.id === pid) || {}).name || '…';

function trPersist(){
  const p = TR.puzzle;
  const rec = {started: TR.started, picks: TR.picks, ended: TR.ended};
  if (TR.mode === 'daily') trSave({daily: Object.assign({date: p.date}, rec)});
  else trSave({practice: Object.assign({seed: p.seed}, rec)});
}

function trPaintStreak(){
  const best = trStore().best;
  const streak = TR.mode === 'daily' && TR.puzzle
    ? pzStreakHtml(trStore().days || [], TR.puzzle.date, 'target') : '';
  $('#trStreak').innerHTML = [streak, best != null ? `Your closest: <b>${best === 0 ? 'bang on' : trNum(best) + ' off'}</b>.` : '']
    .filter(Boolean).join(' ');
}

// --- loading ------------------------------------------------------------------------------

async function trFetchPuzzle(mode, fresh){
  if (mode === 'daily') return api('/api/trio/today');
  const urlSeed = new URLSearchParams(location.search).get('seed');
  const saved = trStore().practice;
  const seed = !fresh && urlSeed ? urlSeed : (!fresh && saved ? saved.seed : null);
  return api('/api/trio' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

async function trMode(mode, fresh){
  if (TR.busy) return;
  trStopTick();
  TR.mode = mode;
  document.querySelectorAll('#trTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
  try {
    const puzzle = await trFetchPuzzle(mode, fresh);
    await trLoad(puzzle, fresh);
    history.replaceState(null, '', mode === 'daily' ? '/trio' : '/trio?seed=' + puzzle.seed);
  } catch(e){ slip(e.message); }
}

async function trLoad(puzzle, fresh){
  TR.puzzle = puzzle;
  // A finished attempt names an exact trio, and the target is the same for everybody, so a
  // browser that has already finished today's signed out is not offered a ranked attempt at it.
  TR.peeked = TR.mode === 'daily' && trStore().anonDone === puzzle.date;
  TR.signedIn = await pzSignedIn();
  TR.ranked = TR.mode === 'daily' && TR.signedIn && !TR.peeked;
  TR.rank = null; TR.of = 0; TR.rarity = null; TR.result = null;
  $('#trDone').classList.add('hide');
  if (TR.ranked){ await trLoadRanked(); return; }
  const s = trStore();
  const saved = TR.mode === 'daily'
    ? (s.daily && s.daily.date === puzzle.date ? s.daily : null)
    : (!fresh && s.practice && String(s.practice.seed) === String(puzzle.seed) ? s.practice : null);
  TR.started = saved ? saved.started || null : null;
  TR.picks = saved ? saved.picks || [] : [];
  TR.ended = saved ? saved.ended || null : null;
  // The clock may have run out while the page was closed: that attempt ended at the limit,
  // with a full trio counted if one was held and nothing otherwise.
  if (TR.started && !TR.ended && Date.now() - TR.started >= trLimitMs())
    TR.ended = {t: trLimitMs(), timeout: true};
  if (TR.ended){
    try { TR.result = await trScore(); }
    catch(e){ TR.started = null; TR.picks = []; TR.ended = null; }   // a trio the server refuses is dropped
  }
  trPersist();
  await pzPlayers().catch(() => {});
  trRender();
  if (TR.result) trFinish(false);
  else if (TR.started) trStartTick();
}

async function trLoadRanked(){
  TR.started = null; TR.picks = []; TR.ended = null;
  try { trApplyRanked(await api('/api/trio/ranked')); }
  catch(e){ slip(e.message); }
  await pzPlayers().catch(() => {});
  trRender();
  if (TR.result) trFinish(false);
  else if (TR.started) trStartTick();
}

// Take the server's reading: the trio it holds, its result once over, and the time left, from
// which the page's own start is rebuilt (so the countdown is the server's).
function trApplyRanked(r){
  TR.rank = r.rank; TR.of = r.of; TR.rarity = r.rarity;
  if (!r.started){ TR.started = null; TR.result = null; return; }
  TR.started = Date.now() - (trLimitMs() - r.remaining_ms);
  if (r.finished){
    TR.result = r.result;
    TR.picks = r.result.picks.map(p => p.person_id);
  } else {
    TR.result = null;
    TR.picks = r.held.map(h => h.person_id);
  }
}

// Anonymous scoring: the page's own clock says when the trio was locked in.
async function trScore(){
  const timeoutEmpty = TR.ended.timeout && TR.picks.length !== 3;
  return api('/api/trio/play', {method: 'POST', retry: true, headers: PZ_JSON,
    body: JSON.stringify({seed: TR.puzzle.seed, picks: timeoutEmpty ? null : TR.picks, t: TR.ended.t})});
}

// --- the clock ----------------------------------------------------------------------------

function trStopTick(){ if (TR.timer){ clearInterval(TR.timer); TR.timer = null; } }

function trStartTick(){
  trStopTick();
  trTick();
  TR.timer = setInterval(trTick, 200);
}

function trTick(){
  if (!TR.started || TR.result) { trStopTick(); return; }
  const left = trLimitMs() - (Date.now() - TR.started);
  $('#trTime').textContent = trClock(left);
  $('#trBar').style.width = Math.max(0, Math.min(100, left / trLimitMs() * 100)) + '%';
  $('#trClock').classList.toggle('urgent', left <= 15000);
  if (left <= 0) trTimeUp();
}

async function trTimeUp(){
  trStopTick();
  if (TR.busy || TR.result) return;
  if (TR.ranked){ await trRankedTimeUp(); return; }
  TR.ended = {t: trLimitMs(), timeout: true};
  await trEndAnon();
}

// The server ends a ranked attempt at its own limit. The page's clock was rebuilt from the
// server's, so this arrives around then; if a little early, the server is asked again shortly.
async function trRankedTimeUp(){
  TR.busy = true;
  try {
    for (let attempt = 0; attempt < 5 && !TR.result; attempt++){
      const r = await api('/api/trio/ranked/play', {method: 'POST', retry: true, headers: PZ_JSON,
        body: JSON.stringify({picks: TR.picks})});
      trApplyRanked(r);
      if (!TR.result) await new Promise(res => setTimeout(res, 1200));
    }
    if (!TR.result) trApplyRanked(await api('/api/trio/ranked'));
  } catch(e){ slip(e.message); }
  finally { TR.busy = false; }
  trRender();
  if (TR.result) trFinish(true);
}

async function trEndAnon(){
  TR.busy = true;
  try {
    TR.result = await trScore();
    trPersist(); trRender(); trFinish(true);
  } catch(e){ TR.ended = null; slip(e.message); if (TR.started) trStartTick(); }
  finally { TR.busy = false; }
}

// --- choosing -----------------------------------------------------------------------------

function trRender(){
  const p = TR.puzzle;
  const started = !!TR.started;
  $('#trIntro').classList.toggle('hide', started);
  $('#trPlay').classList.toggle('hide', !started || !!TR.result);
  $('#trIntroTime').textContent = trClock(trLimitMs());
  $('#trIntroRules').textContent =
    `A target appears: a total of one IPL stat. Pick ${p.picks} different players whose figures add up `
    + `as close to it as you can. Nearest wins; a tie goes to the trio fewer others picked, then to the quicker.`;
  $('#trNote').innerHTML = TR.ranked
    ? `<b>Ranked.</b> The clock is the server's, and it starts when you press Start.`
    : TR.mode !== 'daily' ? `Practice. It isn't ranked, and the clock is yours.`
    : TR.peeked ? `<b>Not ranked.</b> You already played today's target in this browser while signed out,
        so a second attempt can't be ranked.`
    : `<b>Not ranked.</b> You are signed out, so this attempt is not recorded.
        <a href="#" onclick="openAuthModal('login');return false">Sign in</a> to be on today's board.`;
  const chal = $('#trChallenge');
  const shared = new URLSearchParams(location.search).get('seed');
  chal.classList.toggle('hide', !(shared && TR.mode === 'practice' && !started));
  if (!chal.classList.contains('hide')) chal.innerHTML = `Somebody sent you <b>${esc(p.label)}</b>. Can you get closer?`;
  trPaintStreak();
  if (!started || TR.result) return;

  $('#trTarget').textContent = trNum(p.target);
  $('#trStat').textContent = p.stat_label;
  const slots = [];
  for (let k = 0; k < p.picks; k++){
    const pid = TR.picks[k];
    slots.push(pid
      ? `<div class="tr-slot on"><b>${esc(trName(pid))}</b><button aria-label="Remove" onclick="trDrop('${esc(pid)}')">×</button></div>`
      : `<div class="tr-slot"><i>${k + 1}</i></div>`);
  }
  $('#trSlots').innerHTML = slots.join('');
  const full = TR.picks.length === p.picks;
  $('#trPicker').classList.toggle('hide', full);
  $('#trPickerQ').textContent = TR.picks.length ? `${p.picks - TR.picks.length} more` : 'Who makes it?';
  $('#trLock').disabled = !full;
}

function trFilter(){
  const chosen = new Set(TR.picks);
  const m = pzMatches($('#trInput').value, p => chosen.has(p.id) ? 'picked' : '');
  TR.matches = m.list.map(x => x.p); TR.active = m.active;
  $('#trResults').innerHTML = pzResultsHtml(m, 'trAdd',
    'Nobody by that name. Names are as the scorecard prints them: V Kohli.');
}

function trKey(e){
  if (!TR.matches.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    TR.active = (TR.active + (e.key === 'ArrowDown' ? 1 : -1) + TR.matches.length) % TR.matches.length;
    document.querySelectorAll('#trResults button').forEach((b, k) => b.classList.toggle('act-row', k === TR.active));
  } else if (e.key === 'Enter'){
    e.preventDefault();
    const p = TR.matches[TR.active];
    if (p) trAdd(p.id);
  }
}

async function trAdd(pid){
  if (!TR.started || TR.result || TR.picks.length >= TR.puzzle.picks || TR.picks.includes(pid)) return;
  TR.picks = TR.picks.concat(pid);
  $('#trInput').value = ''; $('#trResults').innerHTML = ''; TR.matches = [];
  await trChanged();
  if (TR.picks.length < TR.puzzle.picks) $('#trInput').focus({preventScroll: true});
}

async function trDrop(pid){
  if (!TR.started || TR.result) return;
  TR.picks = TR.picks.filter(x => x !== pid);
  await trChanged();
  $('#trInput').focus({preventScroll: true});
}

// A change to the trio: kept in this browser, and -- ranked -- held by the server too, so a
// clock that runs out keeps it.
async function trChanged(){
  trRender();
  if (!TR.ranked){ trPersist(); return; }
  try {
    const r = await api('/api/trio/ranked/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({picks: TR.picks})});
    const mine = TR.picks;
    trApplyRanked(r);
    if (!r.finished) TR.picks = mine;            // what is on screen is the latest choice
    if (r.late) slip('Time is up.');
    trRender();
    if (TR.result) trFinish(true);
  } catch(e){ slip(e.message); }
}

async function trLock(){
  if (TR.busy || !TR.started || TR.result || TR.picks.length !== TR.puzzle.picks) return;
  const t = trElapsed();                                 // when it was pressed, not when it came back
  trStopTick();
  if (!TR.ranked){
    TR.ended = {t};
    await trEndAnon();
    return;
  }
  TR.busy = true;
  try {
    const r = await api('/api/trio/ranked/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({picks: TR.picks, lock: true})});
    trApplyRanked(r);
    if (r.late) slip('Time was up before that arrived.');
  } catch(e){ slip(e.message); }
  finally { TR.busy = false; }
  trRender();
  if (TR.result) trFinish(true); else if (TR.started) trStartTick();
}

async function trStart(){
  if (TR.started || TR.busy) return;
  if (TR.ranked){
    TR.busy = true;
    try { trApplyRanked(await api('/api/trio/ranked/start', {method: 'POST', retry: true})); }
    catch(e){ slip(e.message); TR.busy = false; return; }
    TR.busy = false;
    trRender();
    if (TR.result){ trFinish(false); return; }
    trStartTick();
    $('#trInput').focus({preventScroll: true});
    return;
  }
  TR.started = Date.now();
  TR.picks = []; TR.ended = null; TR.result = null;
  trPersist();
  trRender();
  trStartTick();
  $('#trInput').focus({preventScroll: true});
}

// --- finishing ----------------------------------------------------------------------------

function trTitle(r){
  if (r.distance === null) return 'Out of time';
  if (r.distance === 0) return 'Bang on';
  const share = r.distance / TR.puzzle.target;
  return share <= .01 ? 'A whisker away' : share <= .05 ? 'Close' : share <= .15 ? 'In the area' : 'Wide of the mark';
}

function trCrests(urls){
  return urls.map(u => `<img class="gp-club" src="${esc(u)}" alt="" width="20" height="20">`).join('');
}

function trRow(p){
  return `<div class="tr-row"><span><b>${esc(p.name)}</b><span class="gp-clubs">${trCrests(p.crests)}</span></span>
    <em>${trNum(p.value)}</em></div>`;
}

function trFinish(fresh){
  trStopTick();
  const r = TR.result, p = TR.puzzle;
  if (TR.mode === 'daily'){
    const days = trStore().days || [];
    if (!days.includes(p.date)) trSave({days: days.concat(p.date).sort().slice(-400)});
  }
  const best = trStore().best;
  if (r.distance !== null && (best == null || r.distance < best)) trSave({best: r.distance});
  if (TR.mode === 'daily' && !TR.ranked) trSave({anonDone: p.date});
  $('#trPlay').classList.add('hide');
  $('#trIntro').classList.add('hide');
  $('#trDone').classList.remove('hide');
  $('#trDoneLabel').textContent = `THREE TO MAKE · ${p.label.toUpperCase()}`;
  $('#trDoneTitle').textContent = trTitle(r);
  $('#trFinal').textContent = r.distance === null ? '–' : trNum(r.distance);
  $('#trFinalUnit').textContent = r.distance === null ? 'no trio' : r.distance === 0 ? 'off · exact' : `${p.unit} ${r.total > p.target ? 'over' : 'short'}`;
  const time = r.distance === null ? 'time up' : trTaken(r.elapsed_ms) + ' taken';
  $('#trStats').innerHTML = `<span><b>${trNum(p.target)}</b> ${esc(p.stat_label)}</span><span><b>${time}</b></span>`;
  $('#trSheet').innerHTML = r.picks.length
    ? r.picks.map(trRow).join('')
      + `<div class="tr-row tot"><span>Your three</span><em>${trNum(r.total)}</em></div>`
      + `<div class="tr-row tgt"><span>Target</span><em>${trNum(p.target)}</em></div>`
    : `<div class="tr-row"><span>No trio was locked in before the clock ran out.</span></div>`;
  const n = r.exact_count;
  $('#trExample').innerHTML = `<div class="gp-name">One way to make it exactly</div>`
    + r.example.map(trRow).join('')
    + `<p class="room-note">${trNum(n)} different trio${n === 1 ? '' : 's'} of IPL players make ${trNum(p.target)} exactly.</p>`;
  $('#trSharePreview').textContent = r.share || '';
  trPaintStreak();
  trRankPanel();
  if (fresh) $('#trDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

async function trRankPanel(){
  const el = $('#pzRank'), board = $('#pzBoard'), rar = $('#pzRarity');
  if (TR.mode !== 'daily'){ el.innerHTML = ''; board.innerHTML = ''; rar.innerHTML = ''; return; }
  const r = TR.result;
  el.innerHTML = TR.ranked && TR.rank
    ? `<div class="pzr-chip"><i>Today's rank</i><b>#${TR.rank}</b> of ${TR.of}<span>${esc(r.verdict)}${
        r.distance !== null ? ' · ' + trTaken(r.elapsed_ms) : ''}</span></div>`
    : TR.signedIn ? `<div class="pzr-note">Not ranked: this target was played in this browser while signed out.</div>`
    : `<div class="pzr-note">Sign in to be ranked on today's board.
        <button class="act minor" onclick="openAuthModal('login')">Sign in</button></div>`;
  if (TR.signedIn) el.innerHTML += pzResetHtml('trio', TR.puzzle.seed);
  pzLoadBoard('trio', '#pzBoard', TR.puzzle.date);
  if (TR.ranked){ rar.innerHTML = pzRarityHtml(TR.rarity, true); return; }
  rar.innerHTML = '';
  if (r.picks.length)        // signed out: counted toward rarity under this browser's id, never ranked
    rar.innerHTML = pzRarityHtml(await pzCountPicks('trio', {seed: TR.puzzle.seed, ids: TR.picks}), true);
}

document.addEventListener('signedin', () => { if (TR.puzzle) trMode(TR.mode); });

const trShare = () => pzShare(TR.result && TR.result.share);
const trLink = () => pzCopyLink(`${PZ_ORIGIN}/trio?seed=${TR.puzzle.seed}`);

// --- boot ---------------------------------------------------------------------------------

(async function trBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  pzPlayers().catch(() => {});       // ready before the first keystroke
  const seeded = new URLSearchParams(location.search).get('seed');
  await trMode(seeded ? 'practice' : 'daily');
})();
