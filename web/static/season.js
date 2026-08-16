// The solo season page: table, verdict/bracket, journey card, and the match-by-match
// reveal (whose engine -- the over-by-over stepper, scorecard overlay, journey card
// drawing -- lives in reveal.js, shared verbatim with a room's own match screen).
// Reached only from /draft's "Play the season" (a real navigation, `/season?enter=
// ...#draft_state`) or a reload/bookmark of this page's own URL once a season is
// under way.

// "Draft again" starts a brand new draft in the default (Stat) mode -- this page has
// no mode toggle of its own (that choice lives on home, once, before a draft starts),
// so there is nothing truthful to carry forward here.
const DRAFT_MODE = 'stat';

async function newDraft(ctrl){
  await busyClick(ctrl, 'Dealing…', async () => {
    try {
      const s = await api('/api/draft', {method:'POST'});
      location.href = '/draft#' + s.state;
    } catch(e){ slip(e.message); }
  });
}

// A minimal two-screen version of the old all-page `go()` -- this page only ever shows
// its own #result or #reveal, never home/draft/room.
function go(id){
  for (const s of ['result', 'reveal']) $('#' + s).classList.toggle('hide', s !== id);
  window.scrollTo(0, 0);
}

// The full "{draft_state}~{season_moves}" identity of a season in progress. Every
// season-route call below uses it, and render() below keeps it (and the URL hash it
// lives in) in sync as the season advances -- the reload-persistence /season never had
// before this split (see the plan's own "two things this migration must add").
let SEASON_STATE = null;

async function seasonSkip(scope){
  const d = await api(`/api/season/${SEASON_STATE}/skip`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({scope})});
  SEASON_STATE = d.state;
  return d;
}

// Every render function below that lands on a stable (non-reveal) screen re-syncs the
// address bar to the season's own current state, so a reload always resumes from
// exactly here rather than the state /season was first opened with.
function syncHash(){
  history.replaceState(null, '', '#' + SEASON_STATE);
}

let SEASON_DATA = null;

// One save attempt per completed season `state` -- the server's own unique constraint is
// the real idempotency guarantee (A62: saving is a deliberate act, never a side effect of
// a poll/reload), this set just avoids firing the request again on every re-render of an
// already-saved season (e.g. reloading the result screen).
const SAVED_SEASON_STATES = new Set();

async function maybeSaveSeason(d){
  if (!ME || !ME.account_id) return;
  if (SAVED_SEASON_STATES.has(d.state)) return;
  SAVED_SEASON_STATES.add(d.state);
  try { await api(`/api/season/${d.state}/save`, {method: 'POST'}); }
  catch(e){ /* best-effort -- a failed save must never interrupt the result screen */ }
}

function showSeason(d){
  renderTableAndForm(d);
  renderVerdictAndBracket(d);
  go('result');
  syncHash();
  maybeSaveSeason(d);
}

function renderTableAndForm(d){
  SEASON_DATA = d;
  SEASON_STATE = d.state;
  $('#tableNote').innerHTML = `<b>${d.matches_each} each</b><span>matches played</span>`;
  $('#ladder').innerHTML =
    `<thead><tr><th>#</th><th>Side</th><th class="n">P</th><th class="n">W</th><th class="n">L</th>
        <th class="n">Pts</th><th class="n">NRR</th></tr></thead><tbody>` +
    d.table.map(r => `<tr class="${r.you ? 'you' : ''} ${r.pos === 4 ? 'cut' : ''}">
      <td class="n" style="text-align:left">${r.pos}</td><td>${teamBadge(r.short, r.you)}${r.short}</td>
      <td class="n">${r.played}</td><td class="n">${r.won}</td><td class="n">${r.lost}</td>
      <td class="n pts">${r.points}</td>
      ${nrrCell(r.nrr)}</tr>`).join('') + '</tbody>';

  const w = d.your_results.filter(r => r.winner === 'YOU').length;
  const l = d.your_results.filter(r => r.winner && r.winner !== 'YOU').length;
  $('#formNote').innerHTML = `<b>${w} won</b><span>${l} lost</span>`;
  // The sequence, not just the count -- a season's shape lives in the run of results.
  const strip = d.your_results.map(r => {
    const k = r.winner === 'YOU' ? 'w' : (r.winner === null ? 't' : 'l');
    return `<i class="${k}">${k.toUpperCase()}</i>`;
  }).join('');
  $('#yourForm').innerHTML = `<div class="form-strip">${strip}</div>` +
    d.your_results.map((r, i) => {
    const them = r.home === 'YOU' ? r.away : r.home;
    const mine = r.home === 'YOU' ? r.home_score : r.away_score;
    const theirs = r.home === 'YOU' ? r.away_score : r.home_score;
    const k = r.winner === 'YOU' ? 'w' : (r.winner === null ? '' : 'l');
    // Yours bright, theirs dim -- the two used to render identically, so a row gave no
    // way to tell which of "130/10 · 157/7" you had scored.
    return `<div class="fx" onclick="showScorecard('league', ${i})">
      <span class="wl ${k}">${k ? k.toUpperCase() : 'T'}</span>
      <span>v ${them}</span><span class="sc"><span class="mine">${mine}</span>
        <span class="theirs">· ${theirs}</span></span></div>`;
  }).join('');
}

