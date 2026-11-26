// [A322] The stats explorer. Every IPL ball since 2008 arrives once as columns of small
// integers (explorer.bin.gz, ~0.5 MB) and is added up here, so any question -- a figure,
// filtered and grouped any way -- is answered in the browser without asking a server.
// `exAggregate` must agree with tools/snapshot_explorer.py's `aggregate` ball for ball; a test
// runs both on the real data. The question lives in the address, so an answer can be sent.

const EX = {idx: null, c: null, q: null, sort: null, desc: true, ready: false};

const EX_GROUPS = [['player', 'Player'], ['season', 'Season'], ['team', 'Team'], ['opponent', 'Opponent'],
  ['ground', 'Ground'], ['phase', 'Phase'], ['position', 'Batting position'], ['style', 'Bowling type'],
  ['innings', 'Innings'], ['stage', 'Stage']];
const EX_PHASE = ['Unknown', 'Powerplay', 'Middle overs', 'Death overs'];
const EX_STYLE = ['Unknown', 'Pace', 'Spin'];
const EX_STAGE = ['League', 'Playoffs', 'Final'];
const EX_NOT_OUT = ['retired hurt'];
const EX_NOT_BOWLERS = ['run out', 'retired hurt', 'retired out', 'obstructing the field'];
const EX_SHOWN = 50;

// ------------------------------------------------------------------ loading

async function exLoad(box){
  const idx = await (await fetch(box.dataset.index)).json();
  // The file is gzip on disk. A host that sends it with Content-Encoding: gzip hands the page the
  // bytes already opened, so the gzip header (1f 8b) is checked rather than assumed.
  const raw = await (await fetch(box.dataset.data)).arrayBuffer();
  const head = new Uint8Array(raw, 0, 2);
  const buf = head[0] === 0x1f && head[1] === 0x8b
    ? await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()
    : raw;
  const c = {};
  for (const [name, kind, off] of idx.cols)
    c[name] = kind === 'u8' ? new Uint8Array(buf, off, idx.n) : new Uint16Array(buf, off, idx.n);
  EX.idx = idx; EX.c = c; EX.ready = true;
}

// ------------------------------------------------------------------ the aggregate (mirrors the Python)

function exAggregate(idx, c, q){
  const bat = (q.mode || 'bat') === 'bat', by = q.by || 'player';
  const credited = idx.kinds.map(k => !EX_NOT_BOWLERS.includes(k));
  const dismissal = idx.kinds.map(k => !EX_NOT_OUT.includes(k));
  const people = idx.people;
  const out = new Map();
  const acc = key => {
    let a = out.get(key);
    if (!a){
      a = {inns: new Set(), runs: 0, balls: 0, outs: 0, fours: 0, sixes: 0, dots: 0, conceded: 0, wkts: 0,
           gone: new Set(), wkInn: new Map(), runInn: new Map()};
      out.set(key, a);
    }
    return a;
  };
  const has = v => v !== undefined && v !== null && v !== '';
  const {striker: st, nonstriker: ns, bowler: bw, rb, wd, nb, wk, out: who, phase: ph} = c;
  idx.innings.forEach(([mi, innNo, bt, bl, start, count], ii) => {
    const [year, , ground, stage] = idx.matches[mi];
    if ((q.from && year < q.from) || (q.to && year > q.to)) return;
    const team = bat ? bt : bl, opp = bat ? bl : bt;
    if (has(q.team) && team !== q.team) return;
    if (has(q.opp) && opp !== q.opp) return;
    if (has(q.ground) && ground !== q.ground) return;
    if (q.inn && innNo !== q.inn) return;
    if (has(q.stage) && stage !== q.stage) return;
    const pos = new Map();
    for (let i = start; i < start + count; i++){
      if (!pos.has(st[i])) pos.set(st[i], pos.size + 1);
      if (!pos.has(ns[i])) pos.set(ns[i], pos.size + 1);
      if (q.phase && ph[i] !== q.phase) continue;
      const style = people[bw[i]][2];
      if (q.style && style !== q.style) continue;
      const key = person => {
        switch (by){
          case 'player': return person;
          case 'season': return year;
          case 'team': return team;
          case 'opponent': return opp;
          case 'ground': return ground;
          case 'phase': return ph[i];
          case 'position': return pos.get(bat ? person : st[i]);
          case 'style': return style;
          case 'innings': return innNo;
          default: return stage;
        }
      };
      if (bat){
        for (const [person, striker] of [[st[i], true], [ns[i], false]]){
          if (has(q.player) && person !== q.player) continue;
          if (q.pos && pos.get(person) !== q.pos) continue;
          const a = acc(key(person)), ik = ii * 65536 + person;
          a.inns.add(ik);
          if (striker){
            a.runs += rb[i];
            a.runInn.set(ik, (a.runInn.get(ik) || 0) + rb[i]);
            if (wd[i] === 0){ a.balls++; if (rb[i] === 0) a.dots++; }
            if (rb[i] === 4) a.fours++;
            if (rb[i] === 6) a.sixes++;
          }
          if (wk[i] && dismissal[wk[i] - 1] && who[i] === (striker ? 1 : 2)){ a.outs++; a.gone.add(ik); }
        }
      } else {
        if (has(q.player) && bw[i] !== q.player) continue;
        if (q.pos && pos.get(st[i]) !== q.pos) continue;
        const a = acc(key(bw[i])), ik = ii * 65536 + bw[i];
        a.inns.add(ik);
        const legal = wd[i] === 0 && nb[i] === 0, r = rb[i] + wd[i] + nb[i];
        if (legal){ a.balls++; if (rb[i] === 0) a.dots++; }
        a.conceded += r;
        a.runInn.set(ik, (a.runInn.get(ik) || 0) + r);
        if (rb[i] === 4) a.fours++;
        if (rb[i] === 6) a.sixes++;
        if (wk[i] && credited[wk[i] - 1]){ a.wkts++; a.wkInn.set(ik, (a.wkInn.get(ik) || 0) + 1); }
      }
    }
  });
  const res = {};
  for (const [k, a] of out) res[k] = exFinish(a, bat);
  return res;
}

