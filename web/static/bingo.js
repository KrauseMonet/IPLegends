// Bingo: a 3x3 grid of "name a player who is both" [A182].
//
// The server owns the rules and the page owns nothing it cannot rebuild. A grid is a pure
// function of its seed, and the guesses so far are the whole game state: each move sends
// the list, the server replays it and answers (A62's shape), so a reload, a second device
// or a returning player's page all ask the same question -- "where was I" -- and get the
// same answer. localStorage holds only that list, which is the one thing it cannot derive.
//
// There is no account and no leaderboard, so a streak lives in this browser (a per-viewer
// convenience, like Flashback's best score) and a result travels as a spoiler-free share.

const BG_KEY = 'iplegends_bingo_v1';
// A shared link has to work for whoever reads it, so it names the real site (A129).
const BG_ORIGIN = 'https://iplegends.vercel.app';
const BG_JSON = {'Content-Type': 'application/json'};

let BG = {mode: 'daily', grid: null, guesses: [], state: null, sel: null,
          players: null, answers: null, busy: false, matches: [], active: 0};

// --- storage: the guess list per grid, and the days a daily was finished ------------------

function bgStore(){
  try { return JSON.parse(localStorage.getItem(BG_KEY) || '{}') || {}; }
  catch(e){ return {}; }
}
function bgSave(patch){
  try { localStorage.setItem(BG_KEY, JSON.stringify(Object.assign(bgStore(), patch))); }
  catch(e){ /* private window, quota: the game still plays, it just will not resume */ }
}

function bgPersist(){
  const g = BG.grid;
  if (BG.mode === 'daily') bgSave({daily: {date: g.date, guesses: BG.guesses}});
  else bgSave({practice: {seed: g.seed, guesses: BG.guesses}});
}

// --- streak -------------------------------------------------------------------------------
// A streak is not broken until a day has actually been MISSED (A130): playing yesterday and
// not yet today leaves it alive, because nothing has been lost, only not yet extended.

function bgDayNum(iso){ return Math.round(Date.parse(iso + 'T00:00:00Z') / 86400000); }

function bgStreaks(days, todayIso){
  const set = new Set(days.map(bgDayNum));
  const today = bgDayNum(todayIso);
  let cur = 0, d = set.has(today) ? today : today - 1;
  while (set.has(d)){ cur++; d--; }
  let best = 0, run = 0, prev = null;
  for (const n of [...set].sort((a, b) => a - b)){
    run = prev !== null && n === prev + 1 ? run + 1 : 1;
    best = Math.max(best, run); prev = n;
  }
  return {cur, best};
}

function bgPaintStreak(){
  const el = $('#bgStreak');
  if (BG.mode !== 'daily' || !BG.grid){ el.textContent = ''; return; }
  const days = bgStore().days || [];
  const {cur, best} = bgStreaks(days, BG.grid.date);
  const doneToday = days.includes(BG.grid.date);
  if (!cur){ el.textContent = best ? `Best streak: ${best} days.` : ''; return; }
  el.innerHTML = doneToday
    ? `Streak: <b>${cur}</b> day${cur === 1 ? '' : 's'}.`
    : `Streak: <b>${cur}</b> day${cur === 1 ? '' : 's'}. Finish today's grid to keep it.`;
}

// --- loading ------------------------------------------------------------------------------

function bgEmptyState(){
  return {placed: [], wrong: [], guesses_left: 9, score: 0, finished: false, share: null};
}

