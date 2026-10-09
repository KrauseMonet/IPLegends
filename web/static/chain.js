// Teammate Chain: how long can your IPL knowledge hold? [A184]
//
// Same shape as Bingo and Guess the Player: the server owns the rules, the guesses so far
// are the whole state, and each move sends the list and gets the whole position back. What
// `puzzle.js` provides -- storage, streaks, name search, sharing -- is not repeated here.

const CH_KEY = 'iplegends_chain_v1';

let CH = {mode: 'daily', puzzle: null, guesses: [], stopped: false, state: null,
          matches: [], active: 0, busy: false};

const chStore = () => pzStore(CH_KEY);
const chSave = patch => pzSave(CH_KEY, patch);

function chPersist(){
  const p = CH.puzzle, rec = {guesses: CH.guesses, stopped: CH.stopped};
  if (CH.mode === 'daily') chSave({daily: Object.assign({date: p.date}, rec)});
  else chSave({practice: Object.assign({seed: p.seed}, rec)});
}

function chPaintStreak(){
  const best = chStore().best;
  const streak = CH.mode === 'daily' && CH.puzzle
    ? pzStreakHtml(chStore().days || [], CH.puzzle.date, 'chain') : '';
  $('#chStreak').innerHTML = [streak, best ? `Your longest chain: <b>${best}</b>.` : '']
    .filter(Boolean).join(' ');
}

// --- loading ------------------------------------------------------------------------------

function chEmptyState(){
  return {chain: [CH.puzzle.start], links: 0, clubs: 0, strikes: 0, strikes_left: CH.puzzle.strikes,
          tried_here: [], finished: false, stopped: false, missed: [], share: null};
}

