// Name the XI: a famous match's scorecard with the names blanked [A185].
//
// Same shape as the other puzzle games: the server owns the rules, the guesses so far are the
// whole state, and each move sends the list and gets the whole position back. The clues for
// every blank are in the puzzle from the start; the names are not, until a man is named or the
// puzzle is over. What `puzzle.js` provides -- storage, streaks, name search, sharing -- is not
// repeated here.

const XI_KEY = 'iplegends_xi_v1';

let XI = {mode: 'daily', puzzle: null, guesses: [], gaveUp: false, state: null,
          matches: [], active: 0, busy: false, fresh: null};

const xiStore = () => pzStore(XI_KEY);
const xiSave = patch => pzSave(XI_KEY, patch);

function xiPersist(){
  const p = XI.puzzle, rec = {guesses: XI.guesses, gaveUp: XI.gaveUp};
  if (XI.mode === 'daily') xiSave({daily: Object.assign({date: p.date}, rec)});
  else xiSave({practice: Object.assign({seed: p.seed}, rec)});
}

function xiPaintStreak(){
  const best = xiStore().best;
  const streak = XI.mode === 'daily' && XI.puzzle
    ? pzStreakHtml(xiStore().days || [], XI.puzzle.date, 'match') : '';
  $('#xiStreak').innerHTML = [streak, best ? `Your best: <b>${best}</b> named.` : '']
    .filter(Boolean).join(' ');
}

// --- loading ------------------------------------------------------------------------------

function xiEmptyState(){
  return {slots: XI.puzzle.slots, found: 0, size: XI.puzzle.size, wrong: [],
          mistakes_left: XI.puzzle.mistakes, finished: false, solved: false, share: null};
}