function renderVerdictAndBracket(d){
  SEASON_DATA = d;
  $('#resSeed').textContent = 'Seed ' + d.state.split('-')[0];
  const me = d.table.find(r => r.you);
  const won = d.you_champion;
  $('#resVerdict').textContent = won ? 'Champions'
    : (me.pos <= 4 ? 'Into the playoffs' : 'The season ends here');
  $('#resVerdict').classList.toggle('won', won);
  $('#resScore').textContent = `${me.won}–${me.lost}${me.tied ? '–' + me.tied : ''}`;
  $('#resMargin').textContent = won
    ? `Your eleven take the title, finishing ${ordinal(me.pos)} in the league.`
    : `${ordinal(me.pos)} of ${d.teams} on ${me.points} points. ${d.champion} took the title.`;

  $('#bracket').innerHTML = d.playoffs.map((r, i) => `
    <div class="tie-stage">${r.stage}</div>
    <div class="fx" onclick="showScorecard('playoffs', ${i})">
      <span class="wl ${r.yours ? (r.winner === 'YOU' ? 'w' : 'l') : ''}"
      >${r.yours ? (r.winner === 'YOU' ? 'W' : 'L') : '·'}</span>
      <span>${r.home} v ${r.away}</span>
      <span class="sc">${r.home_score} · ${r.away_score}</span></div>
    <div class="fx" style="border:0;padding-top:2px"><span></span>
      <span class="sc" style="font-style:italic">${r.margin}</span><span></span></div>`).join('');

  $('#capRow').innerHTML = capRowHtml(d.orange_cap, d.orange_cap_runs,
                                       d.purple_cap, d.purple_cap_wickets);
}

function showGroupStageChoice(d){
  const me = d.table.find(r => r.you);
  $('#resSeed').textContent = 'Seed ' + d.state.split('-')[0];
  $('#resVerdict').textContent = 'Group stage complete';
  $('#resVerdict').classList.remove('won');
  $('#resScore').textContent = `${me.won}–${me.lost}${me.tied ? '–' + me.tied : ''}`;
  $('#resMargin').textContent = `${ordinal(me.pos)} of ${d.teams} -- into the playoffs.`;
  $('#bracket').innerHTML = `
    <p class="deck" style="max-width:none">You're through to the knockouts. Play them all
      at once, or one result at a time?</p>
    <div class="actions" style="margin-top:16px">
      <button class="act lead" onclick="revealSkipToEnd(this)">Simulate knockouts</button>
      <button class="act" onclick="enterRevealStage(SEASON_DATA)">Match by match</button>
    </div>`;
  go('result');
  syncHash();
}

function showScorecard(list, i){
  renderScorecard(SEASON_DATA[list === 'league' ? 'your_results' : 'playoffs'][i]);
}

/* --- match-by-match reveal: a real toss, a real Impact choice, a real over-by-over --- */

let REVEAL = null;     // the latest SeasonProgressOut driving these screens, plus `.current`
                        // (the most recently completed match, for showRevealScorecard)