function exFinish(a, bat){
  if (bat){
    let fifties = 0, hundreds = 0, hs = 0, hsNot = false;
    for (const [k, r] of a.runInn){
      if (r >= 100) hundreds++; else if (r >= 50) fifties++;
      const notOut = !a.gone.has(k);
      if (r > hs || (r === hs && notOut && !hsNot)){ hs = r; hsNot = notOut; }
    }
    return {inns: a.inns.size, runs: a.runs, balls: a.balls, outs: a.outs, fours: a.fours, sixes: a.sixes,
            dots: a.dots, fifties, hundreds, hs, hs_not: hsNot && hs > 0};
  }
  let w4 = 0, bw = 0, br = 0, any = false;
  for (const [k, r] of a.runInn){
    const w = a.wkInn.get(k) || 0;
    if (w >= 4) w4++;
    if (!any || w > bw || (w === bw && r < br)){ bw = w; br = r; any = true; }
  }
  return {inns: a.inns.size, balls: a.balls, runs: a.conceded, wkts: a.wkts, dots: a.dots, fours: a.fours,
          sixes: a.sixes, w4, best_w: bw, best_r: br};
}

// ------------------------------------------------------------------ columns of the answer

const EX_BAT_COLS = [
  ['inns', 'Inns', r => r.inns, 0],
  ['runs', 'Runs', r => r.runs, 0],
  ['balls', 'Balls', r => r.balls, 0],
  ['sr', 'SR', r => r.balls ? 100 * r.runs / r.balls : null, 1],
  ['avg', 'Avg', r => r.outs ? r.runs / r.outs : null, 2],
  ['outs', 'Outs', r => r.outs, 0],
  ['fours', '4s', r => r.fours, 0],
  ['sixes', '6s', r => r.sixes, 0],
  ['dotp', 'Dot %', r => r.balls ? 100 * r.dots / r.balls : null, 1],
  ['bdry', 'Bdry %', r => r.runs ? 100 * (4 * r.fours + 6 * r.sixes) / r.runs : null, 1],
  ['fifties', '50s', r => r.fifties, 0],
  ['hundreds', '100s', r => r.hundreds, 0],
  ['hs', 'HS', r => r.hs, 0],
];
const EX_BOWL_COLS = [
  ['inns', 'Inns', r => r.inns, 0],
  ['overs', 'Overs', r => r.balls, 'overs'],
  ['runs', 'Runs', r => r.runs, 0],
  ['wkts', 'Wkts', r => r.wkts, 0],
  ['econ', 'Econ', r => r.balls ? 6 * r.runs / r.balls : null, 2],
  ['avg', 'Avg', r => r.wkts ? r.runs / r.wkts : null, 2],
  ['sr', 'SR', r => r.wkts ? r.balls / r.wkts : null, 1],
  ['dotp', 'Dot %', r => r.balls ? 100 * r.dots / r.balls : null, 1],
  ['sixes', '6s', r => r.sixes, 0],
  ['w4', '4w+', r => r.w4, 0],
  ['best', 'Best', r => r.best_w * 1000 - r.best_r, 'best'],
];
// Lower is better for a bowler's economy, average and strike rate, so they sort ascending first.
function exLowFirst(bat, key){
  return !bat && ['econ', 'avg', 'sr'].includes(key);
}

