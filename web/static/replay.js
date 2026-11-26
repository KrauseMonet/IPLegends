// [A319] Match replays and what-ifs. A real match is fetched once as the game's own result
// shape and played on the shared scoreboard (reveal.js); a what-if is the same two elevens
// put through the match engine with any swaps. Everything is kept in the address bar
// (?m=match, &sw=swaps, &s=seed), so a what-if can be sent to someone and plays the same.

const RP = {year: null, match: null, swaps: [], seed: null, last: null, searchTimer: null};

function rpEsc(s){ return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }

async function rpGet(url){
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Could not load that.');
  return r.json();
}

async function rpPost(url, body){
  const r = await fetch(url, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || 'The engine could not play that.');
  return j;
}

function rpUrl(){
  const q = new URLSearchParams();
  if (RP.match) q.set('m', RP.match.id); else if (RP.year) q.set('y', RP.year);
  if (RP.match && RP.swaps.length)
    q.set('sw', RP.swaps.map(s => [s.fs, s.out, s.in_fs, s.in_person].join('.')).join(','));
  if (RP.match && RP.seed) q.set('s', RP.seed);
  history.replaceState(null, '', '?' + q.toString());
}

// ---------------------------------------------------------------- a season's matches

async function rpYear(y){
  RP.year = y; RP.match = null; RP.swaps = []; RP.seed = null;
  document.querySelectorAll('.al-yr-btn').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.year) === y)));
  $('#rpMatch').classList.add('hide');
  const list = $('#rpList');
  list.classList.remove('hide');
  list.innerHTML = '<p class="hx-note">Loading the season…</p>';
  rpUrl();
  try {
    const d = await rpGet(`/api/almanack/matches?year=${y}`);
    list.innerHTML = d.matches.slice().reverse().map(m => {
      const won = n => m.winner === n ? ' won' : '';
      return `<button type="button" class="al-rp-row${m.stage.startsWith('League') ? '' : ' big'}" onclick="rpOpen('${m.id}')">
        <span class="al-rp-stage">${rpEsc(m.stage)} · ${rpEsc(rpDay(m.day))}</span>
        <span class="al-rp-side${won(m.home)}">${crestImg(m.home_crest, 'al-rp-crest')}<b>${rpEsc(m.home)}</b><em>${rpEsc(m.home_score)}</em></span>
        <span class="al-rp-side${won(m.away)}">${crestImg(m.away_crest, 'al-rp-crest')}<b>${rpEsc(m.away)}</b><em>${rpEsc(m.away_score)}</em></span>
        <span class="al-rp-margin">${rpEsc(rpMargin(m.margin))}</span></button>`;
    }).join('');
  } catch (e){ list.innerHTML = `<p class="hx-note">${rpEsc(e.message)}</p>`; }
}

function rpDay(iso){
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
}

// The engine's margin starts with the winner's short code; the page names the winner itself.
function rpMargin(m){ return (m || '').replace(/^[A-Z]{1,4} \d{4} /, ''); }

// ---------------------------------------------------------------- one match

async function rpOpen(id, swaps = [], seed = null){
  const box = $('#rpMatch');
  box.classList.remove('hide');
  $('#rpList').classList.add('hide');
  box.innerHTML = '<p class="hx-note">Loading the match…</p>';
  try {
    RP.match = await rpGet(`/api/almanack/match/${encodeURIComponent(id)}`);
  } catch (e){ box.innerHTML = `<p class="hx-note">${rpEsc(e.message)}</p>`; return; }
  RP.year = RP.match.year;
  document.querySelectorAll('.al-yr-btn').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.year) === RP.year)));
  RP.swaps = swaps; RP.seed = seed; RP.last = null;
  rpUrl();
  rpRender();
  rpOdds();
  box.scrollIntoView({block: 'start', behavior: 'smooth'});
}

function rpSideOf(fs){ return RP.match.sides.find(s => s.fs === fs); }

function rpTeam(side, score, won){
  const cls = crestClass(side.crest || '');
  return `<div class="al-rp-team ${cls}${won ? ' won' : ''}">${crestImg(side.crest, 'al-rp-tcrest')}
    <b>${rpEsc(side.name)}</b><span class="al-rp-score">${rpEsc(score)}</span></div>`;
}

