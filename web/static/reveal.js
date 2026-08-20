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
function startOverStepper(innings, stageText, onDone, priorContext){
  hideAllRevealScreens();
  OVER_STEP = {
    log: innings.over_log, i: 0, timer: null,
    speed: Number($('#overSpeedSelect').value) || 500,
    paused: false, onDone, stageText, innings, priorContext: priorContext || null,
    lastRuns: null,
  };
  renderOverPrior();
  $('#overStepper').classList.remove('hide');
  renderOverStep();
  if (OVER_STEP) scheduleNextOver();
}

// Painted once, at the start of the reveal, and left alone -- unlike renderOverStep this
// never changes tick to tick, since the prior innings is already fully resolved.
function renderOverPrior(){
  const p = OVER_STEP.priorContext;
  $('#overPrior').classList.toggle('hide', !p);
  if (!p) return;
  $('#overPriorScore').textContent =
    `${p.battingLabel} posted ${p.innings.runs}/${p.innings.wickets} (${p.innings.overs} ov)`;
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

// One small pill per over revealed so far -- a cheap "worm chart" substitute with no
// charting library, rebuilt from scratch each tick (cheap at <=20 overs). Only the most
// recently added chip gets the pop-in animation.
function renderOverChips(entries){
  $('#overChips').innerHTML = entries.map((o, idx) => {
    const cls = o.over_runs >= 12 ? 'great' : (o.over_runs >= 7 ? 'good' : '');
    const wkt = o.over_wickets ? ' wicket' : '';
    const isNew = idx === entries.length - 1;
    return `<span class="chip ${cls}${wkt}${isNew ? ' chip-new' : ''}">${o.over_runs}</span>`;
  }).join('');
}

function renderOverStep(){
  const { log, i, stageText, innings, priorContext } = OVER_STEP;
  $('#overStage').textContent = stageText;
  if (i >= log.length){
    // either a genuinely over-free innings, or the log just ran out -- either way the
    // final total is already known, so show it and hand off immediately.
    $('#overScoreLine').textContent = `${innings.runs}/${innings.wickets}`;
    $('#overLastLine').textContent = `${innings.overs} overs, ${innings.extras} extras`;
    $('#overOversBar').style.width = (100 * Math.min(1, innings.balls / 120)) + '%';
    renderChaseLine(priorContext, innings.runs, innings.balls, true);
    finishOverStepper();
    return;
  }
  const o = log[i];
  const scoreChanged = OVER_STEP.lastRuns !== null && o.runs !== OVER_STEP.lastRuns;
  OVER_STEP.lastRuns = o.runs;
  $('#overScoreLine').textContent = `${o.runs}/${o.wickets} after ${o.over + 1} overs`;
  $('#overLastLine').textContent =
    `Over ${o.over + 1}: ${o.bowler} -- ${o.over_runs} run${o.over_runs === 1 ? '' : 's'}` +
    (o.over_wickets ? `, ${o.over_wickets} wicket${o.over_wickets === 1 ? '' : 's'}` : '');
  $('#overOversBar').style.width = (100 * Math.min(1, o.balls / 120)) + '%';
  renderChaseLine(priorContext, o.runs, o.balls, false);
  pulseScoreLine(o.over_wickets > 0, scoreChanged);
  renderOverChips(log.slice(0, i + 1));
}

function scheduleNextOver(){
  clearOverTimer();
  if (!OVER_STEP || OVER_STEP.paused) return;
  OVER_STEP.timer = setTimeout(() => {
    if (!OVER_STEP) return;
    OVER_STEP.i++;
    const more = OVER_STEP.i < OVER_STEP.log.length;
    renderOverStep();
    if (more) scheduleNextOver();
  }, OVER_STEP.speed);
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
// under the same key both pages read, so solo and a room agree on it.
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
  renderOverStep();
}

function finishOverStepper(){
  clearOverTimer();
  const onDone = OVER_STEP && OVER_STEP.onDone;
  OVER_STEP = null;
  hideAllRevealScreens();
  if (onDone) onDone();
}

/* --- the scorecard overlay -------------------------------------------------------- */

function renderScorecard(r){
  if (!r || !r.home_innings || !r.away_innings) return;
  $('#scStage').textContent = r.stage;
  $('#scHeadline').textContent = r.winner ? `${r.winner} win` : 'Match tied';
  $('#scHeadline').classList.toggle('won', !!r.winner);
  $('#scMargin').textContent = r.margin;
  $('#scInnings').innerHTML =
    scorecardInnings(r.home, r.home_score, r.home_innings) +
    scorecardInnings(r.away, r.away_score, r.away_innings);
  $('#scorecardOverlay').classList.remove('hide');
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

function scorecardInnings(short, score, inn){
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
      <caption>${short} · ${score} (${inn.overs} ov, ${inn.extras} extras) · ${
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

// A generated colour-coded initial stands in for a team crest (there is no real crest
// art for a franchise-season, and hashing keeps it stable without a lookup table). The
// viewer's own row gets a fixed gold star instead, never a hashed colour, so it reads as
// a status rather than just another team.
function teamBadge(short, isYou){
  if (isYou) return `<span class="team-badge you-badge">★</span>`;
  let h = 0;
  for (let i = 0; i < short.length; i++) h = (h * 33 + short.charCodeAt(i)) >>> 0;
  return `<span class="team-badge" style="background:hsl(${h % 360} 60% 52%)">${short.slice(0, 2).toUpperCase()}</span>`;
}

// The tournament's own Orange Cap/Purple Cap, never populated until the season/room is
// actually complete (both callers only have real names to pass once it is), so an empty
// pair renders nothing rather than a blank card.
function capRowHtml(orangeName, orangeRuns, purpleName, purpleWickets){
  if (!orangeName && !purpleName) return '';
  return `<div class="cap-row">
    <div class="cap-card cap-orange">
      <div class="cap-label">🟠 Orange Cap</div>
      <div class="cap-name">${orangeName}</div>
      <div class="cap-value">${orangeRuns} runs</div>
    </div>
    <div class="cap-card cap-purple">
      <div class="cap-label">🟣 Purple Cap</div>
      <div class="cap-name">${purpleName}</div>
      <div class="cap-value">${purpleWickets} wickets</div>
    </div>
  </div>`;
}

/* --- the journey card: a hand-drawn canvas, no server-side image generation --------- */

const CARD_MONO = 'ui-monospace,"SF Mono",Menlo,Consolas,monospace';
const CARD_SANS = '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif';

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

function drawJourneyCard(d, header){
  const canvas = $('#journeyCanvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height, cx = W / 2;
  const gold = themeColor('gold'), gold2 = themeColor('gold-2'), ink = themeColor('ink'),
        ink2 = themeColor('ink-2'), ink3 = themeColor('ink-3'), green = themeColor('green'),
        red = themeColor('red'), red2 = themeColor('red-2'), lineStrong = themeColor('line-strong'),
        line = themeColor('line'), bg = themeColor('bg'), bg2 = themeColor('bg-2');

  ctx.clearRect(0, 0, W, H);
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, bg2);
  grad.addColorStop(1, bg);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = gold;
  ctx.lineWidth = 5;
  ctx.strokeRect(30, 30, W - 60, H - 60);
  ctx.strokeStyle = lineStrong;
  ctx.lineWidth = 1;
  ctx.strokeRect(40, 40, W - 80, H - 80);

  ctx.textAlign = 'center';
  ctx.fillStyle = ink3;
  ctx.font = `600 24px ${CARD_MONO}`;
  ctx.fillText(header || 'SEED ' + d.state.split('-')[0], cx, 110);

  // a soft glow behind the headline/score -- champion gets the warm one, an eliminated
  // side a duller red, so the two outcomes read apart even before the words register
  ctx.save();
  ctx.shadowColor = d.you_champion ? gold : red2;
  ctx.shadowBlur = d.you_champion ? 26 : 12;
  ctx.fillStyle = d.you_champion ? gold : red2;
  ctx.font = `800 56px ${CARD_SANS}`;
  ctx.fillText(d.you_champion ? 'CHAMPION' : 'THE SEASON ENDS HERE', cx, 190);
  ctx.fillStyle = ink;
  ctx.font = `800 140px ${CARD_MONO}`;
  ctx.fillText(`${d.won}-${d.lost}${d.tied ? '-' + d.tied : ''}`, cx, 350);
  ctx.restore();

  // four stat tiles
  const tiles = [[d.runs, 'RUNS'], [d.wickets, 'WICKETS'], [d.played, 'MATCHES'], [d.overall_rating, 'RATING']];
  const gap = 24, tileW = (W - 120 - gap * (tiles.length - 1)) / tiles.length;
  const tileY = 420, tileH = 150;
  tiles.forEach(([val, label], i) => {
    const x = 60 + i * (tileW + gap);
    ctx.strokeStyle = lineStrong;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect(x, tileY, tileW, tileH, 12); ctx.stroke();
    ctx.fillStyle = green;
    ctx.font = `700 52px ${CARD_MONO}`;
    ctx.fillText(val == null ? '--' : String(val), x + tileW / 2, tileY + 78);
    ctx.fillStyle = ink3;
    ctx.font = `600 18px ${CARD_MONO}`;
    ctx.fillText(label, x + tileW / 2, tileY + 118);
  });

  // top performers, dashed box like a "golden boot" callout
  const boxY = 610, boxH = 200;
  ctx.setLineDash([10, 8]);
  ctx.strokeStyle = gold;
  ctx.beginPath(); ctx.roundRect(60, boxY, W - 120, boxH, 14); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = gold;
  ctx.font = `600 22px ${CARD_MONO}`;
  ctx.fillText('TOP PERFORMERS', cx, boxY + 46);
  ctx.fillStyle = ink;
  ctx.font = `700 36px ${CARD_SANS}`;
  ctx.fillText(`${d.top_scorer} · ${d.top_scorer_runs} runs`, cx, boxY + 100);
  ctx.fillText(`${d.top_wicket_taker} · ${d.top_wicket_taker_wickets} wkts`, cx, boxY + 150);

  // the twelve, in the order the drafter arranged them -- each row its own bordered
  // card: who he actually was that season (franchise, season) and what he did in THIS
  // simulated tournament, not the real archive figures the draft screen already showed
  ctx.textAlign = 'left';
  ctx.fillStyle = ink3;
  ctx.font = `600 20px ${CARD_MONO}`;
  ctx.fillText('YOUR TWELVE', 60, boxY + boxH + 60);

  const listTop = boxY + boxH + 110, rowH = 108, rowGap = 14, rowX = 60, rowW = W - 120;
  d.squad.forEach((c, i) => {
    const y = listTop + i * (rowH + rowGap);
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect(rowX, y, rowW, rowH, 12); ctx.stroke();

    const padX = 24, midY1 = y + 40, midY2 = y + 78;

    ctx.textAlign = 'left';
    ctx.fillStyle = ink3;
    ctx.font = `600 22px ${CARD_MONO}`;
    ctx.fillText(i === 11 ? 'IMP' : String(i + 1), rowX + padX, midY1);

    const [chipLabel, chipColorKey] = KIND_CHIP[c.kind] || KIND_CHIP.unrated;
    const chipColor = {ink2, red, gold, gold2}[chipColorKey] || ink2;
    const chipX = rowX + padX + 46, chipW = 64, chipH = 26, chipTop = midY1 - 19;
    ctx.strokeStyle = chipColor;
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.roundRect(chipX, chipTop, chipW, chipH, 13); ctx.stroke();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';   // centres the label in the pill; fillText below is baseline-relative again
    ctx.fillStyle = chipColor;
    ctx.font = `700 13px ${CARD_MONO}`;
    ctx.fillText(chipLabel, chipX + chipW / 2, chipTop + chipH / 2);
    ctx.textBaseline = 'alphabetic';

    const nameX = chipX + chipW + 20;
    ctx.textAlign = 'left';
    ctx.fillStyle = ink;
    ctx.font = `700 30px ${CARD_SANS}`;
    ctx.fillText(c.name, nameX, midY1);

    ctx.font = `500 18px ${CARD_MONO}`;
    ctx.fillStyle = ink3;
    ctx.fillText([c.franchise, c.season_year].filter(Boolean).join(' · ') || '—', nameX, midY2);

    const fig = simFigures(c);
    ctx.textAlign = 'right';
    ctx.fillStyle = fig.primary === 'DID NOT PLAY' ? ink3 : green;
    ctx.font = `700 24px ${CARD_MONO}`;
    ctx.fillText(fig.primary, rowX + rowW - padX, midY1);
    // [A115] Boundaries share the row's SECOND line rather than taking a line or a tile
    // of their own. A pure batter leaves that line empty, so his read for free; an
    // all-rounder already uses it for his other discipline, so the two join with a
    // separator. At 17px mono the longest combination is about a third of the row, so
    // this cannot collide with the name on the left the way a 24px primary line could.
    //
    // Omitted entirely when he hit none, rather than printed as `0x4 0x6` -- the same
    // reason the scorecard cell is a dash. He batted and did not clear the rope; that is
    // legible as an absence and unreadable as a row of noughts.
    const second = [fig.secondary, boundaryLine(c)].filter(Boolean).join('   ·   ');
    if (second){
      ctx.fillStyle = ink3;
      ctx.font = `500 17px ${CARD_MONO}`;
      ctx.fillText(second, rowX + rowW - padX, midY2);
    }
  });

  ctx.textAlign = 'center';
  ctx.fillStyle = ink3;
  ctx.font = `600 20px ${CARD_MONO}`;
  ctx.fillText('THE LEGENDS ALMANACK', cx, listTop + d.squad.length * (rowH + rowGap) - rowGap + 60);

  $('#cardDownload').href = canvas.toDataURL('image/png');
}