async function chFetchPuzzle(mode, fresh){
  if (mode === 'daily') return api('/api/chain/today');
  const urlSeed = new URLSearchParams(location.search).get('seed');
  const saved = chStore().practice;
  const seed = !fresh && urlSeed ? urlSeed : (!fresh && saved ? saved.seed : null);
  return api('/api/chain' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

async function chMode(mode, fresh){
  if (CH.busy) return;
  CH.mode = mode;
  document.querySelectorAll('#chTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));
  try {
    const puzzle = await chFetchPuzzle(mode, fresh);
    await chStart(puzzle, fresh);
    history.replaceState(null, '', mode === 'daily' ? '/chain' : '/chain?seed=' + puzzle.seed);
  } catch(e){ slip(e.message); }
}

async function chStart(puzzle, fresh){
  CH.puzzle = puzzle;
  const s = chStore();
  const saved = CH.mode === 'daily'
    ? (s.daily && s.daily.date === puzzle.date ? s.daily : null)
    : (!fresh && s.practice && String(s.practice.seed) === String(puzzle.seed) ? s.practice : null);
  CH.guesses = saved ? saved.guesses || [] : [];
  CH.stopped = !!(saved && saved.stopped);
  CH.state = chEmptyState();
  if (CH.guesses.length || CH.stopped){
    try {
      const r = await api('/api/chain/play', {method: 'POST', retry: true, headers: PZ_JSON,
        body: JSON.stringify({seed: puzzle.seed, guesses: CH.guesses, stop: CH.stopped})});
      CH.state = r.state;
    } catch(e){ CH.guesses = []; CH.stopped = false; }     // a list the server refuses is dropped
  }
  chPersist();
  chRender();
  if (CH.state.finished) chFinish(false);
}

// --- rendering ----------------------------------------------------------------------------

function chCrests(urls){
  return urls.map(u => `<img class="gp-club" src="${esc(u)}" alt="" width="22" height="22">`).join('');
}

function chStepHtml(step, i, isEnd, fresh){
  const link = step.shared.length
    ? `<div class="ch-link">${step.shared.map(esc).join(' · ')}</div>` : '';
  return `${link}<div class="ch-step${isEnd ? ' end' : ''}${fresh ? ' fresh' : ''}">
    <span class="ch-n">${i === 0 ? 'START' : i}</span>
    <b>${esc(step.name)}</b><span class="gp-clubs">${chCrests(step.crests)}</span></div>`;
}

function chRender(fresh){
  const st = CH.state, n = st.chain.length;
  // The newest link first, so it sits just under the box that made it.
  $('#chSteps').innerHTML = st.chain.map((s, i) => chStepHtml(s, i, i === n - 1, fresh && i === n - 1))
    .reverse().join('');
  const lives = [];
  for (let i = 0; i < CH.puzzle.strikes; i++) lives.push(`<i class="${i < st.strikes ? 'lost' : ''}"></i>`);
  $('#chLives').innerHTML = lives.join('');
  $('#chLinks').textContent = st.links;
  const end = st.chain[n - 1];
  $('#chQ').innerHTML = `Who was a teammate of <b>${esc(end.name)}</b>?
    <span class="gp-clubs">${chCrests(end.crests)}</span>`;
  $('#chPicker').classList.toggle('hide', st.finished);
  $('#chStopRow').classList.toggle('hide', st.finished || st.links === 0);
  $('#chDone').classList.toggle('hide', !st.finished);
  $('#chHint').textContent = st.finished ? '' : st.links === 0 ? 'Same club, same season. Nobody twice.' : '';
  const chal = $('#chChallenge');
  const shared = new URLSearchParams(location.search).get('seed');
  chal.classList.toggle('hide', !(shared && CH.mode === 'practice' && !st.finished && !CH.guesses.length));
  if (!chal.classList.contains('hide')) chal.innerHTML = `Somebody sent you <b>${esc(CH.puzzle.label)}</b>. How far can you get?`;
  chPaintStreak();
}

// --- guessing -----------------------------------------------------------------------------

function chFilter(){
  const inChain = new Set(CH.state.chain.map(s => s.person_id));
  const tried = new Set(CH.state.tried_here);
  const m = pzMatches($('#chInput').value,
    p => inChain.has(p.id) ? 'in the chain' : tried.has(p.id) ? 'tried here' : '');
  CH.matches = m.list.map(x => x.p); CH.active = m.active;
  $('#chResults').innerHTML = pzResultsHtml(m, 'chGuess',
    'Nobody by that name. Names are as the scorecard prints them: V Kohli.');
}

function chKey(e){
  if (!CH.matches.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    CH.active = (CH.active + (e.key === 'ArrowDown' ? 1 : -1) + CH.matches.length) % CH.matches.length;
    document.querySelectorAll('#chResults button').forEach((b, k) => b.classList.toggle('act-row', k === CH.active));
  } else if (e.key === 'Enter'){
    e.preventDefault();
    const p = CH.matches[CH.active];
    if (p) chGuess(p.id);
  }
}

async function chGuess(pid){
  if (CH.busy || CH.state.finished) return;
  CH.busy = true;
  try {
    const r = await api('/api/chain/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({seed: CH.puzzle.seed, guesses: CH.guesses, guess: pid})});
    CH.guesses.push(pid);
    CH.state = r.state;
    chPersist();
    $('#chInput').value = ''; $('#chResults').innerHTML = ''; CH.matches = [];
    chRender(r.linked);
    if (!r.linked){
      const name = (PZ_PLAYERS.find(p => p.id === pid) || {}).name || 'That player';
      const end = r.state.chain[r.state.chain.length - 1].name;
      slip(`${name} never played with ${end}. ${r.state.strikes_left} left.`);
      const box = $('#chPicker'); box.classList.remove('shake'); void box.offsetWidth; box.classList.add('shake');
    }
    if (CH.state.finished) chFinish(true);
    else $('#chInput').focus({preventScroll: true});
  } catch(e){ slip(e.message); }
  finally { CH.busy = false; }
}

async function chStop(){
  if (CH.busy || CH.state.finished) return;
  CH.busy = true;
  try {
    const r = await api('/api/chain/play', {method: 'POST', retry: true, headers: PZ_JSON,
      body: JSON.stringify({seed: CH.puzzle.seed, guesses: CH.guesses, stop: true})});
    CH.stopped = true; CH.state = r.state;
    chPersist(); chRender(); chFinish(true);
  } catch(e){ slip(e.message); }
  finally { CH.busy = false; }
}

// --- finishing ----------------------------------------------------------------------------

function chTitle(n){
  return n >= 25 ? 'A walking encyclopedia' : n >= 15 ? 'Dressing-room royalty' : n >= 8 ? 'A fine chain'
       : n >= 3 ? 'A decent start' : n >= 1 ? 'Short but sweet' : 'Not a single link';
}

function chFinish(fresh){
  const st = CH.state, p = CH.puzzle;
  if (CH.mode === 'daily'){
    const days = chStore().days || [];
    if (!days.includes(p.date)) chSave({days: days.concat(p.date).sort().slice(-400)});
  }
  if (st.links > (chStore().best || 0)) chSave({best: st.links});
  $('#chDoneLabel').textContent = `TEAMMATE CHAIN · ${p.label.toUpperCase()}`;
  $('#chDoneTitle').textContent = chTitle(st.links);
  $('#chFinal').textContent = st.links;
  $('#chStats').innerHTML = `<span><b>${st.clubs}</b> club${st.clubs === 1 ? '' : 's'}</span>`
    + `<span><b>${st.strikes}</b> strike${st.strikes === 1 ? '' : 's'}</span>`;
  const end = st.chain[st.chain.length - 1].name;
  $('#chMissed').innerHTML = st.missed.length
    ? `<div class="gp-name">You could have named, from ${esc(end)}:</div>
       <div class="ch-names">${st.missed.map(n => `<span>${esc(n)}</span>`).join('')}</div>` : '';
  $('#chSharePreview').textContent = st.share || '';
  chPaintStreak();
  if (fresh) $('#chDone').scrollIntoView({block: 'start', behavior: 'smooth'});
}

const chShare = () => pzShare(CH.state && CH.state.share);
const chLink = () => pzCopyLink(`${PZ_ORIGIN}/chain?seed=${CH.puzzle.seed}`);

// --- boot ---------------------------------------------------------------------------------

(async function chBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const seeded = new URLSearchParams(location.search).get('seed');
  await chMode(seeded ? 'practice' : 'daily');
  pzPlayers().catch(() => {});       // ready before the first keystroke, off the critical path
})();
