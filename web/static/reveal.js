// The match reveal engine, shared verbatim by solo's /season and a room's own match
// screen -- both pace an already-fully-known over_log locally (no network call per
// over), show the same scorecard overlay, and draw the same journey card off whatever
// SeasonProgressOut-shaped object the caller hands in. Neither page's own script (the
// entry points that decide WHEN to call these -- enterRevealStage vs roomEnterReveal)
// lives here; only the parts with no season/room-specific state do.

let OVER_STEP = null;  // {log, i, timer, speed, paused, onDone, stageText, innings,
                        // priorContext, lastRuns} -- purely local pacing over an
                        // over_log the server already computed in full. `priorContext`
                        // (null for a match's first innings) is {innings, battingLabel}
                        // for the innings already known -- see startOverStepper's own
                        // comment below.

// Not every page carries all four -- room.html's own #reveal only ever has
// tossScreen/overStepper (A81 removed a room's Impact choice, and a room shows its
// match result through #roomResult, never through revealMatchResult), while season.html
// has all four. Each id is checked for existence rather than assumed present, so this
// one shared function serves both markups without a page-specific branch.
function hideAllRevealScreens(){
  ['tossScreen', 'overStepper', 'impactScreen', 'revealMatchResult'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('hide');
  });
}

function matchLabel(stage, n, total){
  return stage === 'league' ? `League · match ${n} of ${total}` : stage;
}

function clearOverTimer(){
  if (OVER_STEP && OVER_STEP.timer){ clearTimeout(OVER_STEP.timer); OVER_STEP.timer = null; }
}

// A batting-order scan for the innings' own headline names, done client-side because the
// server already ships the complete `batting`/`bowling` for a FINISHED innings (this is
// never used on the innings currently ticking over, only on one already fully known) --
// `faced_any` matters here: without it a did-not-bat batter (0 runs off 0 balls) can beat
// a real duck on the tie-break, since both compare equal on runs alone.
function inningsTopBatter(inn){
  const faced = inn.batting.filter(b => b.faced_any);
  if (!faced.length) return null;
  return faced.reduce((a, b) =>
    (b.runs > a.runs || (b.runs === a.runs && b.strike_rate > a.strike_rate)) ? b : a);
}
function inningsTopBowler(inn){
  if (!inn.bowling.length) return null;
  return inn.bowling.reduce((a, b) =>
    (b.wickets > a.wickets || (b.wickets === a.wickets && b.economy < a.economy)) ? b : a);
}

// `priorContext` is null for a match's first-ever innings (nothing to recap) and
// {innings, battingLabel} for every innings after it -- built by the caller from data
// already in hand (REVEAL.pending for solo, the match result's own home innings for a
// room), never re-fetched. "Home always bats first" is enforced server-side (game/
// season.py), so the prior innings is always the one already fully known.
// `matchup` is {bat, bowl}, each {name, crest, kit} -- who is batting and who is bowling
// this innings, for the two team panels. A real franchise shows its crest, a drafted side
// its kit [A151]; optional, so a caller without it still gets a working reveal.
function startOverStepper(innings, stageText, onDone, priorContext, matchup){
  hideAllRevealScreens();
  renderOverMatchup(matchup);
  OVER_STEP = {
    log: innings.over_log, i: 0, timer: null,
    speed: Number($('#overSpeedSelect').value) || 500,
    paused: false, onDone, stageText, innings, priorContext: priorContext || null,
    lastRuns: null, lastCrease: [], milestonesThrough: 0, skipping: false,
  };
  const ms = document.getElementById('overMilestone');
  if (ms){ ms.className = 'sb-milestone'; ms.innerHTML = ''; }
  renderOverPrior();
  $('#overStepper').classList.remove('hide');
  // The screen before (an Impact choice, a long result list) may have left the page
  // scrolled well down; the score is the thing to watch, so bring the board into view.
  const board = document.getElementById('scoreboard');
  if (board && board.getBoundingClientRect().top < 0) board.scrollIntoView({block: 'start'});
  renderOverStep();
  if (OVER_STEP) scheduleNextOver();
}

// Each panel takes its own side's colours -- a `.crest-KEY` class for a franchise, inline
// kit variables for a drafted side -- and the scoreboard as a whole takes the BATTING
// side's, which is what colours the score's accent and the bars of this innings.
function sideTint(side){
  if (!side) return {cls: '', style: ''};
  if (side.crest) return {cls: crestClass(side.crest), style: ''};
  if (side.kit) return {cls: '', style: kitStyle(side.kit)};
  return {cls: '', style: ''};
}

function renderOverMatchup(m){
  const board = document.getElementById('scoreboard');
  if (!board) return;
  const paint = (el, side, role) => {
    el.className = `sb-team sb-${role}`;
    el.removeAttribute('style');
    if (!side){ el.innerHTML = ''; return; }
    const t = sideTint(side);
    if (t.cls) el.classList.add(t.cls);
    if (t.style) el.setAttribute('style', t.style);
    el.innerHTML = `<div class="sb-badge">${sideBadge(side, 'sb-mark')}</div>
      <div class="sb-name"><b>${esc(side.name)}</b><em>${role === 'bat' ? 'Batting' : 'Bowling'}</em></div>`;
  };
  paint(document.getElementById('sbBat'), m && m.bat, 'bat');
  paint(document.getElementById('sbBowl'), m && m.bowl, 'bowl');
  board.className = 'sb';
  board.removeAttribute('style');
  const t = sideTint(m && m.bat);
  if (t.cls) board.classList.add(t.cls);
  if (t.style) board.setAttribute('style', t.style);
}

// Painted once, at the start of the reveal, and left alone -- unlike renderOverStep this
// never changes tick to tick, since the prior innings is already fully resolved.
function renderOverPrior(){
  const p = OVER_STEP.priorContext;
  $('#overPrior').classList.toggle('hide', !p);
  if (!p) return;
  $('#overPriorScore').textContent =
    `${p.battingLabel} ${p.innings.runs}/${p.innings.wickets} (${p.innings.overs} ov)`;
  const bat = inningsTopBatter(p.innings), bowl = inningsTopBowler(p.innings);
  $('#overPriorBest').textContent = [
    bat ? `${bat.name} ${bat.runs}${bat.out ? '' : '*'} (${bat.balls})` : null,
    bowl ? `${bowl.name} ${bowl.wickets}/${bowl.runs}` : null,
  ].filter(Boolean).join(' · ');
}

// The live target line, recomputed every tick against whatever runs/balls this render
// represents -- `isFinal` marks the moment the over_log has run out (a partial final over
// is never logged, so this is exactly where a chase actually resolves) and is what tells
// "still chasing, plenty of balls left on paper" apart from "innings over, fell short".
function renderChaseLine(priorContext, runsNow, ballsNow, isFinal){
  const el = $('#overTargetLine');
  if (!priorContext){ el.textContent = ''; return; }
  const target = priorContext.innings.runs + 1;
  const remaining = target - runsNow;
  const ballsLeft = 120 - ballsNow;
  if (remaining <= 0){
    el.textContent = 'Target chased down';
  } else if (ballsLeft <= 0 || isFinal){
    el.textContent = `Fell short by ${remaining} run${remaining === 1 ? '' : 's'}`;
  } else {
    const rrr = (remaining / (ballsLeft / 6)).toFixed(2);
    el.textContent = `Need ${remaining} off ${ballsLeft} ball${ballsLeft === 1 ? '' : 's'} (RRR ${rrr})`;
  }
}

// Re-triggering a CSS animation on a REPEAT tick (two good overs back to back) needs the
// element reflowed between removing and re-adding its class, not just toggled -- toggling
// alone is a no-op when the class is already present. A wicket outranks a plain score
// bump when both are true in the same over.
function pulseScoreLine(wicketFell, scoreChanged){
  const el = $('#overScoreLine');
  el.classList.remove('score-pop', 'wicket-flash');
  void el.offsetWidth;
  if (wicketFell) el.classList.add('wicket-flash');
  else if (scoreChanged) el.classList.add('score-pop');
}

