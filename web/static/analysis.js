// Season Analysis -- the graphics a broadcast puts up after a tournament, drawn as inline
// SVG with no charting library (the same "no dependency we don't need" stance the rest of
// this project takes; `fieldWheel` in common.js already established the pattern).
//
// Shared verbatim by the solo season page and a league room, which is the whole reason it
// lives here rather than in either one: both call `renderAnalysis(data, host)` with the
// identical `AnalysisOut` payload, so the two screens cannot drift.
//
// The two toggles ARE the "search" the feature was asked for. Rather than a query box that
// can be typed into wrongly, the dimensions the engine can actually answer -- runs against
// wickets, the whole league against your own side -- are the axes themselves, so every
// combination a viewer can reach is one the data supports.

let ANALYSIS = null;
let AN_METRIC = 'runs';      // 'runs' | 'wickets'
let AN_SCOPE = 'league';     // 'league' | 'yours'

const PHASE_TINT = {powerplay: 'var(--gold)', middle: 'var(--ink-2)', death: 'var(--hot)'};

function renderAnalysis(d, host){
  ANALYSIS = d;
  if (!anYoursHasData()) AN_SCOPE = 'league';   // a spectator seat has no side of its own
  host.innerHTML = `
    <div class="an-head">
      <div>
        <div class="an-eyebrow">The Almanack · Season analysis</div>
        <div class="an-title">Where the season was won</div>
      </div>
      <div class="an-stats">
        ${anStat(d.fixtures, 'matches')}
        ${anStat(d.innings, 'innings')}
        ${anStat(d.overs_logged.toLocaleString(), 'overs')}
      </div>
    </div>
    <div id="anBody"></div>`;
  anPaint();
}

function anYoursHasData(){
  return !!ANALYSIS && ANALYSIS.your_phases.some(p => p.overs > 0);
}

function anStat(v, label){
  return `<div class="an-stat"><b>${v}</b><span>${label}</span></div>`;
}

function anSetMetric(m){ AN_METRIC = m; anPaint(); }
function anSetScope(s){ AN_SCOPE = s; anPaint(); }

