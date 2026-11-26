// [A315] The Almanack's player index: a plain HTML table, filtered as you type. The page
// works without this script (every row is there); it only hides rows that do not match.
function alFold(s) {
  return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function alFilter(q) {
  const want = alFold(q.trim());
  const rows = document.querySelectorAll('.al-index tbody tr');
  let shown = 0;
  rows.forEach(tr => {
    const hit = !want || alFold(tr.cells[0].textContent).includes(want);
    tr.hidden = !hit;
    if (hit) shown++;
  });
  const note = document.getElementById('alCount');
  if (note) note.textContent = want ? `${shown} of ${rows.length} players` : '';
}

// [A316] Head to head: one static file of every pair that met for six balls or more, loaded
// once; the page itself carries the rivalries as plain HTML for readers without the script.
let H2H = null;

async function h2hLoad() {
  const box = document.querySelector('.al-h2h');
  if (!box || H2H) return H2H;
  const res = await fetch(box.dataset.src);
  const doc = await res.json();
  const bats = new Set(), bowls = new Set();
  doc.pairs.forEach(([bt, bw]) => { bats.add(bt); bowls.add(bw); });
  const opts = ids => [...ids].map(i => doc.people[i][0]).sort()
    .map(n => `<option value="${n.replace(/"/g, '&quot;')}">`).join('');
  document.getElementById('h2hBats').innerHTML = opts(bats);
  document.getElementById('h2hBowls').innerHTML = opts(bowls);
  const byName = new Map(doc.people.map((p, i) => [alFold(p[0]), i]));
  H2H = { doc, byName };
  return H2H;
}

function h2hName(i) {
  const [name, path] = H2H.doc.people[i];
  const safe = name.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  return path ? `<a href="${path}">${safe}</a>` : safe;
}

function h2hRows(pairs, other) {
  const rows = pairs.map(([bt, bw, b, r, o]) =>
    `<tr><td>${h2hName(other === 'bowler' ? bw : bt)}</td><td class=n><b>${b}</b></td><td class=n>${r}</td>` +
    `<td class=n>${(100 * r / b).toFixed(1)}</td><td class=n>${o}</td></tr>`).join('');
  return `<div class="hx-scroll"><table class="hx-table"><thead><tr><th>${other === 'bowler' ? 'Bowler' : 'Batter'}</th>` +
    `<th class=n>Balls</th><th class=n>Runs</th><th class=n>SR</th><th class=n>Out</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function h2hFind() {
  const out = document.getElementById('h2hOut');
  try { await h2hLoad(); } catch (e) { out.textContent = 'Could not load the head-to-head records.'; return; }
  const bat = H2H.byName.get(alFold(document.getElementById('h2hBat').value.trim()));
  const bowl = H2H.byName.get(alFold(document.getElementById('h2hBowl').value.trim()));
  const pairs = H2H.doc.pairs;
  if (bat !== undefined && bowl !== undefined) {
    const p = pairs.find(x => x[0] === bat && x[1] === bowl);
    out.innerHTML = p ? h2hRows([p], 'bowler')
      : '<p class="hx-note">These two met for fewer than six balls, or never.</p>';
  } else if (bat !== undefined) {
    const mine = pairs.filter(x => x[0] === bat).sort((x, y) => y[2] - x[2]).slice(0, 15);
    out.innerHTML = `<h3>${h2hName(bat)} against the bowlers faced most</h3>` + h2hRows(mine, 'bowler');
  } else if (bowl !== undefined) {
    const mine = pairs.filter(x => x[1] === bowl).sort((x, y) => y[2] - x[2]).slice(0, 15);
    out.innerHTML = `<h3>${h2hName(bowl)} against the batters bowled to most</h3>` + h2hRows(mine, 'batter');
  } else {
    out.innerHTML = '';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.querySelector('.al-h2h')) h2hLoad().catch(() => {});
});

// ---------------------------------------------------------------------------------------
// [A318] The facelift. Every picture and number is already in the page; this only animates
// it, sorts tables, folds long ones, switches tabs and finds a player. With no script the
// page reads in full, which is the point (the Almanack is written for readers and crawlers).
document.documentElement.classList.add('al-js');
const AL_CALM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Count a figure up from nothing the first time it is seen. "9,336", "175*", "6/12",
// "₹27 cr" all keep everything but the leading number as it was printed.
function alCount(el){
  const text = el.textContent;
  const m = text.match(/^([^\d]*)([\d,]+)(.*)$/s);
  if (!m || AL_CALM) return;
  const to = Number(m[2].replace(/,/g, ''));
  if (!to || to < 10) return;
  const commas = m[2].includes(',') || to >= 10000;
  const t0 = performance.now(), dur = Math.min(1400, 500 + to / 8);
  const tick = now => {
    const f = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - f, 3);
    const v = Math.round(to * e);
    el.textContent = m[1] + (commas ? v.toLocaleString('en-US') : v) + m[3];
    if (f < 1) requestAnimationFrame(tick); else el.textContent = text;
  };
  requestAnimationFrame(tick);
  // A tab hidden mid-count stops its animation frames; the printed figure always comes back.
  setTimeout(() => { el.textContent = text; }, dur + 400);
}

function alReveal(){
  const targets = document.querySelectorAll('.al-chart, .al-par, .al-board, .al-meter, .al-donut, .al-num, .al-tile b, .al-spot-tiles b');
  document.querySelectorAll('.al-career').forEach(svg =>
    svg.querySelectorAll('.al-series').forEach(g => g.querySelectorAll('.al-b').forEach((b, i) => b.style.setProperty('--i', i))));
  const show = el => {
    if (el.classList.contains('in')) return;
    el.classList.add('in');
    if (el.matches('.al-num, .al-tile b, .al-spot-tiles b')) alCount(el);
  };
  if (!('IntersectionObserver' in window)){ targets.forEach(show); return; }
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting){ show(e.target); io.unobserve(e.target); }
  }), {threshold: 0.25});
  targets.forEach(t => io.observe(t));
}

// Runs or wickets on a career chart.
function alSwitch(){
  document.querySelectorAll('.al-chart .al-switch').forEach(sw => {
    const fig = sw.closest('.al-chart');
    sw.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b) return;
      sw.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      fig.querySelectorAll('.al-series').forEach(g => { g.style.display = g.dataset.metric === b.dataset.metric ? '' : 'none'; });
      fig.classList.remove('in'); void fig.offsetWidth; fig.classList.add('in');
    });
  });
}

// Click a column heading to sort. A "Career" total row stays at the bottom, and a figure is
// read as a number wherever it starts with one ("104*", "3/18", "₹2.4 cr", "1,082").
function alCellValue(td){
  const t = td.textContent.trim().replace(/^[^\d−-]*(?=[\d−-])/, '');
  const m = t.replace(/,/g, '').replace('−', '-').match(/^-?\d+(\.\d+)?/);
  return m ? Number(m[0]) : null;
}

function alSortable(){
  document.querySelectorAll('.al-page .hx-table').forEach(table => {
    const head = table.tHead && table.tHead.rows[0];
    const body = table.tBodies[0];
    if (!head || !body || body.rows.length < 4) return;
    [...head.cells].forEach((th, col) => {
      th.classList.add('al-sort');
      th.tabIndex = 0;
      const go = () => {
        const dir = th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
        [...head.cells].forEach(x => x.removeAttribute('aria-sort'));
        th.setAttribute('aria-sort', dir);
        const rows = [...body.rows];
        const pinned = rows.filter(r => /^Career/.test(r.cells[0].textContent.trim()));
        const rest = rows.filter(r => !pinned.includes(r));
        const nums = rest.every(r => !r.cells[col] || alCellValue(r.cells[col]) !== null || r.cells[col].textContent.trim() === '-');
        const key = r => {
          const c = r.cells[col];
          if (!c) return null;
          return nums ? alCellValue(c) : c.textContent.trim().toLowerCase();
        };
        rest.sort((a, b) => {
          const x = key(a), y = key(b);
          if (x === null && y === null) return 0;
          if (x === null) return 1;
          if (y === null) return -1;
          const s = x < y ? -1 : x > y ? 1 : 0;
          return dir === 'ascending' ? s : -s;
        });
        rest.concat(pinned).forEach(r => body.appendChild(r));
        alFoldApply(table);
      };
      th.addEventListener('click', go);
      th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); go(); } });
    });
  });
}

// A long table shows its first rows and a button for the rest. The player index is left
// whole: its filter is the way through it.
const AL_FOLD_AT = 25, AL_FOLD_SHOW = 15;
function alFoldApply(table){
  if (!table.dataset.folded) return;
  const open = table.dataset.folded === 'open';
  [...table.tBodies[0].rows].forEach((r, i) => { r.hidden = !open && i >= AL_FOLD_SHOW; });
}

function alFoldTables(){
  document.querySelectorAll('.al-page .hx-table').forEach(table => {
    if (table.classList.contains('al-index')) return;
    const n = table.tBodies[0] ? table.tBodies[0].rows.length : 0;
    if (n < AL_FOLD_AT) return;
    table.dataset.folded = 'shut';
    alFoldApply(table);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'al-fold-btn';
    btn.textContent = `Show all ${n} rows`;
    btn.addEventListener('click', () => {
      const open = table.dataset.folded !== 'open';
      table.dataset.folded = open ? 'open' : 'shut';
      btn.textContent = open ? 'Show fewer' : `Show all ${n} rows`;
      alFoldApply(table);
    });
    table.closest('.hx-scroll').after(btn);
  });
}

// The records page: its four sections as tabs.
function alTabs(){
  document.querySelectorAll('.al-tabbed').forEach(box => {
    const secs = [...box.querySelectorAll(':scope > .hx-sec')];
    if (secs.length < 2) return;
    const bar = document.createElement('div');
    bar.className = 'al-tabbar';
    bar.setAttribute('role', 'tablist');
    const pick = i => {
      secs.forEach((s, j) => { s.hidden = i !== j; });
      [...bar.children].forEach((b, j) => b.setAttribute('aria-selected', String(i === j)));
    };
    secs.forEach((s, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.textContent = s.querySelector('h2').textContent;
      b.addEventListener('click', () => { pick(i); bar.scrollIntoView({block: 'nearest'}); });
      bar.appendChild(b);
    });
    box.prepend(bar);
    pick(0);
  });
}

// The front page: find a player, and the player of the day.
let AL_PLAYERS = null;
function alPlayers(){
  if (AL_PLAYERS) return AL_PLAYERS;
  const el = document.getElementById('alPlayers');
  AL_PLAYERS = el ? JSON.parse(el.textContent) : [];
  return AL_PLAYERS;
}

function alGo(){
  const input = document.getElementById('alFind');
  if (!input) return;
  const want = alFold(input.value.trim());
  if (!want) return;
  const all = alPlayers();
  const hit = all.find(p => alFold(p[0]) === want) || all.find(p => alFold(p[0]).includes(want));
  if (hit) location.href = hit[1];
}

function alFinder(){
  const input = document.getElementById('alFind');
  if (!input) return;
  document.getElementById('alFindList').innerHTML =
    alPlayers().map(p => `<option value="${p[0].replace(/"/g, '&quot;')}">`).join('');
  input.addEventListener('keydown', e => { if (e.key === 'Enter') alGo(); });
  input.addEventListener('change', () => {
    const want = alFold(input.value.trim());
    if (alPlayers().some(p => alFold(p[0]) === want)) alGo();
  });
}