function exCell(col, r){
  const [key, , f, places] = col;
  const v = f(r);
  if (v === null || v === undefined) return '–';
  if (places === 'overs') return `${Math.floor(v / 6)}.${v % 6}`;
  if (places === 'best') return `${r.best_w}/${r.best_r}`;
  if (key === 'hs') return `${r.hs}${r.hs_not ? '*' : ''}`;
  return places ? v.toFixed(places) : v.toLocaleString('en-US');
}

function exLabel(by, k){
  const idx = EX.idx;
  switch (by){
    case 'player': {
      const [name, slug] = idx.people[k];
      return slug ? `<a href="/almanack/players/${slug}">${exEsc(name)}</a>` : exEsc(name);
    }
    case 'team': case 'opponent': return exEsc(idx.teams[k]);
    case 'ground': return `<a href="/almanack/grounds/${idx.grounds[k][1]}">${exEsc(idx.grounds[k][0])}</a>`;
    case 'phase': return EX_PHASE[k];
    case 'position': return `No. ${k}`;
    case 'style': return EX_STYLE[k];
    case 'innings': return k === 1 ? 'Batting first' : 'Batting second';
    case 'stage': return EX_STAGE[k];
    default: return String(k);
  }
}

function exEsc(s){ return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }

// ------------------------------------------------------------------ the form and the address

const EX_FIELDS = ['mode', 'by', 'from', 'to', 'team', 'opp', 'ground', 'phase', 'pos', 'style', 'inn', 'stage', 'player', 'min', 'sort'];

function exReadForm(){
  const v = id => document.getElementById(id).value;
  const num = id => v(id) === '' ? null : Number(v(id));
  const q = {mode: document.querySelector('.ex-mode button[aria-pressed=true]').dataset.mode, by: v('exBy'),
             from: num('exFrom'), to: num('exTo'), team: num('exTeam'), opp: num('exOpp'), ground: num('exGround'),
             phase: num('exPhase'), pos: num('exPos'), style: num('exStyle'), inn: num('exInn'), stage: num('exStage'),
             min: num('exMin'), sort: EX.sort};
  const who = document.getElementById('exPlayer').value.trim().toLowerCase();
  q.player = who ? EX.idx.people.findIndex(p => p[0].toLowerCase() === who) : null;
  if (q.player === -1) q.player = null;
  return q;
}