// Entry point for match-by-match mode, and every step after it: `d` is whatever the
// server just returned -- a fresh GET, or the response to a toss/impact/skip POST.
// Its `pending` (if any) decides which screen comes up next; `complete` ends the reveal
// the same way `showSeason` always has.
function enterRevealStage(d){
  REVEAL = d;
  SEASON_STATE = d.state;
  syncHash();
  // A prior ROOM reveal has no bearing here -- solo mode always wants its own
  // per-match skip controls visible.
  $('#tossSkipBtn').classList.remove('hide');
  $('#overSkipMatchBtn').classList.remove('hide');
  go('reveal');
  if (d.complete){ showSeason(d); return; }
  if (d.pending.kind === 'toss'){
    hideAllRevealScreens();
    showTossScreen(d.pending);
  } else {
    // A lost toss never raises `NeedToss` at all (`_play_human_match` only asks when
    // you won it), so this is also where a match that skipped the toss screen entirely
    // first appears -- the first innings is already fully known, so it plays out
    // over-by-over before the break-time choice itself is shown.
    startOverStepper(d.pending.first_innings,
      matchLabel(d.pending.stage, d.your_results.length + 1, d.matches_each),
      () => showImpactScreen(d.pending));
  }
}

function showTossScreen(pending){
  $('#tossStage').textContent = matchLabel(pending.stage, REVEAL.your_results.length + 1, REVEAL.matches_each);
  $('#tossOpponent').textContent = `v ${pending.opponent} -- you called it right.`;
  $('#tossScreen').classList.remove('hide');
}

function handleTossChoice(elects, ctrl){
  return submitToss(elects, ctrl);
}

async function submitToss(elects, ctrl){
  await busyClick(ctrl, elects === 'bat' ? 'Batting…' : 'Bowling…', async () => {
    try {
      const d = await api(`/api/season/${SEASON_STATE}/toss`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({elects})});
      enterRevealStage(d);
    } catch(e){ slip(e.message); }
  });
}

/* --- the innings break: your own Impact Player choice, or decline --------------------- */

function showImpactScreen(pending){
  $('#impactStage').textContent = matchLabel(pending.stage, REVEAL.your_results.length + 1, REVEAL.matches_each);
  const battingSide = pending.human_bats_first ? 'Your eleven' : pending.opponent;
  $('#impactFirstInnings').textContent =
    `${battingSide} posted ${pending.first_innings.runs}/${pending.first_innings.wickets} ` +
    `(${pending.first_innings.overs} ov). You're ${pending.discipline === 'bat' ? 'batting' : 'bowling'} next.`;
  $('#impactXiList').innerHTML = pending.your_xi.map((c, i) => `
    <div class="fx" onclick="submitImpact(${i + 1}, this)" style="cursor:pointer">
      <span>${c.name}</span><span class="sc">slot ${i + 1}</span>
    </div>`).join('');
  $('#impactScreen').classList.remove('hide');
}

async function submitImpact(slot, ctrl){
  const stage = REVEAL.pending.stage;
  // Captured before the network call, from the exact fields showImpactScreen already
  // read one screen earlier -- REVEAL itself is about to be replaced by the response.
  const priorContext = {
    innings: REVEAL.pending.first_innings,
    battingLabel: REVEAL.pending.human_bats_first ? 'Your eleven' : REVEAL.pending.opponent,
  };
  await busyClick(ctrl, slot === null ? 'Declining…' : 'Sending in…', async () => {
    try {
      const d = await api(`/api/season/${SEASON_STATE}/impact`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({slot})});
      SEASON_STATE = d.state;
      // Impact resolves the whole rest of the match in one step -- no further pause is
      // possible until the NEXT match -- so the second innings is already fully known
      // too, and gets its own over-by-over pass before the result is shown.
      const list = stage === 'league' ? d.your_results : d.playoffs;
      const match = list[list.length - 1];
      startOverStepper(match.away_innings,
        matchLabel(stage, d.your_results.length, d.matches_each),
        () => revealCompletedMatch(match, d), priorContext);
    } catch(e){ slip(e.message); }
  });
}

/* --- a completed match's result, live-played or skipped alike ------------------------- */