// The innings as twenty bars, one per over, drawn in the batting side's colour -- height
// is that over's runs (capped at 24, so one freak over cannot flatten the rest), a red pip
// per wicket above it, and the powerplay and death overs shaded behind. Overs not yet
// bowled stay as empty slots, so the shape of what is left is visible too. Rebuilt whole
// each tick (twenty elements); only the newest bar animates in.
const BAR_CAP = 24;
function renderOverChips(entries){
  const bars = [];
  for (let n = 0; n < 20; n++){
    const o = entries[n];
    const phase = n < 6 ? 'pp' : (n >= 15 ? 'death' : 'mid');
    if (!o){ bars.push(`<div class="sb-bar ${phase} future"><i></i><span>${n + 1}</span></div>`); continue; }
    const h = Math.max(4, 100 * Math.min(o.over_runs, BAR_CAP) / BAR_CAP);
    const pips = o.over_wickets ? `<u>${'<s></s>'.repeat(Math.min(o.over_wickets, 3))}</u>` : '';
    const isNew = n === entries.length - 1;
    bars.push(`<div class="sb-bar ${phase}${isNew ? ' new' : ''}${o.over_runs >= 12 ? ' big' : ''}"
      title="Over ${n + 1}: ${o.over_runs} run${o.over_runs === 1 ? '' : 's'}${o.over_wickets ? ', ' + o.over_wickets + ' wkt' : ''}">
      ${pips}<i style="height:${h}%"><b>${o.over_runs}</b></i><span>${n + 1}</span></div>`);
  }
  $('#overChips').innerHTML = bars.join('');
}

// The batters at the crease, the one on strike marked, and the bowler of the over just
// done. A batter who was not at the crease last time the board was drawn slides in, so a
// wicket reads as a new man arriving rather than a name silently changing.
function renderCrease(batters, bowler){
  const el = document.getElementById('overCrease');
  if (!el) return;
  if (!batters.length){ el.innerHTML = ''; OVER_STEP.lastCrease = []; return; }
  const before = OVER_STEP.lastCrease;
  const rows = batters.map(b => {
    const bits = [];
    if (b.fours) bits.push(`${b.fours}×4`);
    if (b.sixes) bits.push(`${b.sixes}×6`);
    const sr = b.balls ? (100 * b.runs / b.balls).toFixed(0) : '–';
    const fresh = before.length && !before.includes(b.name);
    return `<div class="sb-bat-row${b.onStrike ? ' on' : ''}${fresh ? ' fresh' : ''}${b.runs >= 50 ? ' landmark' : ''}">
      <span class="sb-strike" aria-label="${b.onStrike ? 'on strike' : ''}">${b.onStrike ? '▸' : ''}</span>
      <b>${esc(b.name)}</b>
      <span class="sb-fig"><strong>${b.runs}${b.out ? '' : '*'}</strong> <em>(${b.balls})</em></span>
      <span class="sb-extra">${bits.join(' ')}${bits.length ? ' · ' : ''}SR ${sr}</span>
    </div>`;
  });
  const bowl = bowler
    ? `<div class="sb-bowl-row"><span>Bowling</span><b>${esc(bowler.name)}</b>
        <span class="sb-fig"><strong>${bowler.wickets}-${bowler.runs}</strong>
        <em>(${oversText(bowler.balls)} ov)</em></span></div>`
    : '';
  el.innerHTML = `<div class="sb-bats">${rows.join('')}</div>${bowl}`;
  OVER_STEP.lastCrease = batters.map(b => b.name);
}

// The two not-out batters once the innings is over. Batters come in in order, so the ones
// who came in are the first wickets+2 of the order; whoever of those is not out is still
// there. Nobody is marked on strike -- the innings is over.
function finalCrease(inn){
  const came = inn.batting.slice(0, Math.min(inn.wickets + 2, inn.batting.length));
  return came.filter(b => !b.out).map(b => ({...b, out: false, onStrike: false}));
}

const MILESTONE_TEXT = {
  fifty: m => ['Fifty', `${m.name} · ${m.runs} (${m.faced})`],
  hundred: m => ['Hundred!', `${m.name} · ${m.runs} (${m.faced})`],
  hattrick: m => ['Hat-trick!', `${m.name} · three in three`],
  five_for: m => ['Five-for', `${m.name} · ${m.wickets}/${m.runs}`],
  three_in_over: m => ['Three in the over', `${m.name} · ${m.wickets}/${m.runs}`],
};
const MILESTONE_RANK = ['hundred', 'hattrick', 'five_for', 'fifty', 'three_in_over'];
const MILESTONE_HOLD = 1600;   // ms the reveal waits so a banner can be read

// Fires every milestone reached after the last one shown and on or before `balls`, and
// returns whether any did. Several in one over (a fifty and a five-for, say) share one
// banner, the rarest leading.
function fireMilestones(balls){
  const all = (OVER_STEP.innings.milestones || []);
  const due = all.filter(m => m.balls > OVER_STEP.milestonesThrough && m.balls <= balls);
  OVER_STEP.milestonesThrough = Math.max(OVER_STEP.milestonesThrough, balls);
  const el = document.getElementById('overMilestone');
  if (!due.length || !el || OVER_STEP.skipping) return false;
  due.sort((a, b) => MILESTONE_RANK.indexOf(a.kind) - MILESTONE_RANK.indexOf(b.kind));
  const lead = due[0].kind;
  el.innerHTML = due.map(m => {
    const [title, line] = (MILESTONE_TEXT[m.kind] || (() => [m.kind, m.name]))(m);
    return `<div class="sb-ms-item"><b>${esc(title)}</b><span>${esc(line)}</span></div>`;
  }).join('');
  el.className = 'sb-milestone';
  void el.offsetWidth;
  el.className = `sb-milestone show ms-${lead}`;
  return true;
}

function oversText(balls){ return `${Math.floor(balls / 6)}.${balls % 6}`; }
function runRate(runs, balls){ return balls ? (runs * 6 / balls).toFixed(2) : '0.00'; }

function renderOverStep(){
  const { log, i, stageText, innings, priorContext } = OVER_STEP;
  $('#overStage').textContent = stageText;
  if (i >= log.length){
    // either a genuinely over-free innings, or the log just ran out -- either way the
    // final total is already known, so show it and hand off immediately.
    $('#overScoreLine').textContent = `${innings.runs}/${innings.wickets}`;
    $('#sbOvers').textContent = `${innings.overs} ov · RR ${runRate(innings.runs, innings.balls)}`;
    $('#overLastLine').textContent = `Innings over · ${innings.extras} extras`;
    $('#overOversBar').style.width = (100 * Math.min(1, innings.balls / 120)) + '%';
    renderChaseLine(priorContext, innings.runs, innings.balls, true);
    renderOverChips(log);
    renderCrease(finalCrease(innings), null);
    // A milestone in the last, partial over (the winning hit that brings up a hundred)
    // is only reachable here, so the board holds on the final score long enough to show
    // it -- unless the viewer skipped, who asked for the end and not a celebration.
    if (fireMilestones(Infinity)){
      clearOverTimer();
      OVER_STEP.timer = setTimeout(finishOverStepper, MILESTONE_HOLD + 600);
      return;
    }
    finishOverStepper();
    return;
  }
  const o = log[i];
  const scoreChanged = OVER_STEP.lastRuns !== null && o.runs !== OVER_STEP.lastRuns;
  OVER_STEP.lastRuns = o.runs;
  $('#overScoreLine').textContent = `${o.runs}/${o.wickets}`;
  $('#sbOvers').textContent = `${oversText(o.balls)} ov · RR ${runRate(o.runs, o.balls)}`;
  $('#overLastLine').textContent =
    `Over ${o.over + 1} · ${o.bowler} · ${o.over_runs} run${o.over_runs === 1 ? '' : 's'}` +
    (o.over_wickets ? `, ${o.over_wickets} wicket${o.over_wickets === 1 ? '' : 's'}` : '');
  $('#overOversBar').style.width = (100 * Math.min(1, o.balls / 120)) + '%';
  renderChaseLine(priorContext, o.runs, o.balls, false);
  pulseScoreLine(o.over_wickets > 0, scoreChanged);
  renderOverChips(log.slice(0, i + 1));
  if (o.striker){
    renderCrease(
      [{...o.striker, onStrike: true}, {...o.non_striker, onStrike: false}],
      o.bowler_balls ? {name: o.bowler, balls: o.bowler_balls, runs: o.bowler_runs,
                        wickets: o.bowler_wickets} : null);
  }
  OVER_STEP.hold = fireMilestones(o.balls) ? MILESTONE_HOLD : 0;
}