async function xiFetchPuzzle(mode, fresh){
  if (mode === 'daily') return api('/api/xi/today');
  const urlSeed = new URLSearchParams(location.search).get('seed');
  const saved = xiStore().practice;
  const seed = !fresh && urlSeed ? urlSeed : (!fresh && saved ? saved.seed : null);
  return api('/api/xi' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

async function xiMode(mode, fresh){
  if (XI.busy) return;
  XI.mode = mode;
  document.querySelectorAll('#xiTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
  try {
    const puzzle = await xiFetchPuzzle(mode, fresh);
    await xiStart(puzzle, fresh);
    history.replaceState(null, '', mode === 'daily' ? '/xi' : '/xi?seed=' + puzzle.seed);
  } catch(e){ slip(e.message); }
}

async function xiStart(puzzle, fresh){
  XI.puzzle = puzzle; XI.fresh = null;
  const s = xiStore();
  const saved = XI.mode === 'daily'
    ? (s.daily && s.daily.date === puzzle.date ? s.daily : null)
    : (!fresh && s.practice && String(s.practice.seed) === String(puzzle.seed) ? s.practice : null);
  XI.guesses = saved ? saved.guesses || [] : [];
  XI.gaveUp = !!(saved && saved.gaveUp);
  XI.state = xiEmptyState();
  if (XI.guesses.length || XI.gaveUp){
    try {
      const r = await api('/api/xi/play', {method: 'POST', retry: true, headers: PZ_JSON,
        body: JSON.stringify({seed: puzzle.seed, guesses: XI.guesses, give_up: XI.gaveUp})});
      XI.state = r.state;
    } catch(e){ XI.guesses = []; XI.gaveUp = false; }     // a list the server refuses is dropped
  }
  xiPersist();
  xiRenderMatch();
  xiRender();
  if (XI.state.finished) xiFinish(false);
}

// --- rendering ----------------------------------------------------------------------------

function xiTeam(t){
  const crest = t.crest ? `<img src="${esc(t.crest)}" alt="" width="46" height="46">` : '';
  return `<div class="xi-team">${crest}<b>${esc(t.name)}</b></div>`;
}

function xiRenderMatch(){
  const m = XI.puzzle.match, n = XI.puzzle.size;
  const date = new Date(m.date + 'T00:00:00Z').toLocaleDateString('en-GB',
    {day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'});
  $('#xiMatch').innerHTML = `
    <div class="xi-title">${esc(m.title)}</div>
    <div class="xi-teams">${xiTeam(m.team)}<span class="xi-v">v</span>${xiTeam(m.opponent)}</div>
    <div class="xi-meta">${esc(m.result)} · ${esc(date)}${m.venue ? ' · ' + esc(m.venue) : ''}</div>
    <div class="xi-ask">Name <b>${esc(m.team.name)}</b>'s ${n === 11 ? 'XI' : n + ' who played'}${
      n === 11 ? '' : ' (the Impact Player era)'}</div>`;
}

function xiSlotHtml(s, i){
  const clue = [s.bat, s.bowl].filter(Boolean).join(' · ') || 'Did not bat or bowl';
  const pos = s.position ? s.position : '–';
  const name = s.name
    ? `<b>${esc(s.name)}</b>` : '<b class="xi-blank">· · · · · ·</b>';
  const fresh = XI.fresh === i ? ' fresh' : '';
  return `<div class="xi-slot ${s.status}${fresh}"><span class="ch-n">${pos}</span>
    <span class="xi-clue">${esc(clue)}</span>${name}</div>`;
}

function xiRender(){
  const st = XI.state;
  $('#xiSlots').innerHTML = st.slots.map(xiSlotHtml).join('');
  const lives = [];
  for (let i = 0; i < XI.puzzle.mistakes; i++) lives.push(`<i class="${i < st.wrong.length ? 'lost' : ''}"></i>`);
  $('#xiLives').innerHTML = lives.join('');
  $('#xiFound').textContent = st.found;
  $('#xiOf').textContent = '/ ' + st.size;
  $('#xiWrong').innerHTML = st.wrong.map(w =>
    `<span class="xi-chip${w.other_side ? ' other' : ''}" title="${w.other_side ? 'He played in this match, for the other side' : 'He was not in this side'}">${esc(w.name)}${w.other_side ? ' · other side' : ''}</span>`).join('');
  $('#xiPicker').classList.toggle('hide', st.finished);
  $('#xiGiveUpRow').classList.toggle('hide', st.finished || !XI.guesses.length);
  $('#xiDone').classList.toggle('hide', !st.finished);
  $('#xiHint').textContent = st.finished || XI.guesses.length ? '' : 'Anyone who has played in the IPL can be guessed.';
  const chal = $('#xiChallenge');
  const shared = new URLSearchParams(location.search).get('seed');
  chal.classList.toggle('hide', !(shared && XI.mode === 'practice' && !st.finished && !XI.guesses.length));
  if (!chal.classList.contains('hide')) chal.innerHTML = `Somebody sent you <b>${esc(XI.puzzle.label)}</b>. How many can you name?`;
  xiPaintStreak();
}

// --- guessing -----------------------------------------------------------------------------

function xiFilter(){
  const named = new Set((XI.state.slots || []).filter(s => s.name).map(s => s.name));
  const tried = new Set(XI.state.wrong.map(w => w.person_id));
  const m = pzMatches($('#xiInput').value,
    p => named.has(p.name) ? 'named' : tried.has(p.id) ? 'tried' : '');
  XI.matches = m.list.map(x => x.p); XI.active = m.active;
  $('#xiResults').innerHTML = pzResultsHtml(m, 'xiGuess',
    'Nobody by that name. Names are as the scorecard prints them: V Kohli.');
}

function xiKey(e){
  if (!XI.matches.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    XI.active = (XI.active + (e.key === 'ArrowDown' ? 1 : -1) + XI.matches.length) % XI.matches.length;
    document.querySelectorAll('#xiResults button').forEach((b, k) => b.classList.toggle('act-row', k === XI.active));
  } else if (e.key === 'Enter'){
    e.preventDefault();
    const p = XI.matches[XI.active];
    if (p) xiGuess(p.id);
  }
}

async function xiGuess(pid){
  if (XI.busy || XI.state.finished) return;
  XI.busy = true;
  try {
    const before = new Set(XI.state.slots.filter(s => s.name).map(s => s.name));
    const r = await api('/api/xi/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({seed: XI.puzzle.seed, guesses: XI.guesses, guess: pid})});
    XI.guesses.push(pid);
    XI.state = r.state;
    XI.fresh = r.result === 'found' ? r.state.slots.findIndex(s => s.name && !before.has(s.name)) : null;
    xiPersist();
    $('#xiInput').value = ''; $('#xiResults').innerHTML = ''; XI.matches = [];
    xiRender();
    if (r.result !== 'found'){
      const name = (PZ_PLAYERS.find(p => p.id === pid) || {}).name || 'That player';
      slip(r.result === 'other_side'
        ? `${name} played in that match, but for the other side. ${r.state.mistakes_left} left.`
        : `${name} wasn't in that side. ${r.state.mistakes_left} left.`);
      const box = $('#xiPicker'); box.classList.remove('shake'); void box.offsetWidth; box.classList.add('shake');
    }
    if (XI.state.finished) xiFinish(true);
    else $('#xiInput').focus({preventScroll: true});
  } catch(e){ slip(e.message); }
  finally { XI.busy = false; }
}

async function xiGiveUp(){
  if (XI.busy || XI.state.finished || !confirm('Reveal the XI? This ends today\'s match.')) return;
  XI.busy = true;
  try {
    const r = await api('/api/xi/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({seed: XI.puzzle.seed, guesses: XI.guesses, give_up: true})});
    XI.gaveUp = true; XI.state = r.state; XI.fresh = null;
    xiPersist(); xiRender(); xiFinish(true);
  } catch(e){ slip(e.message); }
  finally { XI.busy = false; }
}

// --- finishing ----------------------------------------------------------------------------

function xiTitle(found, size){
  return found === size ? 'The full XI' : found >= size - 2 ? 'Nearly the lot' : found >= size / 2
    ? 'A good memory' : found >= 1 ? 'A few names' : 'A blank scorecard';
}

function xiFinish(fresh){
  const st = XI.state, p = XI.puzzle;
  if (XI.mode === 'daily'){
    const days = xiStore().days || [];
    if (!days.includes(p.date)) xiSave({days: days.concat(p.date).sort().slice(-400)});
  }
  if (st.found > (xiStore().best || 0)) xiSave({best: st.found});
  $('#xiDoneLabel').textContent = `NAME THE XI · ${p.label.toUpperCase()}`;
  $('#xiDoneTitle').textContent = xiTitle(st.found, st.size);
  $('#xiFinal').textContent = st.found;
  $('#xiFinalOf').textContent = `of ${st.size} named`;
  $('#xiSharePreview').textContent = st.share || '';
  xiPaintStreak();
  if (fresh) $('#xiDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

const xiShare = () => pzShare(XI.state && XI.state.share);
const xiLink = () => pzCopyLink(`${PZ_ORIGIN}/xi?seed=${XI.puzzle.seed}`);

// --- boot ---------------------------------------------------------------------------------

(async function xiBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const seeded = new URLSearchParams(location.search).get('seed');
  await xiMode(seeded ? 'practice' : 'daily');
  pzPlayers().catch(() => {});       // ready before the first keystroke, off the critical path
})();