function alSpotlight(){
  const box = document.getElementById('alSpot');
  if (!box) return;
  const pool = alPlayers().filter(p => p.length > 2 && p[7] >= 85);
  if (!pool.length) return;
  const day = Math.floor(Date.now() / 86400000);
  const p = pool[(day * 2654435761 >>> 0) % pool.length];
  const [name, path, crest, runs, wkts, matches, span, best, role, team] = p;
  const safe = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const key = crest ? crest.split('/').pop().split('-')[0] : '';
  box.className = 'al-spot' + (key ? ' crest-' + key : '');
  const lead = role === 'bowler' ? [[wkts, 'wickets'], [runs.toLocaleString('en-US'), 'runs']]
                                 : [[runs.toLocaleString('en-US'), 'runs'], [wkts, 'wickets']];
  box.innerHTML = `<em>Player of the day</em><a class="al-spot-name" href="${path}">${safe(name)}</a>` +
    `<span class="al-spot-meta">${safe(role.charAt(0).toUpperCase() + role.slice(1))} · ${safe(team)} · ${safe(span)}</span>` +
    `<div class="al-spot-tiles"><span><b>${matches}</b>matches</span>` +
    lead.map(([v, k]) => `<span><b>${v}</b>${k}</span>`).join('') +
    `<span><b>${best}</b>best card</span></div>` + (crest ? `<img src="${crest}" alt="" width="120" height="120">` : '');
}

document.addEventListener('DOMContentLoaded', () => {
  alSpotlight();
  alFinder();
  alTabs();
  alSortable();
  alFoldTables();
  alSwitch();
  alReveal();
});
