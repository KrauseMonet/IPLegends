// [A320] Compare two IPL players. Everything comes from one static file (compare.json, built
// by tools/almanack_compare.py), including which rows to show and which way is better, so
// the page never judges a figure differently from the build. The address holds the pair
// (?a=slug&b=slug), so a comparison can be sent to someone.

const CMP = {doc: null, byName: null, a: null, b: null, metric: 'runs', h2h: null};

function cmpEsc(s){ return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
function cmpFold(s){ return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim(); }

async function cmpLoad(){
  const box = document.getElementById('cmpPick');
  const r = await fetch(box.dataset.src);
  CMP.doc = await r.json();
  CMP.byName = new Map(Object.entries(CMP.doc.players).map(([slug, p]) => [cmpFold(p.name), slug]));
}

function cmpSlugOf(input){
  const v = cmpFold(input.value);
  if (!v) return null;
  if (CMP.byName.has(v)) return CMP.byName.get(v);
  const hits = [...CMP.byName.entries()].filter(([n]) => n.includes(v));
  return hits.length === 1 ? hits[0][1] : null;
}

function cmpPick(){
  const a = cmpSlugOf(document.getElementById('cmpA')), b = cmpSlugOf(document.getElementById('cmpB'));
  if (a) CMP.a = a;
  if (b) CMP.b = b;
  if (CMP.a && CMP.b){ cmpDefaultMetric(); cmpDraw(); }
}

function cmpSwap(){
  [CMP.a, CMP.b] = [CMP.b, CMP.a];
  cmpDraw();
}

// The chart opens on what the two are known for: wickets for two bowlers, runs otherwise
// (a wicket counted as twenty runs, the Almanack's own scale for one list of both).
function cmpDefaultMetric(){
  const x = CMP.doc.players[CMP.a], y = CMP.doc.players[CMP.b];
  CMP.metric = 20 * (x.wkts + y.wkts) > x.runs + y.runs ? 'wickets' : 'runs';
}

// ------------------------------------------------------------------ the comparison

function cmpShown(p, key, places){
  if (key === 'hs') return p.hs_t || '–';
  if (key === 'best') return p.best_t || '–';
  const v = p[key];
  if (v === null || v === undefined) return '–';
  return places ? v.toFixed(places) : v.toLocaleString('en-US');
}

// -1: the first is better, 1: the second, 0: level or nothing to compare.
function cmpLeader(x, y, key, better){
  const p = x[key], q = y[key];
  if (p === null || p === undefined || q === null || q === undefined || p === q) return 0;
  return (better === 'high' ? p > q : p < q) ? -1 : 1;
}

function cmpRows(x, y){
  return CMP.doc.rows.filter(([key, , group]) => {
    if (group === 'bat' && Math.max(x.runs, y.runs) < CMP.doc.bat_from) return false;
    if (group === 'bowl' && Math.max(x.wkts, y.wkts) < CMP.doc.bowl_from) return false;
    return !(x[key] == null && y[key] == null);
  });
}

// A bar's length: its share of the better of the two (for "lower is better", the better
// figure's share of it), so the leader's bar is always full.
function cmpLen(v, other, better){
  if (v == null) return 0;
  if (better === 'high'){ const top = Math.max(v, other ?? 0); return top > 0 ? 100 * v / top : 0; }
  const low = Math.min(v, other ?? v);
  return v > 0 ? 100 * low / v : 0;
}

function cmpCard(p, side){
  const role = {batter: 'Batter', bowler: 'Bowler', allrounder: 'All-rounder', keeper: 'Wicketkeeper-batter'}[p.role] || 'Player';
  return `<a class="al-cmp-card ${p.key ? 'crest-' + p.key : ''} ${side}" href="${p.path}">
    ${p.crest ? `<img src="${p.crest}" alt="" width="90" height="90">` : ''}
    <em>${cmpEsc(role)}${p.country ? ' · ' + cmpEsc(p.country) : ''}</em>
    <b>${cmpEsc(p.name)}</b>
    <span>${cmpEsc(p.team)} · ${p.years[0]}${p.years[1] !== p.years[0] ? '–' + p.years[1] : ''}</span></a>`;
}

function cmpDraw(){
  const x = CMP.doc.players[CMP.a], y = CMP.doc.players[CMP.b];
  document.getElementById('cmpA').value = x.name;
  document.getElementById('cmpB').value = y.name;
  history.replaceState(null, '', `?a=${CMP.a}&b=${CMP.b}`);
  const stat = document.getElementById('cmpStatic');
  if (stat) stat.hidden = true;
  const rows = cmpRows(x, y);
  const wins = [0, 0];
  const groups = {career: 'Career', bat: 'Batting', bowl: 'Bowling'};
  let html = '', group = null;
  for (const [key, label, g, better, places] of rows){
    if (g !== group){ group = g; html += `<div class="al-cmp-group">${groups[g]}</div>`; }
    const lead = cmpLeader(x, y, key, better);
    if (lead < 0) wins[0]++; else if (lead > 0) wins[1]++;
    html += `<div class="al-cmp-row">
      <b class="${lead < 0 ? 'lead' : ''}">${cmpShown(x, key, places)}</b>
      <span class="al-cmp-bar l ${x.key ? 'crest-' + x.key : ''}"><i style="width:${cmpLen(x[key], y[key], better).toFixed(1)}%" class="${lead < 0 ? 'lead' : ''}"></i></span>
      <span class="al-cmp-k">${cmpEsc(label)}${better === 'low' ? ' <small>lower is better</small>' : ''}</span>
      <span class="al-cmp-bar r ${y.key ? 'crest-' + y.key : ''}"><i style="width:${cmpLen(y[key], x[key], better).toFixed(1)}%" class="${lead > 0 ? 'lead' : ''}"></i></span>
      <b class="${lead > 0 ? 'lead' : ''}">${cmpShown(y, key, places)}</b></div>`;
  }
  const total = wins[0] + wins[1];
  const verdict = wins[0] === wins[1]
    ? `Level: ${wins[0]} figures each`
    : `${cmpEsc((wins[0] > wins[1] ? x : y).name)} leads ${Math.max(...wins)}–${Math.min(...wins)}`;
  document.getElementById('cmpOut').innerHTML = `
    <div class="al-cmp-cards">${cmpCard(x, 'l')}<span class="al-cmp-vs">v</span>${cmpCard(y, 'r')}</div>
    <div class="al-cmp-verdict"><b>${verdict}</b>
      <div class="al-cmp-tally"><i class="${x.key ? 'crest-' + x.key : ''}" style="width:${total ? 100 * wins[0] / total : 50}%"></i><i class="${y.key ? 'crest-' + y.key : ''}" style="width:${total ? 100 * wins[1] / total : 50}%"></i></div>
      <small>of the ${total} figures where the two differ</small></div>
    <div class="al-cmp-rows">${html}</div>
    ${cmpPar(x, y)}
    ${cmpChart(x, y)}
    <div id="cmpMet"></div>
    <div class="actions al-cmp-actions"><button class="act lead" onclick="scCompareCard()">Share card ⤴</button>
      <button class="act" onclick="cmpSwap()">⇄ Swap sides</button>
      <button class="act" onclick="cmpCopy()">Copy the link</button></div>`;
  document.querySelectorAll('.al-cmp-metric button').forEach(b => b.addEventListener('click', () => {
    CMP.metric = b.dataset.m; cmpDraw();
  }));
  requestAnimationFrame(() => document.getElementById('cmpOut').classList.add('in'));
  cmpMet(x, y);
}

// ------------------------------------------------------------------ against league par

function cmpPar(x, y){
  // The same thresholds as the figure rows: no batting edge for two bowlers, and back.
  const sides = [['Batting', 'pb', 'Runs above par per 100 balls', Math.max(x.runs, y.runs) >= CMP.doc.bat_from],
                 ['Bowling', 'pw', 'Runs saved per 100 balls', Math.max(x.wkts, y.wkts) >= CMP.doc.bowl_from]];
  let out = '';
  for (const [title, k, unit, show] of sides){
    if (!show) continue;
    const rows = CMP.doc.par.filter(([key]) => x[k][key] != null || y[k][key] != null);
    if (!rows.length) continue;
    const top = Math.max(10, ...rows.flatMap(([key]) => [Math.abs(x[k][key] || 0), Math.abs(y[k][key] || 0)]));
    const cell = v => v == null ? '<span class="al-cmp-pv">–</span>'
      : `<span class="al-cmp-pv ${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}</span>`;
    const bar = (v, dir) => v == null ? '<span class="al-cmp-pt"></span>'
      : `<span class="al-cmp-pt ${dir}"><i class="${v >= 0 ? 'up' : 'down'}" style="width:${(100 * Math.abs(v) / top).toFixed(1)}%"></i></span>`;
    out += `<div class="al-cmp-group">${title} against the league <small>${unit}</small></div>` + rows.map(([key, label]) => `
      <div class="al-cmp-row par">${cell(x[k][key])}${bar(x[k][key], 'l')}<span class="al-cmp-k">${cmpEsc(label)}</span>${bar(y[k][key], 'r')}${cell(y[k][key])}</div>`).join('');
  }
  return out ? `<div class="al-cmp-rows">${out}</div>` : '';
}

// ------------------------------------------------------------------ both careers, season by season

function cmpChart(x, y){
  const metric = CMP.metric;
  const col = metric === 'runs' ? 1 : 2;
  const years = [...new Set([...x.s, ...y.s].map(s => s[0]))].sort((a, b) => a - b);
  if (years.length < 2) return '';
  const by = p => { const m = new Map(); p.s.forEach(s => m.set(s[0], (m.get(s[0]) || 0) + s[col])); return m; };
  const xs = by(x), ys = by(y);
  const top = Math.max(1, ...xs.values(), ...ys.values());
  const W = 720, H = 220, L = 44, R = 16, T = 16, B = 28;
  const px = i => L + (W - L - R) * (years.length === 1 ? 0.5 : i / (years.length - 1));
  const py = v => T + (H - T - B) * (1 - v / top);
  const line = (m, cls, dash) => {
    const pts = years.map((yr, i) => m.has(yr) ? [px(i), py(m.get(yr)), yr, m.get(yr)] : null);
    let path = '', open = false;
    pts.forEach(p => { if (!p){ open = false; return; } path += `${open ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`; open = true; });
    const dots = pts.filter(Boolean).map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4"><title>${p[2]}: ${p[3].toLocaleString('en-US')} ${metric}</title></circle>`).join('');
    return `<g class="al-cmp-line ${cls}${dash ? ' dash' : ''}"><path d="${path}"/>${dots}</g>`;
  };
  const grid = [0, 0.5, 1].map(f => `<line class="al-grid" x1="${L}" x2="${W - R}" y1="${py(top * f).toFixed(1)}" y2="${py(top * f).toFixed(1)}"/>
    <text class="al-ax" x="${L - 6}" y="${(py(top * f) + 3).toFixed(1)}">${Math.round(top * f).toLocaleString('en-US')}</text>`).join('');
  const labels = years.map((yr, i) => `<text class="al-yr" x="${px(i).toFixed(1)}" y="${H - 10}">’${String(yr % 100).padStart(2, '0')}</text>`).join('');
  const same = x.key && x.key === y.key;
  return `<figure class="al-chart al-cmp-chart">
    <div class="al-switch al-cmp-metric" role="group" aria-label="Show">
      <button type="button" data-m="runs" aria-pressed="${metric === 'runs'}">Runs</button>
      <button type="button" data-m="wickets" aria-pressed="${metric === 'wickets'}">Wickets</button></div>
    <div class="al-chart-scroll"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${metric} each season, both players">
      ${grid}${labels}${line(xs, x.key ? 'crest-' + x.key : 'gold', false)}${line(ys, y.key ? 'crest-' + y.key : 'ink', same)}</svg></div>
    <figcaption><span class="al-cmp-key ${x.key ? 'crest-' + x.key : ''}"></span>${cmpEsc(x.name)}
      <span class="al-cmp-key ${y.key ? 'crest-' + y.key : ''}${same ? ' dash' : ''}"></span>${cmpEsc(y.name)} · ${metric} each season</figcaption></figure>`;
}

// ------------------------------------------------------------------ when they met

async function cmpMet(x, y){
  const el = document.getElementById('cmpMet');
  const src = document.getElementById('cmpPick').dataset.h2h;
  if (!el || !src) return;
  try {
    if (!CMP.h2h){
      const doc = await (await fetch(src)).json();
      const byPath = new Map(doc.people.map((p, i) => [p[1], i]));
      CMP.h2h = {doc, byPath};
    }
  } catch (e){ return; }
  const i = CMP.h2h.byPath.get(x.path), j = CMP.h2h.byPath.get(y.path);
  if (i === undefined || j === undefined){ el.innerHTML = ''; return; }
  const pair = (bt, bw) => CMP.h2h.doc.pairs.find(p => p[0] === bt && p[1] === bw);
  const lines = [[pair(i, j), x, y], [pair(j, i), y, x]].filter(([p]) => p).map(([p, bat, bowl]) => {
    const [, , balls, runs, outs] = p;
    return `<div class="al-cmp-met-row"><b>${cmpEsc(bat.name)}</b> facing <b>${cmpEsc(bowl.name)}</b>
      <span><strong>${runs}</strong> off ${balls} balls · strike rate ${(100 * runs / balls).toFixed(0)} · out ${outs === 0 ? 'never' : outs === 1 ? 'once' : outs + ' times'}</span></div>`;
  });
  el.innerHTML = lines.length ? `<div class="al-cmp-group">When they met</div><div class="al-cmp-met">${lines.join('')}</div>` : '';
}

function cmpCopy(){
  if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(() => { if (typeof slip === 'function') slip('Link copied'); }, () => {});
}

document.addEventListener('DOMContentLoaded', async () => {
  try { await cmpLoad(); } catch (e){ return; }      // the written comparison stays on the page
  const q = new URLSearchParams(location.search);
  const ok = s => s && CMP.doc.players[s];
  CMP.a = ok(q.get('a')) ? q.get('a') : 'virat-kohli';
  CMP.b = ok(q.get('b')) ? q.get('b') : 'rohit-sharma';
  if (CMP.a === CMP.b) CMP.b = CMP.a === 'rohit-sharma' ? 'virat-kohli' : 'rohit-sharma';
  ['cmpA', 'cmpB'].forEach(id => document.getElementById(id).addEventListener('change', cmpPick));
  cmpDefaultMetric();
  cmpDraw();
});