function anPaint(){
  const d = ANALYSIS;
  const phases = AN_SCOPE === 'yours' ? d.your_phases : d.phases;
  const bars = AN_SCOPE === 'yours' ? d.your_manhattan : d.manhattan;
  const scopeToggle = anYoursHasData() ? `
    <div class="an-toggle">
      <button class="an-tab ${AN_SCOPE === 'league' ? 'sel' : ''}" onclick="anSetScope('league')">Whole league</button>
      <button class="an-tab ${AN_SCOPE === 'yours' ? 'sel' : ''}" onclick="anSetScope('yours')">Your side</button>
    </div>` : '';

  // [A117] Every boundary figure the panel below shows, resolved once for the current
  // scope. The panel carries its own copy of the toggle rather than relying on the one in
  // the Manhattan head three screens up -- that is where this was reported from, and a
  // control that governs a number should be next to the number. Both write the same
  // global and `anPaint` redraws everything, so the two can never disagree.
  const yours = AN_SCOPE === 'yours';
  const bdy = {
    fours: yours ? d.your_total_fours : d.total_fours,
    sixes: yours ? d.your_total_sixes : d.total_sixes,
    share: yours ? d.your_boundary_share : d.boundary_share,
    sixBoard: yours ? d.your_most_sixes : d.most_sixes,
    fourBoard: yours ? d.your_most_fours : d.most_fours,
  };

  $('#anBody').innerHTML = `
    <div class="an-panel">
      <div class="an-panel-head">
        <div>
          <h3>Manhattan</h3>
          <p>${AN_METRIC === 'runs'
                ? 'Runs per over, averaged over the innings that reached it.'
                : 'Wickets per over.'}</p>
        </div>
        <div class="an-controls">
          <div class="an-toggle">
            <button class="an-tab ${AN_METRIC === 'runs' ? 'sel' : ''}" onclick="anSetMetric('runs')">Runs</button>
            <button class="an-tab ${AN_METRIC === 'wickets' ? 'sel' : ''}" onclick="anSetMetric('wickets')">Wickets</button>
          </div>
          ${scopeToggle}
        </div>
      </div>
      <div class="an-scroll">${manhattanSvg(bars, AN_METRIC)}</div>
      ${AN_METRIC === 'runs'
        ? '<div class="an-key"><i class="an-key-pip"></i>a pip marks the overs wickets fell in</div>'
        : ''}
    </div>

    <div class="an-panel">
      <div class="an-panel-head"><div>
        <h3>The three phases</h3>
        <p>Overs 1-6, 7-15, 16-20.</p>
      </div></div>
      <div class="an-phases">${phases.map(phaseCard).join('')}</div>
      <div class="an-scroll">${phaseBarSvg(phases)}</div>
    </div>

    <div class="an-panel">
      <div class="an-panel-head"><div><h3>The season's biggest moments</h3></div></div>
      <div class="an-moments">
        ${momentCard('Biggest over', d.best_over && `${d.best_over.runs}`,
                     d.best_over && `Over ${d.best_over.over} · ${d.best_over.side} · off ${d.best_over.bowler}`,
                     'runs')}
        ${momentCard('Highest innings', d.highest_innings && `${d.highest_innings.runs}/${d.highest_innings.wickets}`,
                     d.highest_innings && `${d.highest_innings.side} · ${d.highest_innings.overs} overs`,
                     '')}
      </div>
    </div>

    <div class="an-panel">
      <div class="an-panel-head"><div>
        <h3>Who bowls the hard overs</h3>
        <p>Economy in each phase, attributed over by over.</p>
      </div></div>
      <div class="an-lists an-lists-2">
        ${phasePanel('At the death', 'Overs 16-20 · lowest economy', d.death_bowlers, 'death')}
        ${phasePanel('In the powerplay', 'Overs 1-6 · lowest economy', d.powerplay_bowlers, 'power')}
      </div>
    </div>

    <div class="an-panel">
      <div class="an-panel-head"><div>
        <h3>Spin against pace</h3>
        <p>Economy, wickets and overs for each style, by phase.</p>
      </div></div>
      ${styleTable(d.style_phases, d.unknown_style_overs)}
    </div>

    <div class="an-panel">
      <div class="an-panel-head">
        <div>
          <h3>Boundaries</h3>
          <p>${bdy.fours.toLocaleString()} fours and ${bdy.sixes.toLocaleString()}
             sixes — ${bdy.share}% of ${yours ? "your side's runs" : 'every run scored'}.</p>
        </div>
        ${scopeToggle}
      </div>
      ${boundaryPhases(phases)}
      <div class="an-lists an-lists-2">
        ${leaderPanel('Most sixes', 'Cleared the rope', bdy.sixBoard, 'six', v => v, 'sixes')}
        ${leaderPanel('Most fours', 'Found the fence', bdy.fourBoard, 'four', v => v, 'fours')}
      </div>
    </div>

    <div class="an-panel">
      <div class="an-panel-head"><div>
        <h3>Setting or chasing</h3>
        <p>Average score and win rate. A chase ends when it is won.</p>
      </div></div>
      <div class="an-vs">
        ${splitCard('Batting first', d.bat_first, 'first')}
        ${splitCard('Chasing', d.chasing, 'chase')}
      </div>
    </div>

    <div class="an-panel">
      <div class="an-panel-head"><div>
        <h3>The batting order</h3>
        <p>Runs and average by the number a batter went in at.</p>
      </div></div>
      <div class="an-scroll">${positionSvg(d.positions)}</div>
    </div>

    <div class="an-lists an-lists-5">
      ${leaderPanel('Orange Cap', 'Most runs', d.top_scorers, 'orange', v => v, 'runs')}
      ${leaderPanel('Purple Cap', 'Most wickets', d.top_wickets, 'purple', v => v, 'wickets')}
      ${leaderPanel('Best average', 'Runs per dismissal', d.best_averages, 'avg', v => v.toFixed(1), 'runs per dismissal')}
      ${leaderPanel('Best economy', 'Runs per over conceded', d.best_economy, 'econ', v => v.toFixed(2), 'runs per over')}
      ${leaderPanel('Best strike rate', 'Runs per hundred balls', d.best_strike, 'sr', v => v.toFixed(1), 'runs per 100 balls')}
    </div>`;
}

