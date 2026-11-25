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