function exWriteForm(q){
  const set = (id, val) => { document.getElementById(id).value = val === null || val === undefined ? '' : val; };
  document.querySelectorAll('.ex-mode button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === q.mode)));
  set('exBy', q.by); set('exFrom', q.from); set('exTo', q.to); set('exTeam', q.team); set('exOpp', q.opp);
  set('exGround', q.ground); set('exPhase', q.phase); set('exPos', q.pos); set('exStyle', q.style);
  set('exInn', q.inn); set('exStage', q.stage); set('exMin', q.min);
  set('exPlayer', q.player !== null && q.player !== undefined ? EX.idx.people[q.player][0] : '');
  document.getElementById('exStyleLabel').textContent = q.mode === 'bowl' ? 'Bowler type' : 'Against';
  document.getElementById('exTeamLabel').textContent = q.mode === 'bowl' ? 'Bowling for' : 'Batting for';
}

// A player, team or ground goes into the address by name (its page slug), never by its place in
// the file's lists, which a rebuilt file can shift: a link sent today must ask the same question
// after the next season is added.
function exSlug(s){ return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
const EX_BY_NAME = {
  player: [() => EX.idx.people, p => p[1]],
  team: [() => EX.idx.teams, t => exSlug(t)],
  opp: [() => EX.idx.teams, t => exSlug(t)],
  ground: [() => EX.idx.grounds, g => g[1]],
};

function exToUrl(q){
  const p = new URLSearchParams();
  EX_FIELDS.forEach(f => {
    if (q[f] === null || q[f] === undefined || q[f] === '') return;
    p.set(f, EX_BY_NAME[f] ? EX_BY_NAME[f][1](EX_BY_NAME[f][0]()[q[f]]) : q[f]);
  });
  if (!EX.desc) p.set('asc', '1');
  history.replaceState(null, '', '?' + p.toString());
}

function exFromUrl(){
  const p = new URLSearchParams(location.search);
  const q = {mode: p.get('mode') || 'bat', by: p.get('by') || 'player'};
  ['from', 'to', 'phase', 'pos', 'style', 'inn', 'stage', 'min']
    .forEach(f => { q[f] = p.has(f) ? Number(p.get(f)) : null; });
  for (const [f, [list, slugOf]] of Object.entries(EX_BY_NAME)){
    const at = p.has(f) ? list().findIndex(x => slugOf(x) === p.get(f)) : -1;
    q[f] = at >= 0 ? at : null;
  }
  q.sort = p.get('sort');
  EX.desc = !p.has('asc');
  return q;
}

// ------------------------------------------------------------------ answering

function exRun(fromForm = true){
  if (!EX.ready) return;
  const q = fromForm ? exReadForm() : EX.q;
  const bat = q.mode !== 'bowl';
  const cols = bat ? EX_BAT_COLS : EX_BOWL_COLS;
  if (!q.sort || !cols.some(c => c[0] === q.sort)){ q.sort = bat ? 'runs' : 'wkts'; EX.desc = true; }
  EX.q = q; EX.sort = q.sort;
  exWriteForm(q);
  exToUrl(q);
  const t0 = performance.now();
  const res = exAggregate(EX.idx, EX.c, q);
  const ms = performance.now() - t0;
  const min = q.min ?? (q.by === 'player' ? 60 : 0);
  const col = cols.find(c => c[0] === q.sort);
  let rows = Object.entries(res).filter(([, r]) => r.balls >= min);
  const val = r => { const v = col[2](r); return v === null || v === undefined ? null : v; };
  rows.sort((a, b) => {
    const x = val(a[1]), y = val(b[1]);
    if (x === null) return 1;
    if (y === null) return -1;
    return EX.desc ? y - x : x - y;
  });
  const total = rows.length, balls = Object.values(res).reduce((t, r) => t + r.balls, 0);
  if (q.by === 'season' || q.by === 'position' || q.by === 'phase' || q.by === 'innings' || q.by === 'stage')
    rows.sort((a, b) => Number(a[0]) - Number(b[0]));
  rows = rows.slice(0, EX_SHOWN);
  // A bar is a row's share of the best figure shown: the largest when sorting high to low,
  // the smallest when sorting low to high (an economy), so the leader's bar is always full.
  const shownVals = rows.map(([, r]) => val(r)).filter(v => v !== null && v > 0);
  const top = Math.max(1e-9, ...shownVals), low = Math.min(...shownVals);
  const barOf = r => { const v = val(r); if (v === null || v <= 0) return 0; return EX.desc ? 100 * v / top : 100 * low / v; };
  const head = `<tr><th></th><th>${exEsc(EX_GROUPS.find(g => g[0] === q.by)[1])}</th>` + cols.map(c =>
    `<th class="n ex-th${c[0] === q.sort ? ' on' : ''}" data-k="${c[0]}" aria-sort="${c[0] === q.sort ? (EX.desc ? 'descending' : 'ascending') : 'none'}">${c[1]}</th>`).join('') + '</tr>';
  // On a phone the table scrolls sideways, so the figure being sorted on also rides beside the name.
  const body = rows.map(([k, r], i) => `<tr><td class="ex-rank">${i + 1}</td><td class="ex-name"><b class="ex-key">${exCell(col, r)}<small>${col[1]}</small></b>${exLabel(q.by, Number(k))}
    <span class="ex-bar"><i style="width:${barOf(r).toFixed(1)}%"></i></span></td>` +
    cols.map(c => `<td class="n${c[0] === q.sort ? ' on' : ''}">${exCell(c, r)}</td>`).join('') + '</tr>').join('');
  document.getElementById('exCount').innerHTML = total
    ? `<b>${total.toLocaleString('en-US')}</b> ${total === 1 ? 'row' : 'rows'} from <b>${balls.toLocaleString('en-US')}</b> balls`
      + `${total > EX_SHOWN ? `, the top ${EX_SHOWN} shown` : ''}${q.by === 'player' && min ? ` · ${min}+ balls` : ''} · ${ms.toFixed(0)} ms`
    : 'Nothing in the archive matches that. Loosen a filter.';
  document.getElementById('exOut').innerHTML = total
    ? `<div class="hx-scroll"><table class="hx-table ex-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>` : '';
  document.querySelectorAll('.ex-th').forEach(th => th.addEventListener('click', () => {
    if (EX.sort === th.dataset.k) EX.desc = !EX.desc;
    else { EX.sort = th.dataset.k; EX.desc = !exLowFirst(bat, th.dataset.k); }
    exRun();
  }));
}

function exPreset(btn){
  const q = JSON.parse(btn.dataset.q);
  const named = n => EX.idx.people.findIndex(p => p[0] === n);
  if (q.playerName){ q.player = named(q.playerName); delete q.playerName; }
  if (q.groundName){ q.ground = EX.idx.grounds.findIndex(g => g[0] === q.groundName); delete q.groundName; }
  for (const f of EX_FIELDS) if (!(f in q)) q[f] = null;
  EX.sort = q.sort || null;
  EX.desc = !q.asc;
  exWriteForm(q);
  exRun();
  document.getElementById('exCount').scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function exFill(){
  const idx = EX.idx;
  const opts = (id, items, any) => { document.getElementById(id).innerHTML = `<option value="">${any}</option>` +
    items.map(([v, t]) => `<option value="${v}">${exEsc(t)}</option>`).join(''); };
  const years = [...new Set(idx.matches.map(m => m[0]))].sort();
  opts('exFrom', years.map(y => [y, y]), 'First season');
  opts('exTo', years.map(y => [y, y]), 'Last season');
  opts('exTeam', idx.teams.map((t, i) => [i, t]), 'Any team');
  opts('exOpp', idx.teams.map((t, i) => [i, t]), 'Any opponent');
  opts('exGround', idx.grounds.map((g, i) => [i, g[0]]).sort((a, b) => a[1].localeCompare(b[1])), 'Any ground');
  document.getElementById('exPlayers').innerHTML = idx.people.filter(p => p[1]).map(p => `<option value="${exEsc(p[0])}">`).join('');
}

document.addEventListener('DOMContentLoaded', async () => {
  const box = document.getElementById('exBox');
  if (!box) return;
  const status = document.getElementById('exCount');
  if (typeof DecompressionStream === 'undefined'){
    status.textContent = 'This browser cannot open the archive file; the worked examples below still read in full.';
    return;
  }
  status.textContent = 'Loading every ball since 2008…';
  try { await exLoad(box); } catch (e){ status.textContent = 'Could not load the archive. Try again in a moment.'; return; }
  box.classList.add('ready');
  exFill();
  EX.q = exFromUrl();
  EX.sort = EX.q.sort;
  exWriteForm(EX.q);
  exRun();
  document.querySelectorAll('.ex-mode button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.ex-mode button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    EX.sort = null; exRun();
  }));
  box.querySelectorAll('select, input').forEach(el => el.addEventListener('change', () => exRun()));
  document.querySelectorAll('.ex-preset').forEach(b => b.addEventListener('click', () => exPreset(b)));
});