// --- the Manhattan -------------------------------------------------------------------------
// Phase bands are painted BEHIND the bars rather than as a legend beside them, because the
// whole point of the chart is where in the innings a thing happened -- a viewer should be
// able to see "that spike is in the death" without moving their eyes off the bars.

function manhattanSvg(bars, metric){
  const W = 760, H = 300, L = 44, R = 12, T = 34, B = 42;
  const plotW = W - L - R, plotH = H - T - B;
  const vals = bars.map(b => metric === 'runs' ? b.average_runs : b.wickets);
  const peak = Math.max(1, ...vals);
  const top = metric === 'runs' ? Math.ceil(peak) : Math.max(1, Math.ceil(peak));
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
        metric === 'runs' ? v.toFixed(0) : v.toFixed(0)}</text>`);
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

function phaseCard(p){
  return `<div class="an-phase" style="--tint:${PHASE_TINT[p.phase]}">
    <div class="an-phase-name">${p.label}</div>
    <div class="an-phase-overs">Overs ${p.overs_range}</div>
    <div class="an-phase-rate"><b>${p.run_rate.toFixed(2)}</b><span>runs / over</span></div>
    <div class="an-phase-rows">
      <div><em>${p.runs.toLocaleString()}</em> runs</div>
      <div><em>${p.wickets.toLocaleString()}</em> wickets</div>
      <div><em>${p.balls_per_wicket ? p.balls_per_wicket.toFixed(1) : '--'}</em> balls / wicket</div>
    </div>
  </div>`;
}

// A single stacked bar of where the season's runs and wickets actually came from. Shares are
// what a viewer wants here -- "the death is a fifth of the overs and a third of the wickets"
// is the sentence, and two 100%-wide bars say it without any axis at all.
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

function phaseBarSvg(phases){
  const W = 760, rowH = 34, pad = 8;
  const rows = [['Runs', phases.map(p => p.runs)],
                ['Wickets', phases.map(p => p.wickets)],
                ['Overs', phases.map(p => p.overs)]];
  const H = rows.length * (rowH + pad) + 24;
  const L = 74;
  const out = rows.map(([label, vals], r) => {
    const total = vals.reduce((a, b) => a + b, 0) || 1;
    let x = L;
    const y = r * (rowH + pad) + 10;
    const segs = vals.map((v, i) => {
      const w = (v / total) * (W - L);
      const key = ['powerplay', 'middle', 'death'][i];
      const pct = Math.round(100 * v / total);
      const seg = `<g><title>${label}, ${key}: ${v.toLocaleString()} (${pct}%)</title>
        <rect x="${x.toFixed(1)}" y="${y}" width="${Math.max(0, w - 2).toFixed(1)}"
          height="${rowH}" rx="3" fill="${PHASE_TINT[key]}" opacity=".8"/>
        ${w > 44 ? `<text x="${(x + w / 2 - 1).toFixed(1)}" y="${y + rowH / 2 + 5}"
          class="an-seg" text-anchor="middle">${pct}%</text>` : ''}</g>`;
      x += w;
      return seg;
    }).join('');
    return `<text x="${L - 12}" y="${y + rowH / 2 + 5}" class="an-axis"
      text-anchor="end">${label}</text>${segs}`;
  }).join('');
  return `<svg class="an-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
    role="img" aria-label="Share of runs, wickets and overs by phase">${out}</svg>`;
}

// --- moments and leaders ---------------------------------------------------------------------

function momentCard(label, value, detail, unit){
  if (!value) return '';
  return `<div class="an-moment">
    <div class="an-moment-label">${label}</div>
    <div class="an-moment-value">${value}<i>${unit}</i></div>
    <div class="an-moment-detail">${detail}</div>
  </div>`;
}

function leaderPanel(title, sub, rows, kind, fmt, unit){
  if (!rows.length) return '';
  // `unit` is the board's own reading of its number, so the tooltip says "675 runs" on
  // the Orange Cap and "6.26 runs per over" on the economy board rather than one generic
  // word that is wrong on four of the five.
  const body = rows.map((r, i) => {
    const tip = `${r.name}${r.team ? ' — ' + r.team : ''} · ${fmt(r.value)} ${unit}`
      + (r.detail ? ` · ${r.detail}` : '');
    return `
    <div class="an-lead-row${i === 0 ? ' top' : ''}" title="${attr(tip)}">
      <span class="an-lead-pos">${i + 1}</span>
      <span class="an-lead-name">${r.name}</span>
      <span class="an-lead-value">${fmt(r.value)}</span>
      <span class="an-lead-sub">${teamTag(r.team)}${r.detail}</span>
    </div>`;
  }).join('');
  return `<div class="an-lead an-lead-${kind}">
    <div class="an-lead-head"><b>${title}</b><span>${sub}</span></div>
    ${body}
  </div>`;
}


// --- [A110] phase specialists, the setting/chasing split, and the batting order ---------

function phasePanel(title, sub, rows, kind){
  if (!rows || !rows.length){
    return `<div class="an-lead an-lead-${kind}">
      <div class="an-lead-head"><b>${title}</b><span>${sub}</span></div>
      <div class="cap-empty">Nobody bowled enough overs in this phase to rank.</div></div>`;
  }
  const body = rows.slice(0, 6).map((r, i) => {
    const tip = `${r.name}${r.team ? ' — ' + r.team : ''}`
      + ` · economy ${r.economy.toFixed(2)} · ${r.overs} overs · ${r.wickets} wickets`;
    return `
    <div class="an-lead-row${i === 0 ? ' top' : ''}" title="${attr(tip)}">
      <span class="an-lead-pos">${i + 1}</span>
      <span class="an-lead-name">${r.name}</span>
      <span class="an-lead-value">${r.economy.toFixed(2)}</span>
      <span class="an-lead-sub">${teamTag(r.team)}${r.overs} ov · ${r.wickets}w</span>
    </div>`;
  }).join('');
  return `<div class="an-lead an-lead-${kind}">
    <div class="an-lead-head"><b>${title}</b><span>${sub}</span></div>${body}</div>`;
}

function splitCard(title, s, kind){
  // A dash, not a 0%, when nothing has been played -- the same "no evidence is a dash"
  // convention the ratings and the profile page already use.
  const pct = s.innings ? s.win_rate.toFixed(0) + '%' : '–';
  return `<div class="an-split an-split-${kind}">
    <div class="an-split-title">${title}</div>
    <div class="an-split-big">${s.innings ? s.average.toFixed(1) : '–'}<i>avg score</i></div>
    <div class="an-split-gauge"><span style="width:${s.innings ? s.win_rate : 0}%"></span></div>
    <div class="an-split-rows">
      <div><em>${pct}</em> of these were won</div>
      <div><em>${s.wins}</em> wins from <em>${s.innings}</em> innings</div>
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
  return team ? `<i class="an-team">${team}</i>` : '';
}

// The `title` remains, because a name can still outrun its line on a narrow viewport --
// but it is now the fallback rather than the only way to read a row. It is the same
// tooltip mechanism the SVGs in this file already use (manhattanSvg, phaseBarSvg,
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