function scheduleNextOver(){
  clearOverTimer();
  if (!OVER_STEP || OVER_STEP.paused) return;
  const hold = OVER_STEP.hold || 0;
  OVER_STEP.hold = 0;
  OVER_STEP.timer = setTimeout(() => {
    if (!OVER_STEP) return;
    OVER_STEP.i++;
    const more = OVER_STEP.i < OVER_STEP.log.length;
    renderOverStep();
    if (more) scheduleNextOver();
  }, OVER_STEP.speed + hold);
}

function toggleOverPause(){
  if (!OVER_STEP) return;
  OVER_STEP.paused = !OVER_STEP.paused;
  $('#overPauseBtn').textContent = OVER_STEP.paused ? 'Resume' : 'Pause';
  if (OVER_STEP.paused) clearOverTimer(); else scheduleNextOver();
}

// The speed setting is a preference, not a per-match choice -- it used to reset to
// Normal on every page load, so anyone who prefers Fast re-picked it at the start of
// every session (and a league room shows a viewer fifteen of their own matches). Stored
// under the one key every reveal page reads -- season, room and daily [A148].
//
// A first-time player has nothing stored, so they get NORMAL: the markup's `selected`
// option, never a value this code picks. Nothing is written until the player changes the
// control (`setOverSpeed`), so merely watching at Normal never pins it, and the selects
// carry autocomplete="off" so a browser's own form restore cannot stand in for a choice.
const OVER_SPEED_KEY = 'iplegends_reveal_speed';

function restoreOverSpeed(){
  const sel = document.getElementById('overSpeedSelect');
  if (!sel) return;
  let saved = null;
  try { saved = localStorage.getItem(OVER_SPEED_KEY); } catch(e){ saved = null; }
  // Only a value the select actually offers -- a stale or hand-edited entry must not
  // leave the control showing one speed while the stepper runs at another.
  if (saved && [...sel.options].some(o => o.value === saved)) sel.value = saved;
}

function setOverSpeed(ms){
  try { localStorage.setItem(OVER_SPEED_KEY, String(ms)); } catch(e){ /* private mode */ }
  if (!OVER_STEP) return;
  OVER_STEP.speed = Number(ms);
  if (!OVER_STEP.paused){ clearOverTimer(); scheduleNextOver(); }
}

function skipOverStepper(){
  if (!OVER_STEP) return;
  clearOverTimer();
  OVER_STEP.i = OVER_STEP.log.length;
  OVER_STEP.skipping = true;
  renderOverStep();
}

function finishOverStepper(){
  clearOverTimer();
  const onDone = OVER_STEP && OVER_STEP.onDone;
  OVER_STEP = null;
  hideAllRevealScreens();
  if (onDone) onDone();
}

/* --- whose side is whose [A151] --------------------------------------------------------- */

// The viewer's own side as this page knows it -- {short, name, crest, kit}. The engine
// calls a solo side 'YOU' and a daily side 'You'; the page sets this so every screen that
// draws a result shows the player's team name and kit instead. Null where a page has no
// side of its own to substitute (a room names every side server-side already).
let MY_SIDE = null;

// A side named in a result, resolved to what should be drawn for it. `kit` is supplied
// by a room, where each drafted side's kit travels with the result.
function resolveSide(short, crest, kit){
  if (MY_SIDE && short === MY_SIDE.short) return {...MY_SIDE};
  return {short, name: kit ? kit.name : short, crest: crest || null, kit: kit || null};
}

// The small mark that sits before a side's name in a row or a caption.
function sideMark(side){
  if (side.crest) return crestImg(side.crest, 'row-crest');
  if (side.kit) return kitBadge(side.kit, 'kit-badge-row');
  return '';
}

/* --- the scorecard overlay -------------------------------------------------------- */

function renderScorecard(r){
  if (!r || !r.home_innings || !r.away_innings) return;
  const home = resolveSide(r.home, r.home_crest, r.home_kit);
  const away = resolveSide(r.away, r.away_crest, r.away_kit);
  const winner = r.winner === r.home ? home : (r.winner === r.away ? away : null);
  $('#scStage').textContent = r.stage;
  $('#scHeadline').textContent = winner ? `${winner.name} win` : 'Match tied';
  $('#scHeadline').classList.toggle('won', !!r.winner);
  $('#scMargin').textContent = r.margin;
  $('#scInnings').innerHTML =
    scorecardInnings(home, r.home_score, r.home_innings) +
    scorecardInnings(away, r.away_score, r.away_innings);
  $('#scSuperOver').innerHTML = superOverBlock(r.super_overs);
  $('#scorecardOverlay').classList.remove('hide');
}

// A super over is six balls, three batters and one bowler a side -- a full scorecard
// table for that is mostly empty columns and "did not bat" rows, so it gets its own
// compact form rather than being pushed through `scorecardInnings`. It sits BELOW the
// two innings and outside `.cards2`: the match is what the two columns are, and the
// super over is what happened after them.
function superOverBlock(sos){
  if (!sos || !sos.length) return '';
  // The block's own label is dropped when there is only one -- the section heading right
  // above it already says "Super over", and printing it twice reads as a mistake.
  const rows = sos.map(so => `
    <div class="so-block">
      <div class="so-head"><span>${sos.length > 1 ? `Super over ${so.number}` : ''}</span>
        <span class="so-verdict">${so.winner ? `${esc(resolveSide(so.winner).name)} win` : 'tied, played again'}</span></div>
      ${superOverSide(so.first, so.first_score, so.first_innings)}
      ${superOverSide(so.second, so.second_score, so.second_innings)}
    </div>`).join('');
  const head = sos.length > 1 ? `Super overs <em>${sos.length}</em>` : 'Super over';
  return `<div class="so-wrap"><div class="col-head"><span>${head}</span></div>${rows}</div>`;
}

function superOverSide(short, score, inn){
  const label = esc(resolveSide(short).name);
  if (!inn) return `<div class="so-row"><span class="so-team">${label}</span>
    <span class="so-score">${score}</span><span class="so-detail"></span></div>`;
  // Only the batters who actually faced. With three nominated and two dismissals ending
  // the innings, the third man very often does not bat, and a "did not bat" line for him
  // says nothing a reader needs.
  const bats = inn.batting.filter(b => b.faced_any)
    .map(b => `${b.name} ${b.runs}${b.out ? '' : '*'} (${b.balls})`).join(', ');
  const bowl = inn.bowling.map(bo => `${bo.name} ${bo.wickets}-${bo.runs}`).join(', ');
  return `<div class="so-row">
    <span class="so-team">${label}</span>
    <span class="so-score">${score} (${inn.overs})</span>
    <span class="so-detail">${bats}${bowl ? ` &middot; b ${bowl}` : ''}</span></div>`;
}

function hideScorecard(e){
  if (e && e.target !== e.currentTarget) return;   // a click inside the frame stays open
  $('#scorecardOverlay').classList.add('hide');
}

function impactTag(isImpact){
  return isImpact ? ' <span class="impact-tag">IMP</span>' : '';
}

// [A115] A boundary count of zero reads as a dash, not a 0. Ten noughts down a column is
// noise a reader has to filter past to find the two numbers that matter; a dash is the
// same convention this app already uses for "no average yet" and "nothing to report".
// It says none were hit, which is a fact, not that none were measured.
function bdyCell(n){
  return n ? `<td class="n">${n}</td>` : `<td class="n bdy-none">–</td>`;
}