function rpRender(){
  const m = RP.match, r = m.result;
  const [home, away] = m.sides;
  const winner = r.winner === r.home ? home : r.winner === r.away ? away : null;
  const head = `
    <div class="al-rp-card">
      <div class="al-rp-meta">${rpEsc(m.stage)} · ${rpEsc(rpDay(m.day))}${m.ground ? ' · ' + rpEsc(m.ground) : ''}</div>
      <div class="al-rp-teams">${rpTeam(home, r.home_score + ` (${r.home_innings.overs})`, winner === home)}
        <span class="al-rp-v">v</span>${rpTeam(away, r.away_score + (r.away_innings ? ` (${r.away_innings.overs})` : ''), winner === away)}</div>
      <div class="al-rp-result">${winner ? rpEsc(winner.name) + ' won ' + rpEsc(rpMargin(r.margin)).replace(/^by/, 'by') : rpEsc(r.margin)}</div>
      <div class="al-rp-facts">${m.toss ? `<span>${rpEsc(m.toss)}</span>` : ''}${m.player_of_match ? `<span>Player of the Match: <b>${rpEsc(m.player_of_match)}</b></span>` : ''}</div>
      <div class="actions al-rp-actions">
        <button class="act lead" onclick="rpWatchReal()">▶ Watch it over by over</button>
        <button class="act" onclick="renderScorecard(RP.match.result)">Scorecard</button>
        <button class="act" onclick="rpBack()">All ${m.year} matches</button>
      </div>
      <div class="al-odds" id="rpOddsReal"><span class="hx-note">The engine is playing it a hundred times…</span></div>
    </div>
    <section class="al-wi">
      <h2>What if?</h2>
      <p>Take a player out of either side and bring in any player-season from nineteen years of the IPL. The engine plays the two elevens, each player as the card of that season, the side that batted first batting first again.</p>
      <div class="al-wi-sides">${m.sides.map(rpSideList).join('')}</div>
      <div class="al-wi-swaps" id="rpSwaps"></div>
      <div class="actions al-wi-go">
        <button class="act lead" id="rpPlayBtn" onclick="rpPlay()">Play the what-if</button>
        <button class="act" onclick="rpPlay(true)">Play it again</button>
      </div>
      <div id="rpWhatIf"></div>
      <div class="al-odds" id="rpOddsWhatIf"></div>
      <p class="hx-note">Each player plays as the card of the season of this match (an Impact Player from 2023 is the side's twelfth name), so the engine's match is not the real one, and the odds say how the engine sees the two sides, not how the real match should have gone.${m.sides.some(s => s.filled.length) ? ' Named players with no card that season (they neither batted nor bowled in it) are replaced by the squad\'s best card: ' + rpEsc(m.sides.flatMap(s => s.filled).join(', ')) + '.' : ''}</p>
    </section>`;
  $('#rpMatch').innerHTML = head;
  rpSwapsRender();
}

function rpSideList(side){
  const rows = side.players.map(p => {
    const swap = RP.swaps.find(s => s.fs === side.fs && s.out === p.person);
    return `<li class="${swap ? 'swapped' : ''}">
      <span class="al-wi-name">${rpEsc(p.name)}${p.impact ? ' <span class="impact-tag">IMP</span>' : ''}</span>
      <span class="al-wi-role">${rpEsc(rpRole(p))}</span>
      <span class="al-wi-card">${p.display ?? ''}</span>
      ${swap ? `<span class="al-wi-in">→ ${rpEsc(swap.label)}</span>` : ''}
      <button type="button" class="al-wi-btn" onclick="rpPick(${side.fs}, '${p.person}', this)">${swap ? 'Change' : 'Swap'}</button>
    </li>`;
  }).join('');
  return `<div class="al-wi-side ${crestClass(side.crest || '')}"><h3>${crestImg(side.crest, 'al-rp-crest')} ${rpEsc(side.name)}
    <small>${side.batted_first ? 'batted first' : 'batted second'}</small></h3><ol>${rows}</ol></div>`;
}

function rpRole(p){
  return {batter: 'Bat', bowler: 'Bowl', allrounder: 'All-round', keeper: 'Keeper'}[p.role] || '';
}

// ---------------------------------------------------------------- choosing a swap

function rpPick(fs, out, btn){
  document.querySelectorAll('.al-wi-search').forEach(x => x.remove());
  const li = btn.closest('li');
  const box = document.createElement('div');
  box.className = 'al-wi-search';
  box.innerHTML = `<input class="room-input" placeholder="Bring in… type a name" aria-label="Bring in a player" autocomplete="off">
    <div class="al-wi-results"></div>
    ${RP.swaps.some(s => s.fs === fs && s.out === out) ? '<button type="button" class="al-wi-btn" data-undo>Keep the real player</button>' : ''}`;
  li.after(box);
  const input = box.querySelector('input');
  input.focus();
  input.addEventListener('input', () => {
    clearTimeout(RP.searchTimer);
    RP.searchTimer = setTimeout(() => rpSearch(input.value, box.querySelector('.al-wi-results'), fs, out), 180);
  });
  const undo = box.querySelector('[data-undo]');
  if (undo) undo.addEventListener('click', () => { RP.swaps = RP.swaps.filter(s => !(s.fs === fs && s.out === out)); rpChanged(); });
}

