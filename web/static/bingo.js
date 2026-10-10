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

let BG = {mode: 'daily', grid: null, guesses: [], state: null, sel: null,
          players: null, answers: null, busy: false, matches: [], active: 0};

// --- storage: the guess list per grid, and the days a daily was finished ------------------

const bgStore = () => pzStore(BG_KEY);
const bgSave = patch => pzSave(BG_KEY, patch);

function bgPersist(){
  const g = BG.grid;
  if (BG.mode === 'daily') bgSave({daily: {date: g.date, guesses: BG.guesses}});
  else bgSave({practice: {seed: g.seed, guesses: BG.guesses}});
}

function bgPaintStreak(){
  const el = $('#bgStreak');
  el.innerHTML = BG.mode === 'daily' && BG.grid
    ? pzStreakHtml(bgStore().days || [], BG.grid.date, 'grid') : '';
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
      const r = await api('/api/bingo/play', {method: 'POST', retry: true, headers: PZ_JSON,
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

const bgPlayers = pzPlayers;

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
  const placed = new Set(BG.state.placed.map(p => p.person_id));
  const triedHere = new Set(BG.state.wrong.filter(w => w.cell === BG.sel).map(w => w.person_id));
  const m = pzMatches($('#bgInput').value,
    p => placed.has(p.id) ? 'on the grid' : triedHere.has(p.id) ? 'tried here' : '');
  BG.matches = m.list.map(x => x.p); BG.active = m.active;
  $('#bgResults').innerHTML = pzResultsHtml(m, 'bgGuess',
    'Nobody by that name. Names are as the scorecard prints them: V Kohli.');
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
    const r = await api('/api/bingo/play', {method: 'POST', retry: true, headers: PZ_JSON,
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
  if (BG.mode === 'daily')
    pzRankDaily('bingo', {seed: g.seed, cells: BG.guesses.map(x => ({cell: x[0], id: x[1]}))}, g.date);
  try { BG.answers = await api(`/api/bingo/answers?seed=${g.seed}`); } catch(e){ /* still finished */ }
  bgRender();
  if (fresh) $('#bgDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

const bgShare = () => pzShare(BG.state && BG.state.share);
const bgLink = () => pzCopyLink(`${PZ_ORIGIN}/bingo?seed=${BG.grid.seed}`);

// --- boot ---------------------------------------------------------------------------------

(async function bgBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const seeded = new URLSearchParams(location.search).get('seed');
  await bgMode(seeded ? 'practice' : 'daily');
  bgPlayers().catch(() => {});       // ready before the first tap, off the critical path
})();
