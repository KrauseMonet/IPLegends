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

  $('#anBody').innerHTML = `
    <div class="an-panel">
      <div class="an-panel-head">
        <div>
          <h3>Manhattan</h3>
          <p>${AN_METRIC === 'runs'
                ? 'Average runs per over, across every innings that reached it.'
                : 'Wickets falling in each over of the innings.'}</p>
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
        <p>Powerplay, middle overs, death -- split exactly the way the ratings split them.</p>
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

    <div class="an-lists">
      ${leaderPanel('Orange Cap', 'Most runs', d.top_scorers, 'orange', v => v)}
      ${leaderPanel('Purple Cap', 'Most wickets', d.top_wickets, 'purple', v => v)}
      ${leaderPanel('Best economy', 'Runs per over conceded', d.best_economy, 'econ', v => v.toFixed(2))}
      ${leaderPanel('Best strike rate', 'Runs per hundred balls', d.best_strike, 'sr', v => v.toFixed(1))}
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

function leaderPanel(title, sub, rows, kind, fmt){
  if (!rows.length) return '';
  const body = rows.map((r, i) => `
    <div class="an-lead-row${i === 0 ? ' top' : ''}">
      <span class="an-lead-pos">${i + 1}</span>
      <span class="an-lead-name">${r.name}</span>
      <span class="an-lead-detail">${r.detail}</span>
      <span class="an-lead-value">${fmt(r.value)}</span>
    </div>`).join('');
  return `<div class="an-lead an-lead-${kind}">
    <div class="an-lead-head"><b>${title}</b><span>${sub}</span></div>
    ${body}
  </div>`;
}