async function rpSearch(q, el, fs, out){
  if (q.trim().length < 2){ el.innerHTML = ''; return; }
  try {
    const d = await rpGet(`/api/almanack/cards?q=${encodeURIComponent(q.trim())}`);
    el.innerHTML = d.cards.length ? d.cards.map(c => {
      const [cfs, person, name, year, team, display] = c;
      const label = `${name} (${team} ${year})`;
      return `<button type="button" data-fs="${cfs}" data-p="${person}" data-label="${rpEsc(label)}">
        <b>${rpEsc(name)}</b><span>${rpEsc(team)} ${year}</span><em>${display ?? ''}</em></button>`;
    }).join('') : '<p class="hx-note">Nobody by that name.</p>';
    el.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      RP.swaps = RP.swaps.filter(s => !(s.fs === fs && s.out === out));
      RP.swaps.push({fs, out, in_fs: Number(b.dataset.fs), in_person: b.dataset.p, label: b.dataset.label});
      rpChanged();
    }));
  } catch (e){ el.innerHTML = `<p class="hx-note">${rpEsc(e.message)}</p>`; }
}

function rpChanged(){
  RP.seed = null; RP.last = null;
  rpUrl();
  rpRender();
  rpOdds();
}

function rpSwapsRender(){
  const el = $('#rpSwaps');
  if (!el) return;
  el.innerHTML = RP.swaps.length
    ? RP.swaps.map((s, i) => {
        const side = rpSideOf(s.fs), p = side.players.find(x => x.person === s.out);
        return `<span class="al-wi-chip">${rpEsc(s.label)} for ${rpEsc(p ? p.name : '?')}
          <button type="button" aria-label="Undo" onclick="RP.swaps.splice(${i},1);rpChanged()">×</button></span>`;
      }).join('')
    : '<span class="hx-note">No swaps yet: the engine will play the two real elevens.</span>';
}

function rpBody(){
  return {id: RP.match.id, swaps: RP.swaps.map(({fs, out, in_fs, in_person}) => ({fs, out, in_fs, in_person}))};
}

// ---------------------------------------------------------------- the odds

function rpOddsHtml(o, label){
  const [home, away] = RP.match.sides;
  const h = Math.round(100 * o.home_wins / o.runs), a = Math.round(100 * o.away_wins / o.runs);
  return `<div class="al-odds-label">${label}</div>
    <div class="al-odds-bar"><i class="${crestClass(home.crest || '')}" style="width:${h}%"></i><i class="${crestClass(away.crest || '')}" style="width:${a}%"></i></div>
    <div class="al-odds-key"><span><b>${h}%</b> ${rpEsc(home.name)}</span><span>${rpEsc(away.name)} <b>${a}%</b></span></div>`;
}

async function rpOdds(){
  const real = $('#rpOddsReal');
  try {
    const o = await rpPost('/api/almanack/odds', {id: RP.match.id, swaps: []});
    if (real) real.innerHTML = rpOddsHtml(o, `The engine played the real elevens ${o.runs} times`);
  } catch (e){ if (real) real.innerHTML = ''; }
  const wi = $('#rpOddsWhatIf');
  if (!wi) return;
  if (!RP.swaps.length){ wi.innerHTML = ''; return; }
  wi.innerHTML = '<span class="hx-note">The engine is playing your what-if a hundred times…</span>';
  try {
    const o = await rpPost('/api/almanack/odds', rpBody());
    wi.innerHTML = rpOddsHtml(o, `With your swaps, over ${o.runs} plays`);
  } catch (e){ wi.innerHTML = `<p class="hx-note">${rpEsc(e.message)}</p>`; }
}

// ---------------------------------------------------------------- watching

function rpWatch(r, labelPrefix, onDone){
  const side = (short, name, crest) => ({short, name: name || short, crest: crest || null, kit: null});
  const home = side(r.home, r.home_name, r.home_crest), away = side(r.away, r.away_name, r.away_crest);
  const steps = [];
  if (r.home_innings) steps.push([r.home_innings, `${labelPrefix} · ${home.name} batting`, null, {bat: home, bowl: away}]);
  if (r.away_innings) steps.push([r.away_innings, `${labelPrefix} · ${away.name} batting`,
    r.home_innings ? {innings: r.home_innings, battingLabel: home.name} : null, {bat: away, bowl: home}]);
  MY_SIDE = null;
  RP.watching = {r, onDone};
  $('#rp').classList.add('hide');
  $('#reveal').classList.remove('hide');
  restoreOverSpeed();
  $('#reveal').scrollIntoView({block: 'start'});
  let i = 0;
  (function next(){
    if (i >= steps.length) return rpWatchDone();
    const [innings, label, prior, matchup] = steps[i++];
    startOverStepper(innings, label, next, prior, matchup);
  })();
}