// A second use of rollDeal's own decelerating-flicker technique (common.js), reusing
// its timer map -- the match result is a genuine "not known until this instant" reveal
// (unlike the toss screen, which only ever shows the winner a choice they already know
// they've earned), so it's the one other place in the reveal flow this fits.
const HEADLINE_DECOYS = ['You win', 'Tied', 'You lose'];
function flickerHeadline(el, finalText, won){
  clearTimeout(ROLL_TIMERS.get(el));
  let i = 0;
  const step = () => {
    if (i >= ROLL_STEP_MS.length){
      el.textContent = finalText;
      el.classList.toggle('won', won);
      ROLL_TIMERS.delete(el);
      return;
    }
    el.textContent = HEADLINE_DECOYS[Math.floor(Math.random() * HEADLINE_DECOYS.length)];
    ROLL_TIMERS.set(el, setTimeout(step, ROLL_STEP_MS[i]));
    i++;
  };
  step();
}

function revealCompletedMatch(match, d){
  REVEAL = d;
  REVEAL.current = match;
  SEASON_STATE = d.state;
  syncHash();
  hideAllRevealScreens();

  const isLeague = match.stage === 'league';
  $('#revealStage').textContent = isLeague
    ? `League · match ${d.your_results.length} of ${d.matches_each}` : match.stage;

  const them = match.home === 'YOU' ? match.away : match.home;
  const mine = match.home === 'YOU' ? match.home_score : match.away_score;
  const theirs = match.home === 'YOU' ? match.away_score : match.home_score;
  const headline = match.winner === 'YOU' ? 'You win'
    : (match.winner === null ? 'Tied' : (isLeague ? `${them} win` : `${match.winner} win`));
  flickerHeadline($('#revealHeadline'), headline, match.winner === 'YOU');
  $('#revealMargin').textContent = match.margin;
  $('#revealLine').innerHTML = isLeague
    ? `<span class="wl ${match.winner === 'YOU' ? 'w' : (match.winner === null ? '' : 'l')}"
        >${match.winner === 'YOU' ? 'W' : (match.winner === null ? 'T' : 'L')}</span>
       <span>v ${them}</span><span class="sc">${mine} · ${theirs}</span>`
    : `<span class="wl ${match.winner === 'YOU' ? 'w' : (match.winner === null ? '' : 'l')}"
        >${match.winner === 'YOU' ? 'W' : (match.winner === null ? 'T' : 'L')}</span>
       <span>${match.home} v ${match.away}</span>
       <span class="sc">${match.home_score} · ${match.away_score}</span>`;

  if (isLeague){
    const w = d.your_results.filter(r => r.winner === 'YOU').length;
    const l = d.your_results.filter(r => r.winner && r.winner !== 'YOU').length;
    const t = d.your_results.length - w - l;
    $('#revealRecord').textContent =
      `${w}W ${l}L${t ? ' ' + t + 'T' : ''} so far, ${d.your_results.length} of ${d.matches_each} played`;
  } else {
    $('#revealRecord').textContent = "You're in this one.";
  }

  const isDone = d.complete;
  $('#revealNextBtn').textContent = isDone ? 'See the result' : 'Next match';
  $('#revealSkipThisBtn').classList.toggle('hide', isDone);
  $('#revealSkipGroupBtn').classList.toggle('hide',
    isDone || !d.pending || d.pending.stage !== 'league');
  $('#revealMatchResult').classList.remove('hide');
}

function revealNext(){
  if (REVEAL.complete) showSeason(REVEAL); else enterRevealStage(REVEAL);
}

// One of the three escape hatches: bypass just the CURRENTLY pending match's toss/
// Impact/over-pacing and jump straight to its result -- available from the toss screen,
// the over stepper, the Impact screen, or a previous match's result screen alike, since
// all four just mean "whatever match is next awaiting me, answer it with the default."
async function revealSkipThisMatch(ctrl){
  const stage = REVEAL && REVEAL.pending ? REVEAL.pending.stage : null;
  clearOverTimer();
  OVER_STEP = null;
  await busyClick(ctrl, 'Skipping…', async () => {
    try {
      const d = await seasonSkip('this_match');
      const list = stage === 'league' ? d.your_results : d.playoffs;
      revealCompletedMatch(list[list.length - 1], d);
    } catch(e){ slip(e.message); }
  });
}

