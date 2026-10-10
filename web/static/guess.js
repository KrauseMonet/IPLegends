// Guess the Player: find the mystery IPL player in eight guesses [A183].
//
// Like Bingo (A182) the server owns the rules and the page owns nothing it cannot rebuild:
// the mystery player is a pure function of the seed, the guesses so far are the whole game
// state, and each move sends the list and gets the whole position back. The mystery is never
// in the page until the puzzle is over. What the shared `puzzle.js` provides -- storage,
// streaks, name search, sharing -- is not repeated here.

const GP_KEY = 'iplegends_guess_v1';

let GP = {mode: 'daily', puzzle: null, guesses: [], gaveUp: false, state: null,
          matches: [], active: 0, busy: false, fresh: -1};

const gpStore = () => pzStore(GP_KEY);
const gpSave = patch => pzSave(GP_KEY, patch);

function gpPersist(){
  const p = GP.puzzle, rec = {guesses: GP.guesses, gaveUp: GP.gaveUp};
  if (GP.mode === 'daily') gpSave({daily: Object.assign({date: p.date}, rec)});
  else gpSave({practice: Object.assign({seed: p.seed}, rec)});
}

function gpPaintStreak(){
  $('#gpStreak').innerHTML = GP.mode === 'daily' && GP.puzzle
    ? pzStreakHtml(gpStore().days || [], GP.puzzle.date, 'puzzle') : '';
}

// --- loading ------------------------------------------------------------------------------

function gpEmptyState(){
  return {rows: [], guesses_left: 8, solved: false, finished: false, answer: null, share: null};
}