async function bgFetchGrid(mode, fresh){
  if (mode === 'daily') return api('/api/bingo/today');
  const urlSeed = new URLSearchParams(location.search).get('seed');
  const saved = bgStore().practice;
  const seed = !fresh && urlSeed ? urlSeed : (!fresh && saved ? saved.seed : null);
  return api('/api/bingo' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

async function bgMode(mode, fresh){
  if (BG.busy) return;
  BG.mode = mode;
  document.querySelectorAll('#bgTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
  try {
    const grid = await bgFetchGrid(mode, fresh);
    await bgStart(grid, fresh);
    history.replaceState(null, '', mode === 'daily' ? '/bingo' : '/bingo?seed=' + grid.seed);
  } catch(e){ slip(e.message); }
}

async function bgStart(grid, fresh){
  BG.grid = grid; BG.sel = null; BG.answers = null;
  const s = bgStore();
  const saved = BG.mode === 'daily'
    ? (s.daily && s.daily.date === grid.date ? s.daily.guesses : [])
    : (!fresh && s.practice && String(s.practice.seed) === String(grid.seed) ? s.practice.guesses : []);
  BG.guesses = saved || [];
  BG.state = bgEmptyState();
  if (BG.guesses.length){
    try {
      const r = await api('/api/bingo/play', {method: 'POST', retry: true, headers: BG_JSON,
        body: JSON.stringify({seed: grid.seed, guesses: BG.guesses})});
      BG.state = r.state;
    } catch(e){
      // A stored list the server now refuses (a grid that changed, a corrupt entry) is
      // dropped rather than left to wedge the page.
      BG.guesses = []; bgPersist();
    }
  }
  bgPersist();
  bgRender();
  if (BG.state.finished) bgFinish(false);
}

// --- rendering ----------------------------------------------------------------------------

function bgAxis(a){
  const crest = a.crest ? `<img src="${esc(a.crest)}" alt="" width="30" height="30">` : '';
  return `<div class="bg-axis ${a.kind}">${crest}<span>${esc(a.label)}</span></div>`;
}

function bgCellHtml(i){
  const st = BG.state;
  const placed = st.placed.find(p => p.cell === i);
  const tried = st.wrong.filter(w => w.cell === i).length;
  const classes = ['bg-cell'];
  let inner = '<span class="bg-plus">+</span>';
  if (placed){
    classes.push('filled');
    if (placed.answers <= 5) classes.push('rare');
    inner = `<b>${esc(placed.name)}</b><i>+${placed.points} · 1 of ${placed.answers}</i>`;
  } else {
    if (BG.sel === i) classes.push('sel');
    if (st.finished && BG.answers){
      const a = BG.answers[i];
      classes.push('missed');
      inner = `<i>${a.n} fit</i><span class="bg-eg">${a.sample.slice(0, 2).map(esc).join(', ')}</span>`;
    } else if (tried){
      inner += `<i class="bg-tried">${tried} wrong</i>`;
    }
  }
  const disabled = placed || st.finished ? 'disabled' : '';
  return `<button class="${classes.join(' ')}" data-cell="${i}" ${disabled}
    onclick="bgSelect(${i})">${inner}</button>`;
}

function bgRender(){
  const g = BG.grid, st = BG.state;
  let html = '<div class="bg-corner"></div>' + g.cols.map(bgAxis).join('');
  for (let r = 0; r < 3; r++){
    html += bgAxis(g.rows[r]);
    for (let c = 0; c < 3; c++) html += bgCellHtml(r * 3 + c);
  }
  $('#bgBoard').innerHTML = html;
  $('#bgScore').textContent = st.score;

  // One pip per guess, in the order they were made: mint if it landed, red if it did not.
  const hit = new Set(st.placed.map(p => p.cell + ':' + p.person_id));
  let pips = BG.guesses.map(([c, pid]) => `<i class="${hit.has(c + ':' + pid) ? 'right' : 'wrong'}"></i>`);
  while (pips.length < g.guesses) pips.push('<i></i>');
  $('#bgPips').innerHTML = pips.join('');

  $('#bgHint').textContent = st.finished ? ''
    : BG.sel === null ? 'Tap a square to pick it.' : '';
  $('#bgPicker').classList.toggle('hide', BG.sel === null || st.finished);
  $('#bgDone').classList.toggle('hide', !st.finished);
  const chal = $('#bgChallenge');
  const shared = new URLSearchParams(location.search).get('seed');
  chal.classList.toggle('hide', !(shared && BG.mode === 'practice' && !st.finished && !BG.guesses.length));
  if (!chal.classList.contains('hide')) chal.innerHTML = `Somebody sent you <b>${esc(g.label)}</b>. Can you fill it?`;
  bgPaintStreak();
}

// --- picking ------------------------------------------------------------------------------

async function bgPlayers(){
  if (!BG.players){
    const list = await api('/api/bingo/players');
    BG.players = list.map(p => ({id: p.id, name: p.name,
      words: bgNorm(p.name).split(/\s+/)}));
  }
  return BG.players;
}

function bgNorm(s){
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').trim();
}

async function bgSelect(i){
  if (BG.state.finished) return;
  BG.sel = i;
  bgRender();
  if (i === null) return;
  const g = BG.grid;
  $('#bgPickerQ').innerHTML = `Who fits <b>${esc(g.rows[Math.floor(i / 3)].label)}</b> and <b>${esc(g.cols[i % 3].label)}</b>?`;
  const input = $('#bgInput');
  input.value = '';
  $('#bgResults').innerHTML = '';
  $('#bgPicker').scrollIntoView({block: 'nearest', behavior: 'smooth'});
  input.focus({preventScroll: true});
  try { await bgPlayers(); } catch(e){ slip(e.message); }
}

function bgFilter(){
  const q = bgNorm($('#bgInput').value);
  const out = $('#bgResults');
  if (!q || !BG.players){ BG.matches = []; out.innerHTML = ''; return; }
  const toks = q.split(/\s+/);
  const placed = new Set(BG.state.placed.map(p => p.person_id));
  const triedHere = new Set(BG.state.wrong.filter(w => w.cell === BG.sel).map(w => w.person_id));
  const scored = [];
  for (const p of BG.players){
    let score = 0;
    for (const t of toks){
      const k = p.words.findIndex(w => w.startsWith(t));
      if (k < 0){ score = -1; break; }
      score += k === p.words.length - 1 ? 0 : 1;      // a surname match ranks first
    }
    if (score >= 0) scored.push({p, score});
  }
  scored.sort((a, b) => a.score - b.score || a.p.name.localeCompare(b.p.name));
  BG.matches = scored.slice(0, 8).map(x => x.p);
  BG.active = BG.matches.findIndex(p => !placed.has(p.id) && !triedHere.has(p.id));
  if (BG.active < 0) BG.active = 0;
  out.innerHTML = BG.matches.map((p, k) => {
    const why = placed.has(p.id) ? 'on the grid' : triedHere.has(p.id) ? 'tried here' : '';
    return `<li><button class="${k === BG.active ? 'act-row' : ''}" ${why ? 'disabled' : ''}
      onclick="bgGuess('${esc(p.id)}')">${esc(p.name)}${why ? `<em>${why}</em>` : ''}</button></li>`;
  }).join('') || '<li class="bg-none">Nobody by that name. Names are as the scorecard prints them: V Kohli.</li>';
}

function bgKey(e){
  if (e.key === 'Escape'){ bgSelect(null); return; }
  if (!BG.matches.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    BG.active = (BG.active + (e.key === 'ArrowDown' ? 1 : -1) + BG.matches.length) % BG.matches.length;
    document.querySelectorAll('#bgResults button').forEach((b, k) => b.classList.toggle('act-row', k === BG.active));
  } else if (e.key === 'Enter'){
    e.preventDefault();
    const p = BG.matches[BG.active];
    if (p) bgGuess(p.id);
  }
}

async function bgGuess(pid){
  if (BG.busy || BG.sel === null) return;
  const cell = BG.sel;
  BG.busy = true;
  try {
    const r = await api('/api/bingo/play', {method: 'POST', retry: true, headers: BG_JSON,
      body: JSON.stringify({seed: BG.grid.seed, guesses: BG.guesses, guess: [cell, pid]})});
    BG.guesses.push([cell, pid]);
    BG.state = r.state;
    bgPersist();
    if (r.correct) BG.sel = null;
    bgRender();
    const el = document.querySelector(`.bg-cell[data-cell="${cell}"]`);
    if (el) el.classList.add(r.correct ? 'pop' : 'shake');
    if (!r.correct){
      const name = (BG.players.find(p => p.id === pid) || {}).name || 'That player';
      slip(`${name} doesn't fit. ${BG.state.guesses_left} guess${BG.state.guesses_left === 1 ? '' : 'es'} left.`);
      $('#bgInput').value = ''; $('#bgResults').innerHTML = '';
      $('#bgInput').focus({preventScroll: true});
    }
    if (BG.state.finished) bgFinish(true);
  } catch(e){ slip(e.message); }
  finally { BG.busy = false; }
}

// --- finishing ----------------------------------------------------------------------------

function bgTitle(n){
  return n === 9 ? 'A perfect grid' : n >= 7 ? 'A strong grid' : n >= 4 ? 'A respectable grid'
       : n >= 1 ? 'A tough one' : 'Not your day';
}

async function bgFinish(fresh){
  const st = BG.state, g = BG.grid;
  if (BG.mode === 'daily'){
    const days = bgStore().days || [];
    if (!days.includes(g.date)) bgSave({days: days.concat(g.date).sort().slice(-400)});
  }
  $('#bgDoneLabel').textContent = `BINGO · ${g.label.toUpperCase()}`;
  $('#bgDoneTitle').textContent = bgTitle(st.placed.length);
  $('#bgFinal').textContent = st.score;
  $('#bgSharePreview').textContent = st.share || '';
  const rare = st.placed.filter(p => p.answers <= 5).length;
  $('#bgStats').innerHTML = `<span><b>${st.placed.length}</b>/9 squares</span>`
    + (rare ? `<span><b>${rare}</b> rare</span>` : '') + `<span><b>${BG.guesses.length}</b> guesses</span>`;
  try { BG.answers = await api(`/api/bingo/answers?seed=${g.seed}`); } catch(e){ /* still finished */ }
  bgRender();
  if (fresh) $('#bgDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

// The text is the server's (`share_text`), never assembled here, so its wording cannot drift
// from the scoring (A129). The ladder: the phone's share sheet, then the clipboard, then
// showing the text so it can still be copied by hand.
async function bgShare(){
  const text = BG.state && BG.state.share;
  if (!text){ slip('Finish the grid first.'); return; }
  if (navigator.share){
    try { await navigator.share({text}); return; }
    catch(e){ if (e && e.name === 'AbortError') return; }    // closing the sheet is not an error
  }
  try { await navigator.clipboard.writeText(text); slip('Result copied. It has no names in it, so post it anywhere.'); }
  catch(e){ slip(text); }
}

async function bgLink(){
  const url = `${BG_ORIGIN}/bingo?seed=${BG.grid.seed}`;
  try { await navigator.clipboard.writeText(url); slip('Link copied.'); }
  catch(e){ slip(url); }
}

// --- boot ---------------------------------------------------------------------------------

(async function bgBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const seeded = new URLSearchParams(location.search).get('seed');
  await bgMode(seeded ? 'practice' : 'daily');
  bgPlayers().catch(() => {});       // ready before the first tap, off the critical path
})();