// Mid-reveal escape hatch: seen enough matches one at a time for now, want the rest of
// the group stage at once. Lands exactly where "Group stage first" mode does right
// after its own league skip -- same table, same choice of how to pace the knockouts.
async function revealSkipGroupStage(ctrl){
  await busyClick(ctrl, 'Simulating…', async () => {
    try {
      const d = await seasonSkip('group_stage');
      renderTableAndForm(d);
      if (d.complete){ renderVerdictAndBracket(d); go('result'); syncHash(); }
      else showGroupStageChoice(d);
    } catch(e){ slip(e.message); }
  });
}

// The other escape hatch: resolve every remaining fixture, human or not, with the
// declared defaults, and show the finished season -- same landing point `showSeason`
// always has, whether reached from the reveal screen or the group-stage choice screen.
async function revealSkipToEnd(ctrl){
  await busyClick(ctrl, 'Simulating…', async () => {
    try { showSeason(await seasonSkip('tournament')); }
    catch(e){ slip(e.message); }
  });
}

function showRevealScorecard(){
  if (REVEAL && REVEAL.current) renderScorecard(REVEAL.current);
}

const ordinal = n => n + (['th','st','nd','rd'][(n % 100 - 20) % 10] || ['th','st','nd','rd'][n] || 'th');

function copyLink(){
  // The DRAFT half of SEASON_STATE only ('{draft_state}~{season_moves}') -- this
  // reproduces the same deals, not the same toss/Impact decisions, which is what
  // "copy THIS GAME" has always meant here (a link into /draft, not a season replay).
  const url = location.origin + '/draft#' + SEASON_STATE.split('~')[0];
  navigator.clipboard.writeText(url)
    .then(() => slip('Copied. This link replays the same game.'))
    .catch(() => slip(url));
}

function showJourneyCard(){
  // SEASON_DATA already carries the journey card's own numbers (runs, wickets, top
  // scorer, squad) -- they're folded into the same /api/season replay server-side now,
  // so opening the card is a synchronous re-draw of data already in hand, not a second
  // ~3s simulation behind an unlabelled fetch.
  if (!SEASON_DATA){ slip('Play the season first.'); return; }
  drawJourneyCard(SEASON_DATA);
  $('#cardOverlay').classList.remove('hide');
}

async function boot(){
  loadMe();   // fire-and-forget -- the auth control fills in whenever it resolves
  const m = await loadMeta();
  const s = m.seasons;
  $('#deckPill').textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  $('#footStats').textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
}

boot().then(async () => {
  const enter = new URLSearchParams(location.search).get('enter');
  const hash = location.hash.slice(1);
  if (!hash){ location.href = '/'; return; }
  SEASON_STATE = hash;
  // The `?enter=` marker is a one-time navigation instruction (mirrors draft's own
  // `?mode=` convention) -- strip it immediately so a later reload of this exact URL
  // falls back to the plain, always-correct resume path below instead of re-running a
  // "first action" (skip the whole tournament, skip just the group stage) a second time.
  history.replaceState(null, '', location.pathname + '#' + hash);
  try {
    if (enter === 'reveal'){
      enterRevealStage(await api('/api/season/' + SEASON_STATE));
    } else if (enter === 'groupstage'){
      const d = await seasonSkip('group_stage');
      renderTableAndForm(d);
      if (d.complete){ renderVerdictAndBracket(d); go('result'); syncHash(); }
      else showGroupStageChoice(d);
    } else if (enter === 'whole'){
      showSeason(await seasonSkip('tournament'));
    } else {
      // A reload or a bookmark, mid-season -- ask the server what this exact state
      // means and resume wherever it says, rather than re-deciding a first action.
      const d = await api('/api/season/' + SEASON_STATE);
      if (d.complete) showSeason(d);
      else if (d.pending) enterRevealStage(d);
      else { renderTableAndForm(d); showGroupStageChoice(d); }
    }
  } catch(e){ slip(e.message); location.href = '/'; }
});