async function gpFetchPuzzle(mode, fresh){
  if (mode === 'daily') return api('/api/guess/today');
  const urlSeed = new URLSearchParams(location.search).get('seed');
  const saved = gpStore().practice;
  const seed = !fresh && urlSeed ? urlSeed : (!fresh && saved ? saved.seed : null);
  return api('/api/guess' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

async function gpMode(mode, fresh){
  if (GP.busy) return;
  GP.mode = mode;
  document.querySelectorAll('#gpTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
  try {
    const puzzle = await gpFetchPuzzle(mode, fresh);
    await gpStart(puzzle, fresh);
    history.replaceState(null, '', mode === 'daily' ? '/guess' : '/guess?seed=' + puzzle.seed);
  } catch(e){ slip(e.message); }
}

async function gpStart(puzzle, fresh){
  GP.puzzle = puzzle; GP.fresh = -1;
  const s = gpStore();
  const saved = GP.mode === 'daily'
    ? (s.daily && s.daily.date === puzzle.date ? s.daily : null)
    : (!fresh && s.practice && String(s.practice.seed) === String(puzzle.seed) ? s.practice : null);
  GP.guesses = saved ? saved.guesses || [] : [];
  GP.gaveUp = !!(saved && saved.gaveUp);
  GP.state = gpEmptyState();
  if (GP.guesses.length || GP.gaveUp){
    try {
      const r = await api('/api/guess/play', {method: 'POST', retry: true, headers: PZ_JSON,
        body: JSON.stringify({seed: puzzle.seed, guesses: GP.guesses, give_up: GP.gaveUp})});
      GP.state = r.state;
    } catch(e){
      // A stored list the server now refuses (a pool that changed, a corrupt entry) is
      // dropped rather than left to wedge the page.
      GP.guesses = []; GP.gaveUp = false;
    }
  }
  gpPersist();
  gpRender();
  if (GP.state.finished) gpFinish(false);
}

// --- rendering ----------------------------------------------------------------------------

function gpClub(c){
  const name = esc(c.franchise);
  return c.crest
    ? `<img class="gp-club ${c.shared ? 'shared' : ''}" src="${esc(c.crest)}" alt="${name}" title="${name}" width="26" height="26">`
    : `<span class="gp-club ${c.shared ? 'shared' : ''}" title="${name}">${name.slice(0, 3)}</span>`;
}

function gpTile(t, n, fresh){
  const arrow = t.arrow ? `<em>${t.arrow === 'up' ? '▲' : '▼'}</em>` : '';
  const body = t.key === 'teams'
    ? `<span class="gp-clubs">${t.clubs.map(gpClub).join('')}</span>`
    : `<b>${esc(t.value)}</b>${arrow}`;
  return `<div class="gp-tile ${t.status}${fresh ? ' fresh' : ''}" style="--i:${n}">
    <i>${esc(t.label)}</i>${body}</div>`;
}

function gpRowHtml(row, idx, fresh){
  return `<div class="gp-row"><div class="gp-name">${esc(row.name)}</div>
    <div class="gp-tiles">${row.tiles.map((t, n) => gpTile(t, n, fresh)).join('')}</div></div>`;
}

function gpRender(){
  const st = GP.state;
  // The newest guess first, so it sits just under the box that made it.
  $('#gpRows').innerHTML = st.rows.map((r, i) => gpRowHtml(r, i, i === GP.fresh))
    .reverse().join('');
  let pips = st.rows.map(() => '<i class="used"></i>');
  if (st.solved) pips[pips.length - 1] = '<i class="right"></i>';
  while (pips.length < GP.puzzle.guesses) pips.push('<i></i>');
  $('#gpPips').innerHTML = pips.join('');
  $('#gpLeft').textContent = st.finished ? '' : `${st.guesses_left} guess${st.guesses_left === 1 ? '' : 'es'} left`;
  $('#gpPicker').classList.toggle('hide', st.finished);
  $('#gpGiveUpRow').classList.toggle('hide', st.finished || !st.rows.length);
  $('#gpDone').classList.toggle('hide', !st.finished);
  const chal = $('#gpChallenge');
  const shared = new URLSearchParams(location.search).get('seed');
  chal.classList.toggle('hide', !(shared && GP.mode === 'practice' && !st.finished && !GP.guesses.length));
  if (!chal.classList.contains('hide')) chal.innerHTML = `Somebody sent you <b>${esc(GP.puzzle.label)}</b>. Can you find him?`;
  $('#gpHint').textContent = st.finished || st.rows.length ? '' : 'Guess any player who has appeared in the IPL.';
  gpPaintStreak();
}

// --- guessing -----------------------------------------------------------------------------

function gpFilter(){
  const made = new Set(GP.guesses);
  const m = pzMatches($('#gpInput').value, p => made.has(p.id) ? 'guessed' : '');
  GP.matches = m.list.map(x => x.p); GP.active = m.active;
  $('#gpResults').innerHTML = pzResultsHtml(m, 'gpGuess',
    'Nobody by that name. Names are as the scorecard prints them: V Kohli.');
}

function gpKey(e){
  if (!GP.matches.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    GP.active = (GP.active + (e.key === 'ArrowDown' ? 1 : -1) + GP.matches.length) % GP.matches.length;
    document.querySelectorAll('#gpResults button').forEach((b, k) => b.classList.toggle('act-row', k === GP.active));
  } else if (e.key === 'Enter'){
    e.preventDefault();
    const p = GP.matches[GP.active];
    if (p) gpGuess(p.id);
  }
}

async function gpGuess(pid){
  if (GP.busy || GP.state.finished) return;
  GP.busy = true;
  try {
    const r = await api('/api/guess/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({seed: GP.puzzle.seed, guesses: GP.guesses, guess: pid})});
    GP.guesses.push(pid);
    GP.state = r.state;
    GP.fresh = r.state.rows.length - 1;
    gpPersist();
    $('#gpInput').value = ''; $('#gpResults').innerHTML = ''; GP.matches = [];
    gpRender();
    if (GP.state.finished) gpFinish(true);
    else $('#gpInput').focus({preventScroll: true});
  } catch(e){ slip(e.message); }
  finally { GP.busy = false; }
}

async function gpGiveUp(){
  if (GP.busy || GP.state.finished || !confirm('Reveal the answer? This ends today\'s puzzle.')) return;
  GP.busy = true;
  try {
    const r = await api('/api/guess/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({seed: GP.puzzle.seed, guesses: GP.guesses, give_up: true})});
    GP.gaveUp = true; GP.state = r.state; GP.fresh = -1;
    gpPersist(); gpRender(); gpFinish(true);
  } catch(e){ slip(e.message); }
  finally { GP.busy = false; }
}

// --- finishing ----------------------------------------------------------------------------

function gpFinish(fresh){
  const st = GP.state, p = GP.puzzle;
  if (GP.mode === 'daily'){
    const days = gpStore().days || [];
    if (!days.includes(p.date)) gpSave({days: days.concat(p.date).sort().slice(-400)});
  }
  const n = st.rows.length;
  $('#gpDoneLabel').textContent = `GUESS THE PLAYER · ${p.label.toUpperCase()}`;
  $('#gpDoneTitle').textContent = st.solved
    ? (n === 1 ? 'First guess!' : `Got him in ${n}`) : 'Not this time';
  const a = st.answer;
  $('#gpAnswer').innerHTML = a
    ? `<div class="gp-name">${st.solved ? 'It was' : 'The player was'} <b>${esc(a.name)}</b></div>
       <div class="gp-tiles">${a.tiles.map((t, i) => gpTile(t, i, false)).join('')}</div>` : '';
  $('#gpSharePreview').textContent = st.share || '';
  gpPaintStreak();
  if (GP.mode === 'daily') pzRankDaily('guess', {seed: p.seed, ids: GP.guesses, gave_up: GP.gaveUp}, p.date);
  if (fresh) $('#gpDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

const gpShare = () => pzShare(GP.state && GP.state.share);
const gpLink = () => pzCopyLink(`${PZ_ORIGIN}/guess?seed=${GP.puzzle.seed}`);

// --- boot ---------------------------------------------------------------------------------

(async function gpBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const seeded = new URLSearchParams(location.search).get('seed');
  await gpMode(seeded ? 'practice' : 'daily');
  pzPlayers().catch(() => {});       // ready before the first keystroke, off the critical path
})();