function scorecardInnings(side, score, inn){
  // The innings' best contribution, so a scorecard has a subject rather than being a wall
  // of equally-weighted rows. Ties resolve to whoever appears first, which is batting
  // order -- the earlier man faced his runs under more of the innings.
  const faced = inn.batting.filter(b => b.faced_any);
  const topRuns = faced.length ? Math.max(...faced.map(b => b.runs)) : null;
  let topDone = false;
  const batting = inn.batting.map(b => {
    const isTop = b.faced_any && !topDone && topRuns !== null && b.runs === topRuns && topRuns > 0;
    if (isTop) topDone = true;
    // [A115] `4s` and `6s` sit between balls and strike rate, which is where every real
    // scorecard puts them (R B 4s 6s SR). A `0` is printed as a dim dash rather than a
    // zero: a column of noughts is what makes a scorecard hard to scan, and the two
    // batters who did hit boundaries are the ones the column exists to find.
    return b.faced_any
    ? `<tr class="${isTop ? 'top-bat' : ''}"><td>${b.name}${b.out ? '' : ' *'}${impactTag(b.is_impact)}</td><td class="n">${b.runs}</td>
        <td class="n">${b.balls}</td>${bdyCell(b.fours)}${bdyCell(b.sixes)}<td class="n">${b.strike_rate}</td></tr>`
    : `<tr class="tail"><td>${b.name}${impactTag(b.is_impact)}</td>
        <td class="n" colspan="5" style="font-style:italic">did not bat</td></tr>`;
  }).join('');
  // Boundaries CONCEDED ride in the row's tooltip rather than as two more columns. Seven
  // columns do not fit a phone, and this is a secondary reading of a bowling figure --
  // the primary one, economy, is already there.
  const bowling = inn.bowling.map(bo => `<tr title="${bo.fours} four${
      bo.fours === 1 ? '' : 's'} and ${bo.sixes} six${bo.sixes === 1 ? '' : 'es'} conceded">
    <td>${bo.name}${impactTag(bo.is_impact)}</td>
    <td class="n">${bo.overs}</td><td class="n">${bo.runs}</td>
    <td class="n">${bo.wickets}</td><td class="n">${bo.economy}</td></tr>`).join('');
  const fow = inn.commentary.length ? `<div class="fow">${inn.commentary.join('\n')}</div>` : '';
  return `<div>
    <table>
      <caption>${sideMark(side)}${esc(side.name)} · ${score} (${inn.overs} ov, ${inn.extras} extras) · ${
        inn.fours}x4 ${inn.sixes}x6</caption>
      <tr><th>Batting</th><th class="n">R</th><th class="n">B</th><th class="n">4s</th>
        <th class="n">6s</th><th class="n">SR</th></tr>
      ${batting}
    </table>
    ${bowling ? `<table>
      <tr><th>Bowling</th><th class="n">O</th><th class="n">R</th><th class="n">W</th><th class="n">Econ</th></tr>
      ${bowling}
    </table>` : ''}
    ${fow}
  </div>`;
}

/* --- standings helpers, shared by solo's own ladder and a room's -------------------- */

// Signed and colour-coded the way a broadcast table treats net run rate, rather than a
// plain number identical in weight to every other column.
function nrrCell(v){
  const n = Number(v);
  const cls = n > 0 ? 'nrr-pos' : n < 0 ? 'nrr-neg' : '';
  return `<td class="n ${cls}">${n > 0 ? '+' : ''}${n.toFixed(3)}</td>`;
}

// A table or results row's badge. A real franchise shows its crest; a drafted side its
// kit [A151] -- passed in for another player's side in a room, and your own kit when the
// row is yours and has no crest (an auction side keeps its franchise's crest). Anything
// else falls back to a colour hashed from its name.
function teamBadge(short, isYou, crest, kit){
  if (crest) return `<img class="team-crest" src="${crest}" alt="">`;
  if (kit) return kitBadge(kit, 'kit-badge-row');
  if (isYou) return kitBadge(myKit(), 'kit-badge-row');
  return sideBadge({short});
}

// The tournament's own Orange Cap/Purple Cap, never populated until the season/room is
// actually complete (both callers only have real names to pass once it is), so an empty
// pair renders nothing rather than a blank card. [A153] Each card also names the team the
// winner did it for, with its crest (or kit, or -- when it is the viewer's own side -- the
// viewer's own identity) large on the right, the way a broadcast graphic carries a cap.
function capRowHtml(orangeName, orangeRuns, purpleName, purpleWickets, orangeSide, purpleSide){
  if (!orangeName && !purpleName) return '';
  const card = (cls, label, name, value, capSide) => {
    const side = capSide && capSide.you && MY_SIDE && !capSide.crest ? MY_SIDE
      : capSide ? {name: capSide.team, crest: capSide.crest, kit: capSide.kit} : null;
    const mark = !side ? '' : side.crest
      ? `<img class="cap-mark" src="${side.crest}" alt="">`
      : side.kit ? kitBadge(side.kit, 'cap-mark cap-kit') : '';
    return `<div class="cap-card ${cls}">
      <div class="cap-text">
        <div class="cap-label">${label}</div>
        <div class="cap-name">${esc(name)}</div>
        <div class="cap-value">${value}</div>
        ${side ? `<div class="cap-team">${esc(side.name)}</div>` : ''}
      </div>${mark}
    </div>`;
  };
  return `<div class="cap-row">
    ${card('cap-orange', 'Orange Cap', orangeName, `${orangeRuns} runs`, orangeSide)}
    ${card('cap-purple', 'Purple Cap', purpleName, `${purpleWickets} wickets`, purpleSide)}
  </div>`;
}

/* --- the points table [A153] -------------------------------------------------------------
   Laid out like the IPL's own (iplt20.com): position, crest and full name, a Q beside each
   qualifier, P W L, points as the column that stands out, NRR, For and Against as
   runs/overs, and the last five results as circles. A divider under fourth, where the
   playoffs cut. On a phone it drops For/Against and swaps between the standard columns and
   the form column, the way the real site does. No NR column: nothing in this engine can
   produce a no result, so it would only ever read zero. A T column appears only in the rare
   season where a match stayed tied after the super overs (A135). */

function standingsSide(r){
  // Solo's own row carries the engine's 'YOU'; MY_SIDE is the page's name for it.
  if (r.you && MY_SIDE && !r.kit) return {...MY_SIDE, short: MY_SIDE.kit ? MY_SIDE.kit.monogram : r.short};
  return {name: r.name, short: r.short, crest: r.crest, kit: r.kit};
}

