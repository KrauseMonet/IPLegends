// Season Analysis -- the graphics a broadcast puts up after a tournament, drawn as inline
// SVG with no charting library (the same "no dependency we don't need" stance the rest of
// this project takes; `fieldWheel` in common.js already established the pattern).
//
// Shared verbatim by the solo season page and a league room, which is the whole reason it
// lives here rather than in either one: both call `renderAnalysis(data, host)` with the
// identical `AnalysisOut` payload, so the two screens cannot drift.
//
// The page reads as a magazine spread rather than a stack of charts: a headline band of the
// season's numbers, the honours, then four numbered chapters (the innings, the bat, the
// ball, boundaries), each led by one sentence saying what its charts show. Those sentences
// are computed from the payload, never written in advance, so each one is true of the
// season on screen -- and each picks its words (more / less, chasing / setting) from the
// numbers rather than assuming the usual T20 shape.
//
// The two toggles ARE the "search" the feature was asked for. Rather than a query box that
// can be typed into wrongly, the dimensions the engine can actually answer -- runs against
// wickets, the whole league against your own side -- are the axes themselves, so every
// combination a viewer can reach is one the data supports. The scope toggle lives ONCE, in
// the sticky chapter bar, and every panel it governs carries a tag saying which view it is
// showing [A117: a control that governs a number must be findable from the number].

let ANALYSIS = null;
let AN_METRIC = 'runs';      // 'runs' | 'wickets'
let AN_SCOPE = 'league';     // 'league' | 'yours'

const PHASE_TINT = {powerplay: 'var(--gold)', middle: 'var(--ink-2)', death: 'var(--hot)'};

// Leaderboards show this many rows and fold the rest behind "show more": ten rows across
// five boards was the wall of mono text that made the old screen a scroll rather than a read.
const AN_LEAD_ROWS = 5;

const AN_CHAPTERS = [
  ['an-honours', 'Honours'],
  ['an-innings', 'The innings'],
  ['an-bat', 'With the bat'],
  ['an-ball', 'With the ball'],
  ['an-bdy', 'Boundaries'],
];

function renderAnalysis(d, host){
  ANALYSIS = d;
  if (!anYoursHasData()) AN_SCOPE = 'league';   // a spectator seat has no side of its own
  host.innerHTML = `<div id="anHero"></div><nav id="anNav" class="an-nav"></nav><div id="anBody"></div>`;
  anPaint();
}

function anYoursHasData(){
  return !!ANALYSIS && ANALYSIS.your_phases.some(p => p.overs > 0);
}

function anSetMetric(m){ AN_METRIC = m; anPaint(); }
function anSetScope(s){ AN_SCOPE = s; anPaint(); }

function anJump(id){
  const el = document.getElementById(id);
  if (!el) return;
  // Clear the sticky site nav plus the chapter bar beneath it.
  const bar = document.querySelector('.an-nav');
  const off = (bar ? bar.getBoundingClientRect().bottom : 128) + 12;
  const y = el.getBoundingClientRect().top + window.scrollY - off;
  window.scrollTo({top: y, behavior: 'smooth'});
}

function anToggleMore(btn){
  const panel = btn.closest('.an-lead');
  const open = panel.classList.toggle('open');
  btn.textContent = open ? 'Show fewer' : btn.dataset.more;
}

// --- small helpers for the sentences ---------------------------------------------------------

const anSum = (rows, k) => rows.reduce((n, r) => n + (r[k] || 0), 0);
const anPct = (a, b) => b ? Math.round(100 * a / b) : 0;
const anFix = (v, n = 1) => Number(v).toFixed(n);