function rpWatchDone(){
  hideAllRevealScreens();
  $('#reveal').classList.add('hide');
  $('#rp').classList.remove('hide');
  $('#rp').scrollIntoView({block: 'start'});
  const w = RP.watching;
  RP.watching = null;
  if (w){
    renderScorecard(w.r);
    if (w.onDone) w.onDone();
  }
}

function rpSkipMatch(){
  clearOverTimer();
  OVER_STEP = null;
  rpWatchDone();
}

function rpWatchReal(){
  const m = RP.match;
  rpWatch(m.result, `${m.stage} · real`, null);
}

async function rpPlay(again = false){
  const btn = $('#rpPlayBtn');
  const out = $('#rpWhatIf');
  if (again || !RP.seed) RP.seed = again ? Math.floor(Math.random() * 1e9) + 1 : (RP.seed || null);
  btn.disabled = true;
  out.innerHTML = '<p class="hx-note">The engine is playing it…</p>';
  try {
    const body = {...rpBody(), seed: RP.seed};
    const d = await rpPost('/api/almanack/whatif', body);
    RP.seed = d.seed;
    RP.last = d;
    rpUrl();
    out.innerHTML = rpWhatIfHtml(d);
    rpWatch(d.result, `${RP.match.stage} · what if`, () => { out.innerHTML = rpWhatIfHtml(d); });
  } catch (e){
    out.innerHTML = `<p class="hx-note al-wi-err">${rpEsc(e.message)}</p>`;
  } finally { btn.disabled = false; }
}

function rpWhatIfHtml(d){
  const r = d.result;
  const name = s => s === r.home ? r.home_name : s === r.away ? r.away_name : s;
  const real = RP.match.result;
  const flipped = (real.winner && r.winner) && (name(r.winner) !== (real.winner === real.home ? real.home_name : real.away_name));
  return `<div class="al-wi-result${flipped ? ' flipped' : ''}">
    <em>${flipped ? 'The result changes' : 'Same winner'}</em>
    <b>${r.winner ? rpEsc(name(r.winner)) + ' win ' + rpEsc(rpMargin(r.margin)) : 'Tied'}</b>
    <span>${rpEsc(r.home_name)} ${rpEsc(r.home_score)} · ${rpEsc(r.away_name)} ${rpEsc(r.away_score)}</span>
    ${d.swaps.length ? `<small>${d.swaps.map(rpEsc).join(' · ')}</small>` : '<small>The two real elevens</small>'}
    <div class="actions"><button class="act" onclick="rpWatch(RP.last.result, RP.match.stage + ' · what if', null)">▶ Watch it again</button>
      <button class="act" onclick="renderScorecard(RP.last.result)">Scorecard</button>
      <button class="act" onclick="rpShare()">Copy the link</button></div></div>`;
}

function rpShare(){
  const url = location.href;
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(() => slip && slip('Link copied'), () => {});
}

function rpBack(){ rpYear(RP.year); }

// ---------------------------------------------------------------- start

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.al-yr-btn').forEach(b => b.addEventListener('click', () => rpYear(Number(b.dataset.year))));
  const q = new URLSearchParams(location.search);
  if (q.get('m')){
    const swaps = (q.get('sw') || '').split(',').filter(Boolean).map(x => {
      const [fs, out, in_fs, in_person] = x.split('.');
      return {fs: Number(fs), out, in_fs: Number(in_fs), in_person, label: 'a swap'};
    });
    rpOpen(q.get('m'), swaps, q.get('s') ? Number(q.get('s')) : null).then(() => rpLabelSwaps());
  } else {
    const years = [...document.querySelectorAll('.al-yr-btn')].map(b => Number(b.dataset.year));
    rpYear(Number(q.get('y')) || years[years.length - 1]);
  }
});

// A swap read back from a link carries only ids; name it from the card it points at.
async function rpLabelSwaps(){
  if (!RP.swaps.length) return;
  await Promise.all(RP.swaps.map(async s => {
    try {
      const c = await rpGet(`/api/almanack/card/${s.in_fs}/${encodeURIComponent(s.in_person)}`);
      s.label = `${c.name} (${c.team} ${c.year})`;
    } catch (e){ /* an unplayable swap is reported when the what-if is played */ }
  }));
  rpRender();
}