function standingsHtml(rows, {complete = true, champion = null} = {}){
  if (!rows || !rows.length) return '';
  const ties = rows.some(r => r.tied);
  const body = rows.map(r => {
    const side = standingsSide(r);
    const q = complete && r.pos <= 4 ? '<i class="st-q" title="Qualified for the playoffs">Q</i>' : '';
    const cup = champion && r.name === champion ? '<i class="st-cup" title="Champions">🏆</i>' : '';
    const form = (r.form || []).map(f =>
      `<i class="fm fm-${f.toLowerCase()}" title="${f === 'W' ? 'Won' : f === 'L' ? 'Lost' : 'Tied'}">${f}</i>`).join('');
    return `<tr class="${r.you ? 'you' : ''} ${r.pos === 4 ? 'cut' : ''} ${r.pos === 1 ? 'top' : ''}">
      <td class="st-pos">${r.pos}</td>
      <td class="st-team">${sideBadge(side, 'st-mark')}<span class="st-full">${esc(side.name)}</span><span
        class="st-short">${esc(side.short)}</span>${r.you ? '<span class="you-pill">you</span>' : ''}${cup}${q}</td>
      <td class="n st-std">${r.played}</td>
      <td class="n st-std st-sep">${r.won}</td><td class="n st-std st-sep">${r.lost}</td>
      ${ties ? `<td class="n st-std st-sep">${r.tied}</td>` : ''}
      <td class="n st-std st-wl">${r.won}-${r.lost}${ties ? '-' + r.tied : ''}</td>
      <td class="n st-pts">${r.points}</td>
      ${nrrCell(r.nrr).replace('<td class="n', '<td class="n st-std')}
      <td class="n st-wide st-fa">${r.runs_for}/${r.overs_for}</td>
      <td class="n st-wide st-fa">${r.runs_against}/${r.overs_against}</td>
      <td class="st-formcell"><span class="st-form">${form}</span></td>
    </tr>`;
  }).join('');
  return `<div class="st-wrap">
    <div class="st-toggle" role="tablist">
      <button class="sel" onclick="standingsView(this, false)">Standard</button>
      <button onclick="standingsView(this, true)">Form</button>
    </div>
    <table class="standings">
      <thead><tr><th class="st-pos">#</th><th>Team</th>
        <th class="n st-std">P</th><th class="n st-std st-sep">W</th><th class="n st-std st-sep">L</th>
        ${ties ? '<th class="n st-std st-sep">T</th>' : ''}
        <th class="n st-std st-wl">${ties ? 'W-L-T' : 'W-L'}</th>
        <th class="n st-pts">Pts</th><th class="n st-std">NRR</th>
        <th class="n st-wide">For</th><th class="n st-wide">Against</th>
        <th class="st-formcell">Form</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
}

// The phone-width Standard/Form switch -- a class on the wrapper; CSS does the rest.
function standingsView(btn, form){
  const wrap = btn.closest('.st-wrap');
  wrap.classList.toggle('form-view', form);
  wrap.querySelectorAll('.st-toggle button').forEach(b => b.classList.toggle('sel', b === btn));
}

/* --- the season's verdict [A153] ---------------------------------------------------------
   Where you finished, as a badge rather than a bare "6-8"; and when you won it all, a
   champion's moment with your own kit (or franchise crest) large. `you` is the side as the
   page draws it, `row` your table row, `champion` the winning side ({name, crest, kit}). */
function seasonHeroHtml({youChampion, you, row, teams, champion, finishLine}){
  const ord = n => n + (['th','st','nd','rd'][(n % 100 - 20) % 10] || ['th','st','nd','rd'][n] || 'th');
  const record = row ? `${row.won}W · ${row.lost}L${row.tied ? ' · ' + row.tied + 'T' : ''} · ${row.points} pts` : '';
  const mark = (side, cls) => !side ? '' : side.crest
    ? `<img class="${cls}" src="${side.crest}" alt="">` : side.kit ? kitBadge(side.kit, cls) : '';
  if (youChampion){
    return `<div class="hero champ" style="${you && you.kit ? kitStyle(you.kit) : ''}">
      <div class="hero-trophy" aria-hidden="true">🏆</div>
      ${mark(you, 'hero-mark')}
      <div class="hero-title">Champions</div>
      <div class="hero-name">${esc(you ? you.name : '')}</div>
      <div class="hero-line">${esc(finishLine || '')}</div>
      ${row ? `<div class="hero-record">${record}</div>` : ''}
    </div>`;
  }
  return `<div class="hero">
    ${row ? `<div class="hero-finish">
      <div class="hero-pos">${ord(row.pos)}<span>of ${teams}</span></div>
      <div class="hero-sub">${row.pos <= 4 ? 'Reached the playoffs' : 'Missed the playoffs'}</div>
      <div class="hero-record">${record}</div>
    </div>` : ''}
    ${champion ? `<div class="hero-winner">
      ${mark(champion, 'hero-winner-mark')}
      <div><div class="hero-sub">Champions</div><div class="hero-winner-name">${esc(champion.name)}</div></div>
    </div>` : ''}
  </div>`;
}

/* --- the journey card: a hand-drawn canvas, no server-side image generation --------- */


function themeColor(name){
  return getComputedStyle(document.documentElement).getPropertyValue('--' + name).trim();
}

function hideJourneyCard(e){
  if (e && e.target !== e.currentTarget) return;   // a click inside the frame stays open
  $('#cardOverlay').classList.add('hide');
}

// What a player actually did in THIS simulated tournament -- distinct from his real
// archive figures shown during the draft. Null balls means he never got the chance
// (e.g. an Impact Player rarely summoned), reported plainly rather than as a zero.
//
// Which figure leads is the player's ROLE, not just which happened to exist: a bowler
// who scratched together a few batting balls still leads with his bowling, and vice
// versa. An all-rounder has no fixed lead discipline, so it goes to whichever one he
// actually did more of THIS tournament (more balls involved in it) -- a bowling
// all-rounder having a rare big innings still reads as a bowler's card most of the time,
// which is what "predominantly" means for someone who is genuinely both.
function simFigures(c){
  const bat = (c.sim_bat_balls != null && c.sim_bat_balls > 0)
    ? {text: `${c.sim_bat_runs} runs · SR ${(c.sim_bat_runs / c.sim_bat_balls * 100).toFixed(1)}`,
       balls: c.sim_bat_balls}
    : null;
  const bowl = (c.sim_bowl_balls != null && c.sim_bowl_balls > 0)
    ? {text: `${c.sim_bowl_wickets} wkts · ER ${(c.sim_bowl_runs / (c.sim_bowl_balls / 6)).toFixed(2)}`,
       balls: c.sim_bowl_balls}
    : null;

  if (!bat && !bowl) return {primary: 'DID NOT PLAY', secondary: null};
  if (bat && !bowl) return {primary: bat.text, secondary: null};
  if (bowl && !bat) return {primary: bowl.text, secondary: null};

  const batLeads = c.kind === 'bowler' ? false
    : (c.kind === 'batter' || c.kind === 'keeper') ? true
    : bat.balls >= bowl.balls;   // allrounder/unrated: whichever he did more of this run
  return batLeads ? {primary: bat.text, secondary: bowl.text}
                  : {primary: bowl.text, secondary: bat.text};
}

// [A115] "38x4 12x6", or null when he hit neither. Null-safe on the counts themselves,
// because `_journey_entry` sends None for a man who never faced a ball -- and `0` there
// would claim he batted without clearing the rope, which is a different innings.
function boundaryLine(c){
  const f = c.sim_bat_fours || 0, s = c.sim_bat_sixes || 0;
  return (f || s) ? `${f}x4 ${s}x6` : null;
}

const KIND_CHIP = {
  batter: ['BAT', 'ink2'], bowler: ['BOWL', 'red'], allrounder: ['ALL', 'gold'],
  keeper: ['WK', 'gold2'], unrated: ['—', 'ink2'],
};

// A canvas can only draw an image that has finished loading, so the crests are fetched
// first. One that fails to load is simply left off its row -- never a broken card.
function loadImages(urls){
  return Promise.all(urls.map(u => !u ? null : new Promise(res => {
    const im = new Image();
    im.onload = () => res(im); im.onerror = () => res(null);
    im.src = u;
  })));
}

// --- the card is a poster, not a document --------------------------------------------------
// A team-coloured header with the side's badge and how the season ended, the record with a
// match-by-match form strip, four stat tiles, the two top performers as award cards, and
// the twelve in two columns -- in batting order down each column, Impact Player last. The
// side's own colours (read from the crest or kit CSS, so the card and the page agree)
// carry the header and rim; everything below stays on the site's own palette.

const CARD_DISPLAY = '"Anton","Archivo Black",Impact,sans-serif';
const CARD_BODY = '"Archivo",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif';
const CARD_FIGS = '"JetBrains Mono",ui-monospace,"SF Mono",Menlo,monospace';

// The side's three colours as the page itself would paint them: a throwaway element given
// the same crest class or kit style the scoreboard uses, read back through the cascade.
function sideColours(side){
  const t = sideTint(side);
  const el = document.createElement('div');
  if (t.cls) el.className = t.cls;
  if (t.style) el.setAttribute('style', t.style);
  document.body.appendChild(el);
  const css = getComputedStyle(el);
  const v = n => css.getPropertyValue(n).trim();
  const out = {team: v('--team') || themeColor('gold'), deep: v('--team-deep') || themeColor('bg-2'),
               ink: v('--team-ink') || themeColor('gold-2')};
  el.remove();
  return out;
}

// Your own matches in the order they were played, from whichever shape the page holds: a
// solo season keeps the league and the playoffs apart, a room keeps one flat list.
function journeyMatches(d){
  const list = d.your_results
    ? d.your_results.concat((d.playoffs || []).filter(r => r.yours))
    : (d.results || []).map(x => x.result).filter(r => r && r.yours);
  return list.map(r => {
    const mine = r.you_home === true ? r.home : r.you_home === false ? r.away : 'YOU';
    return {stage: r.stage, outcome: !r.winner ? 'T' : r.winner === mine ? 'W' : 'L'};
  });
}

function cardOrdinal(n){
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// One line saying where the run ended, read off the last match rather than assumed: a
// Qualifier 1 defeat is not an exit, and only the match you went out in says so.
function journeyEnding(d, matches){
  if (d.you_champion) return 'CHAMPIONS';
  const last = matches[matches.length - 1];
  if (last && last.stage !== 'league' && last.outcome === 'L'){
    if (/^final$/i.test(last.stage)) return 'RUNNERS-UP';
    return `OUT IN THE ${last.stage.replace(/^Semi-final \d+$/i, 'semi-final').toUpperCase()}`;
  }
  const me = (d.table || []).find(t => t.you);
  if (me) return `FINISHED ${cardOrdinal(me.pos).toUpperCase()} OF ${d.table.length}`;
  return 'THE SEASON ENDS HERE';
}

// Shrinks a font until the text fits, then ellipsises if it still does not.
function fitText(ctx, text, maxW, weight, size, family, min = 14){
  let s = size;
  ctx.font = `${weight} ${s}px ${family}`;
  while (ctx.measureText(text).width > maxW && s > min){
    s -= 1;
    ctx.font = `${weight} ${s}px ${family}`;
  }
  if (ctx.measureText(text).width <= maxW) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
  return t + '…';
}

function drawImageFit(ctx, im, x, y, box){
  const k = Math.min(box / im.width, box / im.height);
  const w = im.width * k, h = im.height * k;
  ctx.drawImage(im, x + (box - w) / 2, y + (box - h) / 2, w, h);
}

// `side` is the team the card is about -- {name, crest} for a franchise, {name, kit} for a
// drafted twelve [A151]. Its colour carries the header and rim, and its badge leads it.
async function drawJourneyCard(d, header, side){
  if (document.fonts && document.fonts.load){
    await Promise.all([`400 80px ${CARD_DISPLAY}`, `700 30px ${CARD_BODY}`, `700 24px ${CARD_FIGS}`]
      .map(f => document.fonts.load(f).catch(() => null)));
  }
  const [sideCrest] = await loadImages([side && side.crest]);
  const crestImgs = await loadImages(d.squad.map(c => c.crest));
  const canvas = $('#journeyCanvas');
  const ctx = canvas.getContext('2d');
  const gold = themeColor('gold'), gold2 = themeColor('gold-2'), ink = themeColor('ink'),
        ink2 = themeColor('ink-2'), ink3 = themeColor('ink-3'), mint = themeColor('mint'),
        red = themeColor('red'), red2 = themeColor('red-2'), line = themeColor('line'),
        lineStrong = themeColor('line-strong'), bg = themeColor('bg'), bg2 = themeColor('bg-2'),
        panel = themeColor('panel'), capOrange = themeColor('cap-orange'),
        capPurple = themeColor('cap-purple');
  const col = sideColours(side);
  const matches = journeyMatches(d);
  const champ = !!d.you_champion;

  // --- geometry, so the canvas is exactly as tall as what it holds -----------------------
  const W = 1080, M = 60, inner = W - 2 * M;
  const HEAD_H = 380, REC_H = 250, TILE_H = 160, PERF_H = 220;
  const rows = Math.ceil(d.squad.length / 2), cardH = 150, cardGap = 16;
  const squadTop = HEAD_H + REC_H + TILE_H + 40 + PERF_H + 60 + 70;
  const H = squadTop + rows * (cardH + cardGap) - cardGap + 120;
  canvas.width = W;
  canvas.height = H;

  // --- background and rim ------------------------------------------------------------------
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, bg2);
  grad.addColorStop(1, bg);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H);

  // --- the header: the side's colours, a diagonal sweep, the badge and the ending -----------
  ctx.save();
  const hg = ctx.createLinearGradient(0, 0, W, HEAD_H);
  hg.addColorStop(0, col.deep);
  hg.addColorStop(1, bg2);
  ctx.fillStyle = hg;
  ctx.fillRect(0, 0, W, HEAD_H);
  ctx.globalAlpha = .16;
  ctx.fillStyle = col.team;
  for (let i = -2; i < 9; i++){
    ctx.beginPath();
    ctx.moveTo(560 + i * 90, 0); ctx.lineTo(620 + i * 90, 0);
    ctx.lineTo(500 + i * 90, HEAD_H); ctx.lineTo(440 + i * 90, HEAD_H);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  const fade = ctx.createLinearGradient(0, HEAD_H - 120, 0, HEAD_H);
  fade.addColorStop(0, 'rgba(0,0,0,0)');
  fade.addColorStop(1, bg2);
  ctx.fillStyle = fade;
  ctx.fillRect(0, HEAD_H - 120, W, 120);
  ctx.restore();

  ctx.fillStyle = col.team;
  ctx.fillRect(0, 0, W, 10);

  // badge
  const bx = M, by = 90, bs = 170;
  if (sideCrest){
    drawImageFit(ctx, sideCrest, bx, by, bs);
  } else {
    const kc = side && side.kit ? kitColour(side.kit.colour) : null;
    const r = bs / 2, cxB = bx + r, cyB = by + r;
    ctx.beginPath(); ctx.arc(cxB, cyB, r - 6, 0, Math.PI * 2);
    ctx.fillStyle = kc ? kc.deep : panel; ctx.fill();
    ctx.lineWidth = 8; ctx.strokeStyle = kc ? kc.team : gold; ctx.stroke();
    ctx.fillStyle = kc ? kc.ink : gold2;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const mono = (side && side.kit && side.kit.monogram) || (side && side.short) || '★';
    ctx.font = `400 ${mono.length > 2 ? 52 : 64}px ${CARD_DISPLAY}`;
    ctx.fillText(mono, cxB, cyB + 3);
    ctx.textBaseline = 'alphabetic';
  }

  const tx = bx + bs + 40, tw = W - M - tx;
  ctx.textAlign = 'left';
  ctx.fillStyle = col.ink;
  ctx.font = `700 22px ${CARD_FIGS}`;
  ctx.fillText(`${(header || stateLabel(d.state)).toUpperCase()} · SEASON JOURNEY`, tx, 118);
  ctx.fillStyle = ink;
  const name = ((side && side.name) || 'Your twelve').toUpperCase();
  ctx.fillText(fitText(ctx, name, tw, 400, 84, CARD_DISPLAY, 40), tx, 210);
  const ending = journeyEnding(d, matches);
  ctx.fillStyle = champ ? gold2 : ink2;
  ctx.font = `400 44px ${CARD_DISPLAY}`;
  if (champ){ ctx.save(); ctx.shadowColor = gold; ctx.shadowBlur = 24; }
  ctx.fillText((champ ? '🏆  ' : '') + ending, tx, 272);
  if (champ) ctx.restore();

  // --- the record, and every match as a dot ------------------------------------------------
  let y = HEAD_H;
  ctx.textAlign = 'left';
  ctx.fillStyle = ink3;
  ctx.font = `700 20px ${CARD_FIGS}`;
  ctx.fillText('RECORD', M, y + 20);
  ctx.fillStyle = ink;
  ctx.font = `400 150px ${CARD_DISPLAY}`;
  const rec = `${d.won}–${d.lost}${d.tied ? '–' + d.tied : ''}`;
  ctx.fillText(rec, M - 4, y + 170);
  const recW = ctx.measureText(rec).width;
  ctx.fillStyle = ink3;
  ctx.font = `600 19px ${CARD_FIGS}`;
  ctx.fillText(`WON ${d.won} · LOST ${d.lost}${d.tied ? ' · TIED ' + d.tied : ''}`, M, y + 210);

  // Form: league matches as filled dots, playoff matches ringed in gold so the knockout
  // run reads apart from the group stage.
  const fx = M + recW + 60, fw = W - M - fx;
  if (matches.length){
    const per = Math.min(matches.length, 8), gapD = 12;
    const d0 = Math.min(46, (fw - gapD * (per - 1)) / per);
    ctx.fillStyle = ink3;
    ctx.font = `700 20px ${CARD_FIGS}`;
    ctx.fillText('FORM', fx, y + 20);
    matches.forEach((m, i) => {
      const r = Math.floor(i / per), c = i % per;
      const x = fx + c * (d0 + gapD) + d0 / 2, yy = y + 70 + r * (d0 + gapD);
      const fill = m.outcome === 'W' ? mint : m.outcome === 'L' ? red : ink3;
      ctx.beginPath(); ctx.arc(x, yy, d0 / 2, 0, Math.PI * 2);
      ctx.fillStyle = fill; ctx.globalAlpha = m.outcome === 'L' ? .85 : 1; ctx.fill();
      ctx.globalAlpha = 1;
      if (m.stage !== 'league'){
        ctx.lineWidth = 4; ctx.strokeStyle = gold2; ctx.stroke();
      }
      ctx.fillStyle = '#0b1324';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = `800 ${Math.round(d0 * .42)}px ${CARD_FIGS}`;
      ctx.fillText(m.outcome, x, yy + 1);
      ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    });
  }

  // --- four stat tiles ---------------------------------------------------------------------
  y = HEAD_H + REC_H;
  const tiles = [[d.runs, 'RUNS'], [d.wickets, 'WICKETS'], [d.played, 'MATCHES'], [d.overall_rating, 'TEAM RATING']];
  const tg = 18, tw4 = (inner - tg * 3) / 4;
  tiles.forEach(([val, label], i) => {
    const x = M + i * (tw4 + tg);
    ctx.fillStyle = 'rgba(255,255,255,.035)';
    ctx.beginPath(); ctx.roundRect(x, y, tw4, TILE_H, 14); ctx.fill();
    ctx.strokeStyle = line; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = col.team;
    ctx.fillRect(x + 22, y + 22, 34, 4);
    ctx.textAlign = 'left';
    ctx.fillStyle = gold2;
    ctx.font = `400 66px ${CARD_DISPLAY}`;
    ctx.fillText(val == null ? '–' : Number(val).toLocaleString(), x + 22, y + 106);
    ctx.fillStyle = ink3;
    ctx.font = `700 17px ${CARD_FIGS}`;
    ctx.fillText(label, x + 22, y + 138);
  });

  // --- top performers, as two award cards --------------------------------------------------
  y = HEAD_H + REC_H + TILE_H + 40;
  const pw = (inner - 20) / 2;
  const crestOf = nm => {
    const i = d.squad.findIndex(c => c.name === nm);
    return i >= 0 ? crestImgs[i] : null;
  };
  const award = (x, label, acc, nm, val, unit) => {
    ctx.save();
    const ag = ctx.createLinearGradient(x, y, x + pw, y + PERF_H);
    ag.addColorStop(0, acc + '33');
    ag.addColorStop(.7, 'rgba(255,255,255,.02)');
    ctx.fillStyle = ag;
    ctx.beginPath(); ctx.roundRect(x, y, pw, PERF_H, 16); ctx.fill();
    ctx.strokeStyle = line; ctx.lineWidth = 1; ctx.stroke();
    ctx.clip();
    ctx.fillStyle = acc;
    ctx.fillRect(x, y, 6, PERF_H);
    const im = nm && crestOf(nm);
    if (im){ ctx.globalAlpha = .14; drawImageFit(ctx, im, x + pw - 170, y + 40, 200); ctx.globalAlpha = 1; }
    ctx.restore();
    ctx.textAlign = 'left';
    ctx.fillStyle = acc;
    ctx.font = `700 19px ${CARD_FIGS}`;
    ctx.fillText(label, x + 32, y + 46);
    ctx.fillStyle = ink;
    ctx.font = `400 92px ${CARD_DISPLAY}`;
    const v = val == null ? '–' : String(val);
    ctx.fillText(v, x + 30, y + 146);
    const vw = ctx.measureText(v).width;
    ctx.fillStyle = ink3;
    ctx.font = `700 18px ${CARD_FIGS}`;
    ctx.fillText(unit, x + 40 + vw, y + 146);
    ctx.fillStyle = ink;
    ctx.fillText(fitText(ctx, nm || '—', pw - 64, 700, 30, CARD_BODY, 18), x + 32, y + 192);
  };
  award(M, 'TOP SCORER', capOrange, d.top_scorer, d.top_scorer_runs, 'RUNS');
  award(M + pw + 20, 'TOP WICKET-TAKER', capPurple, d.top_wicket_taker, d.top_wicket_taker_wickets, 'WICKETS');

  // --- the twelve ----------------------------------------------------------------------------
  y = squadTop - 40;
  ctx.textAlign = 'left';
  ctx.fillStyle = ink;
  ctx.font = `400 40px ${CARD_DISPLAY}`;
  ctx.fillText('THE TWELVE', M, y);
  ctx.fillStyle = ink3;
  ctx.font = `700 17px ${CARD_FIGS}`;
  ctx.textAlign = 'right';
  ctx.fillText('IN BATTING ORDER · FIGURES FROM THIS SEASON', W - M, y);
  ctx.textAlign = 'left';

  const cw = (inner - 20) / 2;
  d.squad.forEach((c, i) => {
    // Down the first column, then the second, so the order reads as a batting order.
    const colI = Math.floor(i / rows), rowI = i % rows;
    const x = M + colI * (cw + 20), yy = squadTop + rowI * (cardH + cardGap);
    const imp = i === 11;
    ctx.fillStyle = imp ? 'rgba(245,184,61,.07)' : 'rgba(255,255,255,.03)';
    ctx.beginPath(); ctx.roundRect(x, yy, cw, cardH, 14); ctx.fill();
    ctx.strokeStyle = imp ? 'rgba(245,184,61,.45)' : line; ctx.lineWidth = 1; ctx.stroke();

    // the number, in the side's colour
    ctx.fillStyle = imp ? gold : col.team;
    ctx.beginPath(); ctx.roundRect(x, yy, 58, cardH, [14, 0, 0, 14]); ctx.fill();
    ctx.fillStyle = imp ? '#141414' : (col.deep || bg);
    ctx.textAlign = 'center';
    ctx.font = `400 ${imp ? 24 : 40}px ${CARD_DISPLAY}`;
    ctx.fillText(imp ? 'IMP' : String(i + 1), x + 29, yy + cardH / 2 + (imp ? 9 : 15));

    const lx = x + 78, rx = x + cw - 20;
    const im = crestImgs[i];
    if (im) drawImageFit(ctx, im, lx, yy + 18, 40);
    const nx = lx + (im ? 52 : 0);

    const [chipLabel, chipColorKey] = KIND_CHIP[c.kind] || KIND_CHIP.unrated;
    const chipColor = {ink2, red: red2, gold, gold2}[chipColorKey] || ink2;
    ctx.font = `700 13px ${CARD_FIGS}`;
    const chipW = ctx.measureText(chipLabel).width + 22;
    ctx.strokeStyle = chipColor; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.roundRect(rx - chipW, yy + 22, chipW, 26, 13); ctx.stroke();
    ctx.fillStyle = chipColor; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(chipLabel, rx - chipW / 2, yy + 36);
    ctx.textBaseline = 'alphabetic';

    ctx.textAlign = 'left';
    ctx.fillStyle = ink;
    ctx.fillText(fitText(ctx, c.name, rx - chipW - 12 - nx, 700, 27, CARD_BODY, 16), nx, yy + 48);
    ctx.fillStyle = ink3;
    ctx.font = `500 15px ${CARD_FIGS}`;
    ctx.fillText(fitText(ctx, [c.franchise, c.season_year].filter(Boolean).join(' · ') || '—',
                         rx - lx, 500, 15, CARD_FIGS, 11), lx, yy + 82);

    const fig = simFigures(c);
    const dnp = fig.primary === 'DID NOT PLAY';
    ctx.fillStyle = dnp ? ink3 : mint;
    ctx.fillText(fitText(ctx, fig.primary, rx - lx, 700, 23, CARD_FIGS, 14), lx, yy + 116);
    // [A115] The second discipline and the boundaries share the last line; omitted when
    // there is neither, rather than printed as a row of noughts.
    const second = [fig.secondary, boundaryLine(c)].filter(Boolean).join('  ·  ');
    if (second){
      ctx.fillStyle = ink3;
      ctx.fillText(fitText(ctx, second, rx - lx, 500, 15, CARD_FIGS, 11), lx, yy + 138);
    }
  });

  // --- footer and rim ------------------------------------------------------------------------
  const fy = H - 46;
  ctx.strokeStyle = lineStrong; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(M, fy - 40); ctx.lineTo(W - M, fy - 40); ctx.stroke();
  ctx.textAlign = 'left';
  ctx.fillStyle = ink;
  ctx.font = `400 30px ${CARD_DISPLAY}`;
  ctx.fillText('THE LEGENDS', M, fy);
  const lw = ctx.measureText('THE LEGENDS ').width;
  ctx.fillStyle = gold;
  ctx.fillText('ALMANACK', M + lw, fy);

  ctx.strokeStyle = col.team;
  ctx.lineWidth = 6;
  ctx.strokeRect(3, 3, W - 6, H - 6);

  $('#cardDownload').href = canvas.toDataURL('image/png');
}

/* --- the champion's celebration ----------------------------------------------------------
   Full screen, fireworks in the champion's own colours, the trophy and the side's badge,
   shown once when a tournament's result is announced -- to everybody, not only the
   winner, since the final result is the moment the whole room was waiting for.

   `key` names the tournament (a solo state, a room's run) and is remembered in this
   browser, so a reload, a re-render or a room's next poll does not replay it. A per-viewer
   convenience, which is what localStorage is for here; if storage is unavailable the
   worst case is seeing it again. Tap, Escape or the button dismisses it; it also leaves
   by itself. With reduced motion asked for there are no fireworks, just the card. */

const CHAMP_SEEN_KEY = 'iplegends_champion_seen';
let CHAMP_FX = null;

function championSeen(key){
  try { return (JSON.parse(localStorage.getItem(CHAMP_SEEN_KEY)) || []).includes(key); }
  catch(e){ return false; }
}
function markChampionSeen(key){
  try {
    const seen = (JSON.parse(localStorage.getItem(CHAMP_SEEN_KEY)) || []).filter(k => k !== key);
    seen.push(key);
    localStorage.setItem(CHAMP_SEEN_KEY, JSON.stringify(seen.slice(-40)));
  } catch(e){ /* private mode: it may show again, which is harmless */ }
}

function celebrateChampion({key, side, mine}){
  if (!side || !side.name || CHAMP_FX) return;
  if (key && championSeen(key)) return;
  if (key) markChampionSeen(key);

  const el = document.createElement('div');
  el.className = 'champ-fx';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', `${side.name} are the champions`);
  const tint = sideTint(side);
  if (tint.cls) el.classList.add(tint.cls);
  if (tint.style) el.setAttribute('style', tint.style);
  el.innerHTML = `<canvas class="champ-fx-sky"></canvas>
    <div class="champ-fx-rays"></div>
    <div class="champ-fx-card">
      <div class="champ-fx-trophy">🏆</div>
      <div class="champ-fx-kicker">${mine ? 'You are the' : 'The'} champions</div>
      <div class="champ-fx-badge">${sideBadge(side, 'champ-fx-mark')}</div>
      <div class="champ-fx-name">${esc(side.name)}</div>
      <div class="champ-fx-sub">${mine ? 'Your side lifts the trophy.' : 'Winners of the final.'}</div>
      <button class="act lead champ-fx-close" type="button">Continue</button>
    </div>`;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add('on'));

  const css = getComputedStyle(el);
  const team = (css.getPropertyValue('--team') || '').trim() || '#f5b83d';
  const colours = [team, team, '#ffd873', '#ffffff', '#f5b83d'];
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  const fx = {el, raf: null, timers: [], onKey: null};
  CHAMP_FX = fx;
  const close = () => closeChampion();
  el.querySelector('.champ-fx-close').addEventListener('click', e => { e.stopPropagation(); close(); });
  el.addEventListener('click', close);
  fx.onKey = e => { if (e.key === 'Escape' || e.key === 'Enter') close(); };
  document.addEventListener('keydown', fx.onKey);
  fx.timers.push(setTimeout(close, 9000));
  el.querySelector('.champ-fx-close').focus({preventScroll: true});
  if (!reduce) runFireworks(fx, el.querySelector('canvas'), colours);
}

function closeChampion(){
  const fx = CHAMP_FX;
  if (!fx) return;
  CHAMP_FX = null;
  fx.timers.forEach(clearTimeout);
  if (fx.raf) cancelAnimationFrame(fx.raf);
  document.removeEventListener('keydown', fx.onKey);
  fx.el.classList.remove('on');
  fx.el.classList.add('off');
  setTimeout(() => fx.el.remove(), 450);
}

// Rockets rise from the bottom and burst into sparks that fall and fade. Bursts come in
// a steady rhythm for the first six seconds, with a finale of several at once, then the
// sky is left to empty. Drawn at device resolution so it stays sharp on a phone.
function runFireworks(fx, canvas, colours){
  const ctx = canvas.getContext('2d');
  let W = 0, H = 0;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const size = () => {
    W = innerWidth; H = innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  size();
  addEventListener('resize', size);
  const rockets = [], sparks = [];
  const pick = () => colours[Math.floor(Math.random() * colours.length)];
  const launch = () => {
    const x = W * (0.15 + Math.random() * 0.7);
    rockets.push({x, y: H + 10, vx: (Math.random() - 0.5) * 1.6,
                  vy: -(H * 0.012 + 7 + Math.random() * 3),
                  top: H * (0.12 + Math.random() * 0.35), colour: pick()});
  };
  const burst = (x, y, colour) => {
    const n = 70 + Math.floor(Math.random() * 40);
    const ring = Math.random() < 0.3;
    for (let i = 0; i < n; i++){
      const a = (Math.PI * 2 * i) / n + Math.random() * 0.2;
      const v = ring ? 5.2 : 1.5 + Math.random() * 5;
      sparks.push({x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1,
                   decay: 0.009 + Math.random() * 0.012,
                   colour: Math.random() < 0.8 ? colour : pick()});
    }
  };
  for (let t = 0; t < 6000; t += 420) fx.timers.push(setTimeout(launch, t + Math.random() * 200));
  [6200, 6300, 6450, 6600, 6700].forEach(t => fx.timers.push(setTimeout(launch, t)));
  const started = performance.now();
  const frame = now => {
    if (CHAMP_FX !== fx){ removeEventListener('resize', size); return; }
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = 'rgba(0,0,0,0.22)';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'lighter';
    for (let i = rockets.length - 1; i >= 0; i--){
      const r = rockets[i];
      r.x += r.vx; r.y += r.vy; r.vy += 0.12;
      ctx.fillStyle = r.colour;
      ctx.beginPath(); ctx.arc(r.x, r.y, 2.4, 0, Math.PI * 2); ctx.fill();
      if (r.y <= r.top || r.vy >= 0){ burst(r.x, r.y, r.colour); rockets.splice(i, 1); }
    }
    for (let i = sparks.length - 1; i >= 0; i--){
      const s = sparks[i];
      s.x += s.vx; s.y += s.vy; s.vx *= 0.985; s.vy = s.vy * 0.985 + 0.05;
      s.life -= s.decay;
      if (s.life <= 0){ sparks.splice(i, 1); continue; }
      ctx.globalAlpha = Math.max(0, s.life);
      ctx.fillStyle = s.colour;
      ctx.beginPath(); ctx.arc(s.x, s.y, 2 * s.life + 0.6, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (now - started < 12000 || rockets.length || sparks.length) fx.raf = requestAnimationFrame(frame);
  };
  fx.raf = requestAnimationFrame(frame);
}