function anOrdinal(n){
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function anPhase(phases, key){ return phases.find(p => p.phase === key) || null; }

// --- the page -------------------------------------------------------------------------------

function anPaint(){
  const d = ANALYSIS;
  const yours = AN_SCOPE === 'yours';
  const phases = yours ? d.your_phases : d.phases;
  const bars = yours ? d.your_manhattan : d.manhattan;

  $('#anHero').innerHTML = anHero(d);
  $('#anNav').innerHTML = anNavBar();
  // The site's own sticky bar is 64px on a desktop and wraps taller on a phone, so the
  // chapter bar is pinned beneath whatever height it actually has.
  const top = document.querySelector('.topnav');
  if (top) $('#anNav').style.top = top.offsetHeight + 'px';

  // [A117] Every boundary figure the panel below shows, resolved once for the current scope.
  const bdy = {
    fours: yours ? d.your_total_fours : d.total_fours,
    sixes: yours ? d.your_total_sixes : d.total_sixes,
    share: yours ? d.your_boundary_share : d.boundary_share,
    sixBoard: yours ? d.your_most_sixes : d.most_sixes,
    fourBoard: yours ? d.your_most_fours : d.most_fours,
  };
  const scopeTag = anYoursHasData()
    ? `<span class="an-scope-tag${yours ? ' yours' : ''}">${yours ? 'Your side' : 'Whole league'}</span>`
    : '';

  $('#anBody').innerHTML = `
    ${anChapter('an-honours', '01', 'The honours', anHonoursLine(d), '', `
      <div class="an-honours">
        ${anAward('Orange Cap', 'orange', d.top_scorers[0], v => Math.round(v), 'runs')}
        ${anAward('Purple Cap', 'purple', d.top_wickets[0], v => Math.round(v), 'wickets')}
        ${anAward('Best economy', 'econ', d.best_economy[0], v => anFix(v, 2), 'an over')}
        ${anAward('Best strike rate', 'sr', d.best_strike[0], v => anFix(v), 'per 100 balls')}
        ${anMoment('Biggest over', d.best_over && {
            value: d.best_over.runs, unit: 'runs',
            name: `Over ${d.best_over.over}`,
            detail: `${esc(d.best_over.side)} · off ${esc(d.best_over.bowler)}`})}
        ${anMoment('Highest innings', d.highest_innings && {
            value: `${d.highest_innings.runs}/${d.highest_innings.wickets}`, unit: '',
            name: esc(d.highest_innings.side),
            detail: `${d.highest_innings.overs} overs`})}
      </div>`)}

    ${anChapter('an-innings', '02', 'How an innings unfolded', anInningsLine(phases, bars), scopeTag, `
      <div class="an-panel">
        <div class="an-panel-head">
          <div>
            <h3>Manhattan</h3>
            <p>${AN_METRIC === 'runs'
                  ? 'Runs per over, averaged over the innings that reached it.'
                  : 'Wickets that fell in each over, all season.'}</p>
          </div>
          <div class="an-toggle">
            <button class="an-tab ${AN_METRIC === 'runs' ? 'sel' : ''}" onclick="anSetMetric('runs')">Runs</button>
            <button class="an-tab ${AN_METRIC === 'wickets' ? 'sel' : ''}" onclick="anSetMetric('wickets')">Wickets</button>
          </div>
        </div>
        <div class="an-scroll">${manhattanSvg(bars, AN_METRIC)}</div>
        ${AN_METRIC === 'runs'
          ? '<div class="an-key"><i class="an-key-pip"></i>a pip marks the overs wickets fell in</div>'
          : ''}
      </div>
      <div class="an-phases">${phases.map(p => phaseCard(p, phases)).join('')}</div>`)}

    ${anChapter('an-bat', '03', 'With the bat', anBatLine(d), '', `
      <div class="an-vs">
        ${splitCard('Batting first', d.bat_first, 'first')}
        <div class="an-vs-mid">v</div>
        ${splitCard('Chasing', d.chasing, 'chase')}
      </div>
      <div class="an-panel">
        <div class="an-panel-head"><div>
          <h3>The batting order</h3>
          <p>Runs and average by the number a batter went in at.</p>
        </div></div>
        <div class="an-scroll">${positionSvg(d.positions)}</div>
      </div>
      <div class="an-lists an-lists-3">
        ${leaderPanel('Orange Cap', 'Most runs', d.top_scorers, 'orange', v => v, 'runs', AN_LEAD_ROWS)}
        ${leaderPanel('Best average', 'Runs per dismissal', d.best_averages, 'avg', v => v.toFixed(1), 'runs per dismissal', AN_LEAD_ROWS)}
        ${leaderPanel('Best strike rate', 'Runs per hundred balls', d.best_strike, 'sr', v => v.toFixed(1), 'runs per 100 balls', AN_LEAD_ROWS)}
      </div>`)}

    ${anChapter('an-ball', '04', 'With the ball', anBallLine(d), '', `
      <div class="an-lists an-lists-2">
        ${leaderPanel('Purple Cap', 'Most wickets', d.top_wickets, 'purple', v => v, 'wickets', AN_LEAD_ROWS)}
        ${leaderPanel('Best economy', 'Runs per over conceded', d.best_economy, 'econ', v => v.toFixed(2), 'runs per over', AN_LEAD_ROWS)}
      </div>
      <div class="an-sub">Who bowls the hard overs <span>Economy in each phase, attributed over by over</span></div>
      <div class="an-lists an-lists-2">
        ${phasePanel('At the death', 'Overs 16-20 · lowest economy', d.death_bowlers, 'death')}
        ${phasePanel('In the powerplay', 'Overs 1-6 · lowest economy', d.powerplay_bowlers, 'power')}
      </div>
      <div class="an-panel">
        <div class="an-panel-head"><div>
          <h3>Spin against pace</h3>
          <p>Economy, wickets and overs for each style, by phase.</p>
        </div></div>
        ${styleTable(d.style_phases, d.unknown_style_overs)}
      </div>`)}

    ${anChapter('an-bdy', '05', 'Boundaries', anBoundaryLine(bdy, phases, yours), scopeTag, `
      <div class="an-bdy-totals">
        <div><b>${bdy.fours.toLocaleString()}</b><span>fours</span></div>
        <div><b>${bdy.sixes.toLocaleString()}</b><span>sixes</span></div>
        <div><b>${bdy.share}%</b><span>of ${yours ? "your side's runs" : 'all runs'}</span></div>
      </div>
      ${boundaryPhases(phases)}
      <div class="an-lists an-lists-2">
        ${leaderPanel('Most sixes', 'Cleared the rope', bdy.sixBoard, 'six', v => v, 'sixes', AN_LEAD_ROWS)}
        ${leaderPanel('Most fours', 'Found the fence', bdy.fourBoard, 'four', v => v, 'fours', AN_LEAD_ROWS)}
      </div>`)}`;
}

function anHero(d){
  const wickets = anSum(d.phases, 'wickets');
  const balls = anSum(d.phases, 'balls');
  const rr = balls ? d.total_runs / (balls / 6) : 0;
  const stat = (v, label, cls = '') =>
    `<div class="an-hero-stat ${cls}"><b>${v}</b><span>${label}</span></div>`;
  return `<header class="an-hero">
    <div class="an-eyebrow">Season analysis · ${d.fixtures} matches · ${d.innings} innings ·
      ${d.overs_logged.toLocaleString()} overs</div>
    <h2 class="an-title">Where the season<br>was won</h2>
    <div class="an-hero-stats">
      ${stat(d.total_runs.toLocaleString(), 'runs')}
      ${stat(wickets.toLocaleString(), 'wickets')}
      ${stat(d.total_sixes.toLocaleString(), 'sixes', 'six')}
      ${stat(d.total_fours.toLocaleString(), 'fours')}
      ${stat(anFix(rr, 2), 'runs an over')}
    </div>
  </header>`;
}

function anNavBar(){
  const yours = AN_SCOPE === 'yours';
  const scope = anYoursHasData() ? `
    <div class="an-toggle an-nav-scope" title="Applies to the innings and boundaries chapters">
      <button class="an-tab ${!yours ? 'sel' : ''}" onclick="anSetScope('league')"><span class="an-long">Whole </span>league</button>
      <button class="an-tab ${yours ? 'sel' : ''}" onclick="anSetScope('yours')"><span class="an-long">Your </span>side</button>
    </div>` : '';
  return `
    <div class="an-nav-links">${AN_CHAPTERS.map(([id, label], i) =>
      `<button onclick="anJump('${id}')"><i>0${i + 1}</i>${label}</button>`).join('')}</div>
    ${scope}`;
}

function anChapter(id, num, title, line, tag, body){
  return `<section class="an-chapter" id="${id}">
    <div class="an-chapter-head">
      <div class="an-chapter-num">${num}</div>
      <div>
        <h3 class="an-chapter-title">${title} ${tag}</h3>
        ${line ? `<p class="an-chapter-line">${line}</p>` : ''}
      </div>
    </div>
    ${body}
  </section>`;
}

// --- the honours --------------------------------------------------------------------------

function anCrestMark(row){
  // A drafted side has no crest; it reads YOU on the leaderboards, so it gets the star the
  // rest of the site uses for your own side.
  return row.crest
    ? `<img class="an-award-crest" src="${row.crest}" alt="" loading="lazy">`
    : `<div class="an-award-crest an-award-star">★</div>`;
}

function anAward(label, kind, row, fmt, unit){
  if (!row) return '';
  return `<div class="an-award an-award-${kind}${row.team === 'YOU' ? ' yours' : ''}">
    ${anCrestMark(row)}
    <div class="an-award-label">${label}</div>
    <div class="an-award-value">${fmt(row.value)}<i>${unit}</i></div>
    <div class="an-award-name">${esc(row.name)}</div>
    <div class="an-award-detail">${teamTag(row.team)}${esc(row.detail || '')}</div>
  </div>`;
}

function anMoment(label, m){
  if (!m) return '';
  return `<div class="an-award an-award-moment">
    <div class="an-award-label">${label}</div>
    <div class="an-award-value">${m.value}<i>${m.unit}</i></div>
    <div class="an-award-name">${m.name}</div>
    <div class="an-award-detail">${m.detail}</div>
  </div>`;
}

// --- the chapter sentences ------------------------------------------------------------------
// Each returns '' when the data cannot support it, rather than a sentence about nothing.

function anHonoursLine(d){
  const o = d.top_scorers[0], p = d.top_wickets[0];
  if (!o || !p) return '';
  return `<b>${esc(o.name)}</b> made ${Math.round(o.value)} runs and <b>${esc(p.name)}</b>
    took ${Math.round(p.value)} wickets — the season's two caps.`;
}

function anInningsLine(phases, bars){
  const pp = anPhase(phases, 'powerplay'), death = anPhase(phases, 'death');
  if (!pp || !death || !pp.overs || !death.overs) return '';
  const diff = death.run_rate - pp.run_rate;
  const runs = anSum(phases, 'runs'), wk = anSum(phases, 'wickets'), ov = anSum(phases, 'overs');
  let s = `The death went at <b>${anFix(death.run_rate, 2)}</b> an over, ${anFix(Math.abs(diff))}
    ${diff >= 0 ? 'more' : 'less'} than the powerplay: its ${anPct(death.overs, ov)}% of the overs
    brought ${anPct(death.runs, runs)}% of the runs and ${anPct(death.wickets, wk)}% of the wickets.`;
  const reached = bars.filter(b => b.innings);
  if (reached.length){
    const peak = reached.reduce((a, b) => b.average_runs > a.average_runs ? b : a);
    s += ` The dearest over was the <b>${anOrdinal(peak.over)}</b>, at ${anFix(peak.average_runs)}
      runs an innings.`;
  }
  return s;
}

function anBatLine(d){
  const f = d.bat_first, c = d.chasing;
  if (!f.innings || !c.innings) return '';
  const chaseWon = c.win_rate > f.win_rate;
  let s = `${chaseWon ? 'Chasing' : 'Setting a total'} won <b>${anFix(
      chaseWon ? c.win_rate : f.win_rate, 0)}%</b> of matches; a first innings averaged
    ${anFix(f.average)}.`;
  // An average off a handful of dismissals is one score, not a reading of the position.
  const steady = d.positions.filter(p => p.outs >= 10);
  if (steady.length){
    const best = steady.reduce((a, b) => b.average > a.average ? b : a);
    s += ` Number <b>${best.position}</b> was the steadiest place to bat, at ${anFix(best.average)}
      runs a dismissal.`;
  }
  return s;
}

function anBallLine(d){
  const tally = style => {
    const rows = d.style_phases.filter(r => r.style === style);
    return {overs: anSum(rows, 'overs'), runs: anSum(rows, 'runs'), wickets: anSum(rows, 'wickets')};
  };
  const pace = tally('pace'), spin = tally('spin');
  if (!pace.overs || !spin.overs) return '';
  const econ = t => anFix(t.runs / t.overs, 2);
  const strike = t => t.wickets ? anFix(6 * t.overs / t.wickets) : '–';
  return `Spin went at <b>${econ(spin)}</b> an over to pace's <b>${econ(pace)}</b>, and took a
    wicket every ${strike(spin)} balls to pace's ${strike(pace)}.`;
}

function anBoundaryLine(b, phases, yours){
  let s = `<b>${b.share}%</b> of ${yours ? "your side's" : 'every'} run${yours ? 's' : ''} came
    in fours and sixes.`;
  const withSixes = phases.filter(p => p.sixes && p.balls);
  if (withSixes.length > 1){
    const every = p => p.balls / p.sixes;
    const most = withSixes.reduce((a, p) => every(p) < every(a) ? p : a);
    const least = withSixes.reduce((a, p) => every(p) > every(a) ? p : a);
    s += ` The ${most.label.toLowerCase()} cleared the rope every ${anFix(every(most))} balls,
      the ${least.label.toLowerCase()} every ${anFix(every(least))}.`;
  }
  return s;
}

// --- the Manhattan -------------------------------------------------------------------------
// Phase bands are painted BEHIND the bars rather than as a legend beside them, because the
// whole point of the chart is where in the innings a thing happened -- a viewer should be
// able to see "that spike is in the death" without moving their eyes off the bars.

function manhattanSvg(bars, metric){
  const W = 760, H = 300, L = 44, R = 12, T = 34, B = 42;
  const plotW = W - L - R, plotH = H - T - B;
  const vals = bars.map(b => metric === 'runs' ? b.average_runs : b.wickets);
  // The axis is drawn in quarters, so the top is rounded up to a multiple of four and every
  // gridline lands on a whole number (it used to read 0, 3, 6, 8, 11).
  const peak = Math.max(1, ...vals);
  const top = Math.ceil(peak / 4) * 4;
  const bw = plotW / 20;
  const y = v => T + plotH - (v / top) * plotH;

  // Bands: overs 1-6, 7-15, 16-20 (1-based, matching the axis).
  const band = (from, to, key) => {
    const x = L + (from - 1) * bw;
    return `<rect x="${x.toFixed(1)}" y="${T}" width="${((to - from + 1) * bw).toFixed(1)}"
      height="${plotH}" fill="${PHASE_TINT[key]}" opacity=".055"/>
      <text x="${(x + (to - from + 1) * bw / 2).toFixed(1)}" y="${T - 12}"
        class="an-band-label" fill="${PHASE_TINT[key]}">${key.toUpperCase()}</text>`;
  };

  const grid = [];
  const steps = 4;
  for (let i = 0; i <= steps; i++){
    const v = top * i / steps, yy = y(v);
    grid.push(`<line x1="${L}" x2="${W - R}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}"
      stroke="var(--line)" stroke-width="1" opacity="${i ? '.5' : '1'}"/>
      <text x="${L - 8}" y="${(yy + 4).toFixed(1)}" class="an-axis" text-anchor="end">${
        v.toFixed(0)}</text>`);
  }

  const cols = bars.map((b, i) => {
    const v = metric === 'runs' ? b.average_runs : b.wickets;
    const x = L + i * bw + bw * 0.16, w = bw * 0.68;
    const h = Math.max(v > 0 ? 2 : 0, T + plotH - y(v));
    const key = i < 6 ? 'powerplay' : (i < 15 ? 'middle' : 'death');
    const title = metric === 'runs'
      ? `Over ${b.over}: ${b.average_runs} runs per innings (${b.runs} in ${b.innings})`
      : `Over ${b.over}: ${b.wickets} wickets`;
    // A wicket pip above the bar keeps the two dimensions readable at once -- the runs
    // view still shows where wickets fell, which is what a Manhattan is read for.
    const pips = metric === 'runs' && b.wickets
      ? `<circle cx="${(x + w / 2).toFixed(1)}" cy="${(y(v) - 8).toFixed(1)}" r="3"
           fill="var(--hot)" opacity=".9"/>` : '';
    return `<g class="an-col"><title>${title}</title>
      <rect x="${x.toFixed(1)}" y="${y(v).toFixed(1)}" width="${w.toFixed(1)}"
        height="${h.toFixed(1)}" rx="2" fill="${PHASE_TINT[key]}" opacity=".85"/>
      ${pips}
      <text x="${(x + w / 2).toFixed(1)}" y="${H - B + 16}" class="an-axis"
        text-anchor="middle">${b.over}</text></g>`;
  });

  return `<svg class="an-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
    role="img" aria-label="Manhattan chart of ${metric} by over">
    ${band(1, 6, 'powerplay')}${band(7, 15, 'middle')}${band(16, 20, 'death')}
    ${grid.join('')}${cols.join('')}
    <text x="${L - 8}" y="${T - 12}" class="an-axis" text-anchor="end">${
      metric === 'runs' ? 'runs' : 'wkts'}</text>
    <text x="${W - R}" y="${H - 6}" class="an-axis" text-anchor="end">over</text>
  </svg>`;
}

// --- phases ---------------------------------------------------------------------------------

// --- phases ---------------------------------------------------------------------------------
// One card per phase: its rate up top, then three share bars saying what fraction of the
// innings' overs, runs and wickets it held. That replaces a separate stacked-bar chart
// below the cards: "the death is a quarter of the overs and a third of the wickets" is the
// sentence, and it reads best sitting on the phase it describes.

function phaseCard(p, phases){
  const share = k => anPct(p[k], anSum(phases, k));
  const bar = (label, k) => `<div class="an-share">
      <span>${label}</span><div><i style="width:${share(k)}%"></i></div><b>${share(k)}%</b>
    </div>`;
  return `<div class="an-phase" style="--tint:${PHASE_TINT[p.phase]}">
    <div class="an-phase-top">
      <div>
        <div class="an-phase-name">${p.label}</div>
        <div class="an-phase-overs">Overs ${p.overs_range}</div>
      </div>
      <div class="an-phase-rate"><b>${p.run_rate.toFixed(2)}</b><span>runs / over</span></div>
    </div>
    <div class="an-phase-rows">
      <div><em>${p.runs.toLocaleString()}</em> runs</div>
      <div><em>${p.wickets.toLocaleString()}</em> wickets</div>
      <div><em>${p.balls_per_wicket ? p.balls_per_wicket.toFixed(1) : '–'}</em> balls / wkt</div>
    </div>
    <div class="an-shares">
      ${bar('Overs', 'overs')}${bar('Runs', 'runs')}${bar('Wickets', 'wickets')}
    </div>
  </div>`;
}

// [A113] Spin against pace, per phase. A table rather than a chart: the interesting read
// is "compare economy down a column", and three phases x two styles is small enough that
// numbers beat bars -- the SVGs on this screen exist where a shape carries the meaning
// (a Manhattan, a phase share), which is not the case for six economies.
//
// `unknown` is invisible on screen -- no row, no footnote clause -- until it has overs,
// and then it gets both.
//
// [A114] It used to state the count in the footnote at zero too, so a reader could tell
// "nothing unknown" from "not measured". That was worth the line while A112's 30-ball
// threshold left 97 bowlers permanently unrecorded and the bucket held 3.9% of a real
// season's overs; a reader had a live reason to ask. A114 dropped the threshold to 1 and
// filled all 97, so the bucket is now empty in the ordinary case and the sentence had
// become a standing reassurance about a problem that no longer exists.
//
// The A23 guard itself is unchanged and is the reason both branches survive: the deck can
// still acquire a styleless bowler from a revised archive, and on the day it does the row
// and the count both appear. What went is the announcement at zero, not the reporting.
//
// `unknown` stays in ALL_STYLES regardless, because it is the DENOMINATOR -- a phase's
// share is over every style's overs, so folding it out would make the percentages down a
// column sum past 100 the moment the bucket is non-empty.
const ALL_STYLES = ['pace', 'spin', 'unknown'];

function styleTable(rows, unknownOvers){
  if (!rows || !rows.length) return '';
  const phases = [...new Set(rows.map(r => r.phase))];
  const styles = ['pace', 'spin'].concat(unknownOvers > 0 ? ['unknown'] : []);
  const cell = (p, s) => rows.find(r => r.phase === p && r.style === s)
    || {overs: 0, runs: 0, wickets: 0, economy: 0, balls_per_wicket: 0};
  const head = phases.map(p => {
    const r = rows.find(x => x.phase === p);
    return `<th>${r.phase_label}<i>Overs ${r.overs_range}</i></th>`;
  }).join('');
  const body = styles.map(s => {
    const label = (rows.find(r => r.style === s) || {}).style_label || s;
    const tds = phases.map(p => {
      const c = cell(p, s);
      if (!c.overs) return `<td class="an-sty-none">–</td>`;
      // Share of THIS phase's overs. Denominator is every style's overs in this phase --
      // including unknown even when it has no row of its own, so the percentages down a
      // column always describe the whole phase and sum to 100.
      const phaseOvers = ALL_STYLES.reduce((n, st) => n + cell(p, st).overs, 0);
      const share = phaseOvers ? Math.round(100 * c.overs / phaseOvers) : 0;
      return `<td title="${label}, ${c.overs} overs · ${c.runs} runs · ${c.wickets} wickets">
        <b>${c.economy.toFixed(2)}</b>
        <i>${c.wickets} wkt · ${c.overs} ov · ${share}% of phase</i></td>`;
    }).join('');
    return `<tr class="an-sty-${s}"><th scope="row">${label}</th>${tds}</tr>`;
  }).join('');
  return `<div class="an-scroll"><table class="an-sty">
      <thead><tr><th></th>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <p class="an-sty-foot">Economy is runs per over. Share is of that phase's overs; the
      bowling order is set by workload, not by phase.${unknownOvers > 0
      ? ` <b>${unknownOvers} over${unknownOvers === 1 ? '' : 's'}</b> were bowled by someone
         whose style is unrecorded, counted in their own row.`
      : ''}</p>`;
}


// [A115] Boundaries per phase: the counts, and what share of the phase's runs they were.
// The share is the reading worth having -- a death over is not just higher-scoring, it is
// differently scored, and the share says so where two raw counts do not. Bars are drawn
// against the largest share rather than 100%, because nothing reaches 100 and a bar with
// four fifths of it empty carries no information.
function boundaryPhases(phases){
  if (!phases || !phases.length) return '';
  const peak = Math.max(1, ...phases.map(p => p.boundary_share));
  return `<div class="an-bdy">${phases.map(p => `
    <div class="an-bdy-card" style="--tint:${PHASE_TINT[p.phase]}">
      <div class="an-bdy-head"><b>${p.label}</b><span>Overs ${p.overs_range}</span></div>
      <div class="an-bdy-counts">
        <div><em>${p.fours.toLocaleString()}</em><span>fours</span></div>
        <div><em>${p.sixes.toLocaleString()}</em><span>sixes</span></div>
      </div>
      <div class="an-bdy-share">
        <div class="an-bdy-bar"><span style="width:${(100 * p.boundary_share / peak).toFixed(1)}%"></span></div>
        <b>${p.boundary_share}%</b><span>of runs in boundaries</span>
      </div>
    </div>`).join('')}</div>`;
}

// --- leaders ----------------------------------------------------------------------------------
// Each row carries a thin bar measured against the board's leader, so the gap between first
// and fifth is visible without reading two numbers. On a lower-is-better board (an economy)
// the bar is leader / value, so the best still reads longest.

const AN_LOWER_BETTER = new Set(['econ', 'death', 'power']);

function anLeadRow(r, i, kind, value, numeric, best, tip, sub){
  const frac = AN_LOWER_BETTER.has(kind)
    ? (numeric ? best / numeric : 0)
    : (best ? numeric / best : 0);
  return `
    <div class="an-lead-row${i === 0 ? ' top' : ''}${r.team === 'YOU' ? ' yours' : ''}"
         title="${attr(tip)}" style="--frac:${Math.max(0, Math.min(1, frac)).toFixed(3)}">
      <span class="an-lead-pos">${i + 1}</span>
      <span class="an-lead-name">${crestImg(r.crest, 'lead-crest')}${esc(r.name)}</span>
      <span class="an-lead-value">${value}</span>
      <span class="an-lead-sub">${teamTag(r.team)}${sub}</span>
      <span class="an-lead-bar"></span>
    </div>`;
}

function anLeadShell(title, sub, kind, rows, limit){
  const more = limit && rows.length > limit
    ? `<button class="an-lead-more-btn" data-more="Show all ${rows.length}"
         onclick="anToggleMore(this)">Show all ${rows.length}</button>` : '';
  return `<div class="an-lead an-lead-${kind}${limit ? ` an-lead-limit-${limit}` : ''}">
    <div class="an-lead-head"><b>${title}</b><span>${sub}</span></div>
    ${rows.join('')}${more}
  </div>`;
}

// `limit` is optional: the records page shows every row, this screen folds past five.
function leaderPanel(title, sub, rows, kind, fmt, unit, limit){
  if (!rows.length) return '';
  const best = rows[0].value;
  // `unit` is the board's own reading of its number, so the tooltip says "675 runs" on
  // the Orange Cap and "6.26 runs per over" on the economy board rather than one generic
  // word that is wrong on four of the five.
  const body = rows.map((r, i) => {
    const tip = `${r.name}${r.team ? ' — ' + r.team : ''} · ${fmt(r.value)} ${unit}`
      + (r.detail ? ` · ${r.detail}` : '');
    return anLeadRow(r, i, kind, fmt(r.value), r.value, best, tip, esc(r.detail || ''));
  });
  return anLeadShell(title, sub, kind, body, limit);
}

// --- [A110] phase specialists, the setting/chasing split, and the batting order ---------

function phasePanel(title, sub, rows, kind){
  if (!rows || !rows.length){
    return `<div class="an-lead an-lead-${kind}">
      <div class="an-lead-head"><b>${title}</b><span>${sub}</span></div>
      <div class="cap-empty">Nobody bowled enough overs in this phase to rank.</div></div>`;
  }
  const shown = rows.slice(0, AN_LEAD_ROWS);
  const best = shown[0].economy;
  const body = shown.map((r, i) => {
    const tip = `${r.name}${r.team ? ' — ' + r.team : ''}`
      + ` · economy ${r.economy.toFixed(2)} · ${r.overs} overs · ${r.wickets} wickets`;
    return anLeadRow(r, i, kind, r.economy.toFixed(2), r.economy, best, tip,
                     `${r.overs} ov · ${r.wickets}w`);
  });
  return anLeadShell(title, sub, kind, body, 0);
}

function splitCard(title, s, kind){
  // A dash, not a 0%, when nothing has been played -- the same "no evidence is a dash"
  // convention the ratings and the profile page already use. Win rate leads because it is
  // the question ("bat or bowl?"); the average score is the context under it.
  const pct = s.innings ? s.win_rate.toFixed(0) : '–';
  return `<div class="an-split an-split-${kind}">
    <div class="an-split-title">${title}</div>
    <div class="an-split-big">${pct}<i>${s.innings ? '% won' : ''}</i></div>
    <div class="an-split-gauge"><span style="width:${s.innings ? s.win_rate : 0}%"></span></div>
    <div class="an-split-rows">
      <div><em>${s.wins}</em> wins from <em>${s.innings}</em></div>
      <div><em>${s.innings ? s.average.toFixed(1) : '–'}</em> average score</div>
    </div>
  </div>`;
}

// Runs by batting position, with average drawn as a line over the bars. Two quantities
// that answer different questions -- volume against reliability -- and the interesting
// reading is where they disagree.
function positionSvg(rows){
  if (!rows || !rows.length) return '';
  const W = 760, H = 250, L = 44, R = 40, T = 24, B = 40;
  const plotW = W - L - R, plotH = H - T - B;
  const bw = plotW / rows.length;
  const maxRuns = Math.max(1, ...rows.map(r => r.runs));
  const maxAvg = Math.max(1, ...rows.map(r => r.average));
  const yRuns = v => T + plotH - (v / maxRuns) * plotH;
  const yAvg = v => T + plotH - (v / maxAvg) * plotH;

  const bars = rows.map((r, i) => {
    const x = L + i * bw + bw * 0.18, w = bw * 0.64;
    const h = Math.max(r.runs > 0 ? 2 : 0, T + plotH - yRuns(r.runs));
    return `<g class="an-col"><title>Position ${r.position}: ${r.runs} runs, average ${
        r.outs ? r.average : '--'}, SR ${r.strike_rate} over ${r.innings} innings</title>
      <rect x="${x.toFixed(1)}" y="${yRuns(r.runs).toFixed(1)}" width="${w.toFixed(1)}"
        height="${h.toFixed(1)}" rx="2" fill="var(--gold)" opacity=".7"/>
      <text x="${(x + w / 2).toFixed(1)}" y="${H - B + 16}" class="an-axis"
        text-anchor="middle">${r.position}</text></g>`;
  }).join('');

  const pts = rows.filter(r => r.outs)
    .map(r => `${(L + (r.position - 1) * bw + bw / 2).toFixed(1)},${yAvg(r.average).toFixed(1)}`);
  const line = pts.length > 1
    ? `<polyline points="${pts.join(' ')}" fill="none" stroke="var(--mint)"
         stroke-width="2" stroke-linejoin="round"/>` +
      pts.map(p => `<circle cx="${p.split(',')[0]}" cy="${p.split(',')[1]}" r="3"
         fill="var(--mint)"/>`).join('')
    : '';

  return `<svg class="an-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
    role="img" aria-label="Runs and average by batting position">
    <text x="${L - 8}" y="${T - 8}" class="an-axis" text-anchor="end">runs</text>
    <text x="${W - R + 8}" y="${T - 8}" class="an-axis">avg</text>
    ${bars}${line}
    <text x="${W - R}" y="${H - 6}" class="an-axis" text-anchor="end">batting position</text>
  </svg>
  <div class="an-key"><i class="an-key-swatch gold"></i>runs scored
    <i class="an-key-swatch mint"></i>average (runs per dismissal)</div>`;
}


// [A111] A leaderboard row is a (person, SIDE) pair, not a person: the same man can turn
// out for more than one drawn side in one tournament, and without the team two rows for
// him read as a duplicate rather than as two different stints.
//
// The tag sits on the row's SECOND line, beside the detail, rather than inline after the
// name. On one line it was competing with the name for the same ~120px and losing to it:
// five boards across a 1200px screen made "T Stubbs" render as "T St..." and the tag
// itself never appeared at all, so the A111 fix it exists to deliver was invisible on
// every row. A second line costs vertical space the panel has and buys back the width
// the names did not.
function teamTag(team){
  // A room side's tag comes from a player's own name, so it is escaped like any typed text.
  return team ? `<i class="an-team">${esc(team)}</i>` : '';
}

// The `title` remains, because a name can still outrun its line on a narrow viewport --
// but it is now the fallback rather than the only way to read a row. It is the same
// tooltip mechanism the SVGs in this file already use (manhattanSvg,
// positionSvg all carry <title> children): one convention, no positioning logic to get
// wrong, and it cannot overflow the viewport.
//
// Escaped because this lands in an ATTRIBUTE, where a double quote in a name would end
// the attribute early and corrupt the markup. Archive names are plain text today, so
// this guards against a future one rather than a present one.
function attr(s){
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
