// Build a Career [A199]: one player through real IPL seasons, debut to retirement.
//
// The server holds no state. Every response carries a signed token -- the career as it
// stands after its last completed season -- and the page sends it back with the next move,
// so a request plays at most one season (a season costs up to ~2 s on a cold server; a
// replay of nineteen would not be playable). The token is kept in this browser so a reload
// picks the career up where it was.

const CR_KEY = 'iplegends_career_v1';
const CR_JSON = {'Content-Type': 'application/json'};
let CR = {options: null, data: null, busy: false, seen: 0};

const crStore = () => { try { return JSON.parse(localStorage.getItem(CR_KEY)) || {}; } catch(e){ return {}; } };
const crSave = patch => { try { localStorage.setItem(CR_KEY, JSON.stringify(Object.assign(crStore(), patch))); } catch(e){} };
const crNum = n => Number(n).toLocaleString('en-IN');
const crOrd = n => { const t = n % 100, u = n % 10;
  return n + (t >= 11 && t <= 13 ? 'th' : u === 1 ? 'st' : u === 2 ? 'nd' : u === 3 ? 'rd' : 'th'); };

// A bowler's wickets come before his runs, a batter's runs before his wickets [A217]. The
// role is the career's own (`d.role`): what the player was built as, not one season's form.
const crBowls = d => d && (d.role === 'pace' || d.role === 'spin');

// "10 wkts · 89 runs" for a bowler, "156 runs" for a batter who took none. The role's own
// figure is always shown, even at nought; the other only when there is something in it.
function crFigs(d, runs, wkts){
  const r = `<b>${crNum(runs)}</b> run${runs === 1 ? '' : 's'}`, w = `<b>${wkts}</b> wkt${wkts === 1 ? '' : 's'}`;
  return crBowls(d) ? [w, runs ? r : null] : [r, wkts ? w : null];
}

function crPick(group, btn){
  document.querySelectorAll(`#${group} .room-choice`).forEach(b => b.classList.toggle('sel', b === btn));
  if (group === 'crRoles') crRoleNote();
  // [A229] an overseas player picks a country to play for
  if (group === 'crPassport') $('#crCountryWrap').classList.toggle('hide', btn.dataset.v !== '1');
}

// On a phone the role cards show only their names; the chosen role's line sits under them.
function crRoleNote(){
  const el = document.getElementById('crRoleNote'), sel = document.querySelector('#crRoles .room-choice.sel');
  if (el && sel) el.textContent = sel.querySelector('.cr-role-blurb').textContent;
}
const crChosen = group => (document.querySelector(`#${group} .room-choice.sel`) || {}).dataset;

// --- create --------------------------------------------------------------------------------

// Small line icons for the role cards [A219]: a bat, a ball, gloves. Drawn in currentColor
// so a chosen card's colour carries into its icon.
const CR_SVG = p => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
const CR_BAT = '<path d="M15.5 3.5l5 5-10.5 10.5a2.5 2.5 0 0 1-3.5 0l-1.5-1.5a2.5 2.5 0 0 1 0-3.5z"/><path d="M6 18l-3 3"/>';
const CR_BALL = '<circle cx="12" cy="12" r="8"/><path d="M7 6.5c2.5 3 2.5 8 0 11M17 6.5c-2.5 3-2.5 8 0 11"/>';
const CR_ROLE_ICON = {
  opener: CR_SVG(CR_BAT), middle: CR_SVG(CR_BAT), finisher: CR_SVG(CR_BAT + '<path d="M18 2l1 2 2 1-2 1-1 2-1-2-2-1 2-1z"/>'),
  keeper: CR_SVG('<path d="M6 21v-9a2 2 0 0 1 4 0V8a2 2 0 0 1 4 0v4a2 2 0 0 1 4 0v3a6 6 0 0 1-6 6z"/><path d="M6 14l-2-2"/>'),
  pace: CR_SVG(CR_BALL + '<path d="M2 9h3M1 13h4"/>'), spin: CR_SVG(CR_BALL + '<path d="M20 4a10 10 0 0 1 2 6"/>'),
  allrounder: CR_SVG('<g transform="translate(-2 2) scale(.8)">' + CR_BAT + '</g><circle cx="17.5" cy="7" r="4"/>'),
};

function crShowCreate(){
  CR.data = null;
  $('#career').classList.remove('started');
  $('#crMain').classList.add('hide');
  $('#crCreate').classList.remove('hide');
  const o = CR.options;
  // A card per role [A219]: an icon, the name and one line on what the job is, the line
  // the server's own role table carries so it cannot promise a rule the game lacks.
  $('#crRoles').innerHTML = o.roles.map((r, i) =>
    `<button class="room-choice cr-role${i ? '' : ' sel'}" data-v="${r.key}" onclick="crPick('crRoles', this)">
      <span class="cr-role-icon">${CR_ROLE_ICON[r.key] || ''}</span>
      <b>${esc(r.label)}</b><span class="cr-role-blurb">${esc(r.blurb || '')}</span></button>`).join('')
    + '<p class="cr-role-note" id="crRoleNote"></p>';
  crRoleNote();
  const years = [];
  for (let y = o.first_debut; y <= o.last_debut; y++) years.push(y);
  $('#crDebut').innerHTML = years.map(y =>
    `<button class="room-choice${y === 2012 ? ' sel' : ''}" data-v="${y}" onclick="crPick('crDebut', this)">${y}</button>`).join('');
  // [A228] Who you are, for the papers: a celebration and up to three ambitions.
  $('#crCel').innerHTML = [{key: '', label: 'None'}, ...(o.celebrations || [])].map((c, i) =>
    `<button class="room-choice${i ? '' : ' sel'}" data-v="${c.key}" onclick="crPick('crCel', this)">${esc(c.label)}</button>`).join('');
  $('#crCountry').innerHTML = (o.nations || []).map((n, i) =>
    `<button class="room-choice${i ? '' : ' sel'}" data-v="${esc(n)}" onclick="crPick('crCountry', this)">${esc(n)}</button>`).join('');
  $('#crAmb').innerHTML = (o.ambitions || []).map(a =>
    `<button class="room-choice" data-v="${a.key}" onclick="crAmbition(this)">${esc(a.label)}</button>`).join('');
}

function crAmbition(btn){
  const on = document.querySelectorAll('#crAmb .room-choice.sel').length;
  if (!btn.classList.contains('sel') && on >= (CR.options.ambitions_max || 3)){
    slip(`Up to ${CR.options.ambitions_max || 3} ambitions.`);
    return;
  }
  btn.classList.toggle('sel');
}

async function crStart(btn){
  if (CR.busy) return;
  const name = $('#crName').value.trim();
  if (!name){ slip('Give your player a name.'); $('#crName').focus(); return; }
  CR.busy = true; btn.disabled = true;
  try {
    const num = $('#crNumber').value.trim();
    const d = await api('/api/career/start', {method: 'POST', headers: CR_JSON, retry: true, body: JSON.stringify({
      name, role: crChosen('crRoles').v, debut: Number(crChosen('crDebut').v),
      overseas: crChosen('crPassport').v === '1',
      number: num === '' ? null : Number(num), home: $('#crHome').value.trim(),
      celebration: (crChosen('crCel') || {}).v || '',
      ambitions: [...document.querySelectorAll('#crAmb .room-choice.sel')].map(b => b.dataset.v),
      country: crChosen('crPassport').v === '1' ? ((crChosen('crCountry') || {}).v || '') : ''})});
    crSave({token: d.token});
    CR.seen = 0;
    crShow(d, 'spin');
  } catch(e){ slip(e.message || 'Could not start a career.'); }
  finally { CR.busy = false; btn.disabled = false; }
}

// [A227] An admin redraws both spins before the career begins: the same player on a new seed.
// The button shows only for an admin; the server answers 404 to anybody else regardless.
async function crReroll(btn){
  if (CR.busy) return;
  CR.busy = true; btn.disabled = true;
  try {
    const d = await api('/api/career/reroll', {method: 'POST', headers: CR_JSON,
                                                body: JSON.stringify({token: CR.data.token})});
    crSave({token: d.token});
    CR.seen = 0;
    crShow(d, 'spin');
  } catch(e){ slip(e.message || 'Could not reroll.'); }
  finally { CR.busy = false; btn.disabled = false; }
}

// --- one move ------------------------------------------------------------------------------

async function crMove(move){
  if (CR.busy) return;
  CR.busy = true;
  const before = CR.data.seasons.length;
  const mega = move === 'k' || move[0] === 'b';
  if (move[0] === 'w') crLoading('The papers go to print…', 'Your career, as the papers tell it.');
  else if (move !== 'r') crLoading(mega ? 'The auctioneer opens the bidding…' : `Playing ${CR.data.pending.year}…`);
  try {
    const d = await api('/api/career/move', {method: 'POST', headers: CR_JSON, body: JSON.stringify({token: CR.data.token, move})});
    crSave({token: d.token});
    const next = () => crShow(d, d.seasons.length > before ? 'season' : null);
    if (mega && d.auction){ CR.data = d; crHead(d); crTimeline(d); return crAuctionPlay(d.auction, next); }
    next();
  } catch(e){
    slip(e.message || 'That did not go through. Try again.');
    crShow(CR.data, null);
  } finally { CR.busy = false; }
}

function crLoading(text, sub){
  $('#crStage').innerHTML = `<div class="cr-loading"><div class="cr-ball"></div><b>${esc(text)}</b>
    <span>${esc(sub || 'Every match of the season, ball by ball.')}</span></div>`;
}

// --- drawing -------------------------------------------------------------------------------

function crShow(d, moment){
  CR.data = d;
  $('#career').classList.add('started');
  $('#crCreate').classList.add('hide');
  $('#crMain').classList.remove('hide');
  // While a season is being told, the header and the chart stay where it began [A214]: the
  // season's own grade and its effect on your Rating are the end of the story, not the start.
  const hold = (moment === 'season' && !!d.season_play) || (d.season_play && !d.season_play.complete);
  crHead(d, hold);
  // The chart opens by itself only where the career is the subject: the season card and
  // the career card. Everywhere else it folds away so the decision sits near the top.
  crTimeline(d, hold, (moment === 'season' && !d.season_play) || !d.pending);
  if (moment === 'spin'){
    // The player bar shows the Rating and potential the two spins decide [A219]; it waits
    // for the names to stop rolling rather than giving the answer away first.
    $('#crHead').classList.add('cr-held');
    return crSpin(d);
  }
  $('#crHead').classList.remove('cr-held');
  if (moment === 'season') return d.season_play ? crFeed(d) : crSeason(d, d.seasons[d.seasons.length - 1]);
  if (d.pending && d.pending.kind === 'moment') return d.season_play ? crFeed(d) : crEnd(d);
  if (!d.pending) return crEnd(d);
  if (d.pending.kind === 'epilogue') return crEpilogue(d);
  if (d.pending.kind === 'mega') return crMega(d);
  if (d.pending.kind === 'plan') return crPlan(d);
  return d.pending.kind === 'offer' ? crOffers(d) : crEvent(d);
}

// [A214] The player card: your Rating (what the engine plays), how far it is from your
// potential, and where you are now. The Season grade is each season's own mark, on the chart.
// [A226] A match you sat out says WHY in the words a fan would use: left out is selection,
// out is illness, family or international duty. Injuries come only from the injury cards.
function crAbsenceLabel(a){
  if (a === 'rested') return 'Rested';
  if (a === 'not selected') return 'Not selected';
  if (a === 'rotation') return 'Rotated';
  if (a === 'team balance' || a === 'the overseas combination') return 'Left out · ' + esc(a);
  return 'Out · ' + esc(a);
}

function crTier(r){ return r >= 90 ? 'elite' : r >= 84 ? 'star' : r >= 77 ? 'good' : 'base'; }

function crHead(d, hold){
  if (hold && d.season_play && d.season_play.complete && d.seasons.length){
    const r = d.seasons[d.seasons.length - 1];
    d = {...d, seasons: d.seasons.slice(0, -1), rating: r.rating || d.rating, age: r.age || d.age,
         potential: r.potential || d.potential,
         pending: {kind: 'season', offers: [{franchise: r.franchise, crest: r.crest}]}};
  }
  const s = d.seasons, last = s[s.length - 1];
  const club = d.pending && d.pending.offers && d.pending.offers.length === 1 && d.pending.kind !== 'offer'
    ? d.pending.offers[0] : null;
  const crest = club ? club.crest : last ? last.crest : null;
  const clubName = club ? club.franchise : last ? last.franchise : 'Unsigned';
  const lo = d.start.card || 70, hi = d.potential || 99;
  const pct = Math.max(4, Math.min(100, Math.round(100 * (d.rating - lo) / Math.max(1, hi - lo))));
  // [A224] What the potential has grown by, shown apart from what the spin gave you.
  const was = Math.min(hi, d.potential_start || hi), earned = hi - was;
  const from = Math.round(100 * (was - lo) / Math.max(1, hi - lo));
  const runs = s.reduce((a, r) => a + r.runs, 0), wkts = s.reduce((a, r) => a + r.wickets, 0);
  const tot = s.length ? `<div class="cr-tot">
      <span><b>${s.length}</b> season${s.length === 1 ? '' : 's'}</span>
      <span><b>${crNum(s.reduce((a, r) => a + r.matches, 0))}</b> matches</span>
      ${crFigs(d, runs, wkts).filter(Boolean).map(t => `<span>${t}</span>`).join('')}
      <span><b>${s.filter(r => r.champion).length}</b> title${s.filter(r => r.champion).length === 1 ? '' : 's'}</span></div>` : '';
  $('#crHead').innerHTML = `
    <div class="cr-pc">
      <div class="cr-pc-rating ${crTier(d.rating)}"><b>${d.rating}</b><span>Rating</span></div>
      <div class="cr-pc-who">
        <b>${esc(d.name)}</b>
        <span>${d.number != null ? `No. ${d.number} · ` : ''}${esc(d.role_label)} · age ${d.age}${d.overseas ? ' · overseas' : ''}${d.home ? ` · ${esc(d.home)}` : ''} · debut ${d.debut}</span>
      </div>
      <div class="cr-pc-club">${crestImg(crest, 'cr-pc-crest')}<span>${esc(clubName)}</span></div>
      <div class="cr-pc-foot">
        <div class="cr-pot" title="From where you started (${lo}) to your potential (${hi})${earned ? `, ${earned} of it earned` : ''}">
          <div class="cr-pot-bar"><i style="width:${pct}%"></i>${earned ? `<u style="left:${from}%"></u>` : ''}</div>
          <span>Potential <b>${hi}</b>${earned ? ` <em>+${earned} earned</em>` : ''}</span></div>
        ${tot}
        ${crNationalChip(d)}
        ${crAmbitionChips(d)}
      </div>
    </div>`;
}

// [A229] Your country and your international record so far.
function crNationalChip(d){
  if (!d.country) return '';
  const cls = d.in_side ? 'in' : d.caps ? 'out' : '';
  const what = d.in_side ? `in the side · ${d.caps} international${d.caps === 1 ? '' : 's'}`
    : d.caps ? `${d.caps} international${d.caps === 1 ? '' : 's'} · out of the side` : 'uncapped';
  return `<div class="cr-nt ${cls}"><b>${esc(d.country)}</b> ${what}</div>`;
}

// [A228] The ambitions set on the first day, and how each stands.
function crAmbitionChips(d){
  if (!d.ambitions || !d.ambitions.length) return '';
  return `<div class="cr-ambs">${d.ambitions.map(a =>
    `<span class="cr-amb ${a.state}" title="${a.state === 'done' ? 'Done in ' + a.year : a.state === 'broken' ? 'Out of reach since ' + a.year : 'Still to do'}">${a.state === 'done' ? '✓' : a.state === 'broken' ? '✕' : '○'} ${esc(a.label)}</span>`).join('')}</div>`;
}

// The path of a career [A214]: a bar per season for your Rating, a dot for that season's
// grade, and your potential as a line. Rating moves with age and what happens to you; the
// dots are the luck of each season around it.
function crTimeline(d, hold, open){
  const held = hold && d.season_play && d.season_play.complete;
  const s = held ? d.seasons.slice(0, -1) : d.seasons;
  if (!s.length){ $('#crTimeline').innerHTML = ''; return; }
  // [A224] the potential as it stood: each season's own line, and the key the latest
  const pot = held ? (d.seasons[d.seasons.length - 1].potential || d.potential) : d.potential;
  const y = v => Math.max(0, Math.min(100, (v - 55) / (99 - 55) * 100));
  const peak = Math.max(...s.map(r => r.rating || r.card));
  // Gridlines at 70, 80 and 90 across the whole plot, as a background that scrolls with it
  // (`local`), so they run on however many seasons there are. The bar area is 120px tall and
  // starts 20px down the plot; the numbers sit in a gutter on the left of the first column.
  const gy = g => (20 + 120 * (1 - y(g) / 100)).toFixed(1);
  const grid = [70, 80, 90].map(g => `linear-gradient(to bottom,transparent ${gy(g)}px,rgba(255,255,255,.09) ${gy(g)}px,`
    + `rgba(255,255,255,.09) calc(${gy(g)}px + 1px),transparent calc(${gy(g)}px + 1px))`).join(',');
  $('#crTimeline').innerHTML = `
    <details class="cr-path-wrap"${open ? ' open' : ''}>
    <summary>Career path <span>${s.length} season${s.length === 1 ? '' : 's'} · peak Rating ${peak}</span></summary>
    <div class="cr-path">
      <div class="cr-path-key">
        <span class="cr-path-tiers" title="Rating bars by level">Rating <i class="t base"></i>&lt;77 <i class="t good"></i>77+
          <i class="t star"></i>84+ <i class="t elite"></i>90+</span>
        <span><i class="dot hot"></i>Grade 3+ above <i class="dot cold"></i>3+ below <i class="dot"></i>close</span>
        <span><i class="pot"></i>Potential ${pot}</span>
        ${s.some(r => r.squad) ? '<span><i class="t good faded"></i>Squad season</span>' : ''}</div>
      <div class="cr-path-plot" style="background-image:${grid}">
        ${s.map((r, i) => { const rt = r.rating || r.card; return `
        <div class="cr-path-col${r.champion ? ' champ' : ''}${r.squad ? ' squad' : ''}" title="${r.year} · ${esc(r.franchise)} · Rating ${rt} · grade ${r.card}${r.champion ? ' · champions' : ''}">
          <div class="cr-path-area">
            ${i ? '' : [70, 80, 90].map(g => `<span class="cr-path-axis" style="bottom:${y(g)}%">${g}</span>`).join('')}
            <div class="cr-path-pot" style="bottom:${y(r.potential || d.potential)}%"></div>
            <div class="cr-path-bar ${crTier(rt)}" style="height:${y(rt)}%"><b>${rt}</b></div>
            ${r.champion ? `<div class="cr-path-cup" style="bottom:${y(rt)}%">🏆</div>` : ''}
            <div class="cr-path-dot${r.card >= rt + 3 ? ' hot' : r.card <= rt - 3 ? ' cold' : ''}" style="bottom:${y(r.card)}%"></div>
          </div>
          ${crestImg(r.crest, 'cr-path-crest')}<span>${String(r.year).slice(2)}</span>
        </div>`; }).join('')}
      </div>
    </div></details>`;
  // the latest season in view, also when the chart is opened later (closed, it has no width)
  const wrap = document.querySelector('.cr-path-wrap'), plot = document.querySelector('.cr-path-plot');
  const toEnd = () => { if (plot) plot.scrollLeft = plot.scrollWidth; };
  toEnd();
  if (wrap) wrap.addEventListener('toggle', toEnd);
}

function crTemplateCard(label, t, cls, what){
  return `<div class="cr-spin-card ${cls}">
    <div class="cr-spin-label">${label}</div>
    ${crestImg(t.crest, 'cr-spin-crest')}
    <b class="cr-roll" data-final="${esc(t.name)}">${esc(t.name)}</b>
    <span>${t.year} · ${esc(t.franchise)}</span>
    ${t.card ? `<div class="cr-spin-num cr-held"><b>${t.card}</b><i>${what}</i></div>` : ''}
  </div>`;
}

function crSpin(d){
  $('#crStage').innerHTML = `
    <div class="cr-spin">
      ${crTemplateCard('You start like', d.start, 'start', 'your Rating at 19')}
      ${crTemplateCard('Your ceiling', d.ceiling, 'ceiling', 'your potential')}
    </div>
    <p class="room-note">Your Rating climbs from the first toward the second, then falls away
      with age. When you peak and how fast you fade are for you to find out. The second is not
      a limit: a season graded well above it raises your potential while you are still growing,
      and good form in your thirties slows the decline. Coaches, mentors and injuries move your
      Rating; each season's grade is how the matches actually went.</p>
    <div class="foot-actions cr-held" id="crSpinGo" style="justify-content:center">
      <button class="act lead" onclick="crShow(CR.data, null)">See who wants you</button>
      ${typeof ME !== 'undefined' && ME && ME.is_admin
        ? '<button class="act" onclick="crReroll(this)" title="Admin only">Reroll spins</button>' : ''}
    </div>`;
  crRoll(() => {
    $('#crHead').classList.remove('cr-held');
    const go = document.getElementById('crSpinGo');
    if (go) go.classList.remove('cr-held');
  });
}

// A short decelerating flicker of real names before the spin settles, like the deal's
// (rollDeal): the result is already fixed by the server, this only reveals it.
function crRoll(done){
  const els = [...document.querySelectorAll('.cr-roll')];
  // Each card's number appears as its name settles, and `done` runs once both have.
  const settle = el => {
    el.textContent = el.dataset.final;
    el.classList.add('settled');
    const num = el.parentElement.querySelector('.cr-spin-num');
    if (num) num.classList.remove('cr-held');
    if (els.every(e => e.classList.contains('settled')) && done) done();
  };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !els.length){
    els.forEach(settle);
    if (!els.length && done) done();
    return;
  }
  const pool = ['V Kohli', 'MS Dhoni', 'JJ Bumrah', 'SP Narine', 'AB de Villiers', 'R Ashwin',
                'CH Gayle', 'SL Malinga', 'RG Sharma', 'Rashid Khan', 'AD Russell', 'DA Warner'];
  els.forEach((el, n) => {
    let delay = 40, steps = 0;
    (function tick(){
      if (steps++ > 10 + 5 * n){ settle(el); return; }
      el.textContent = pool[Math.floor(Math.random() * pool.length)];
      delay *= 1.13;
      setTimeout(tick, delay);
    })();
  });
}

function crPlace(o){
  if (o.attack_rank != null)
    return o.attack_rank <= 2 ? 'Opens the bowling' : `Bowls ${crOrd(o.attack_rank)} in the attack`;
  return o.position != null ? `Bats at ${o.position}` : '';
}

function crOffers(d){
  const p = d.pending;
  // The squad's twelve's mean card rating, without you [A218]: a bar from a weak squad (74)
  // to a great one (90), so three offers compare at a glance rather than by reading numbers.
  const strength = o => o.strength == null ? '' : `<div class="cr-strength" title="Squad strength: the mean rating of its best twelve, without you">
      <span>Squad</span><i><b style="width:${Math.max(4, Math.min(100, Math.round(100 * (o.strength - 74) / 16)))}%"></b></i><em>${o.strength}</em></div>`;
  const deal = (o, i) => o.contract_year
    ? `<div class="cr-deal"><button class="act lead" onclick="crMove('o${i}')">Play on</button>
         <span>Year ${o.contract_year} of 3 · ${esc(o.price)}</span></div>`
    : `<div class="cr-deal">
         <button class="act lead" onclick="crMove('o${i}')">1 year · ${esc(o.price)}</button>
         ${o.price3 ? `<button class="act" onclick="crMove('c${i}')">3 years · ${esc(o.price3)}/yr</button>` : ''}
       </div>`;
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="over-line">${p.year} · SEASON ${p.season_no}</div>
      <h2 class="cr-h">${p.offers.some(o => o.contract_year) ? 'Your contract goes on'
        : p.season_no === 1 ? 'Who signs you?' : 'Who wants you this season?'}</h2>
      ${p.club_gone ? `<p class="room-note" style="text-align:center">${esc(p.club_gone)} have no team in ${p.year}, so there is no staying put.</p>` : ''}
      ${p.squad ? `<p class="cr-warn">Last season was poor: you start this one as a squad player and
        will get only a few matches.${p.offers.some(o => o.contract_year) ? ' Your contract keeps you safe from release.'
        : ' Another poor season and nobody will sign you.'}</p>` : ''}
      <div class="cr-offers">${p.offers.map((o, i) => `
        <div class="cr-offer${o.stay ? ' stay' : ''} ${crestClass(o.crest)}">
          ${o.stay ? `<span class="cr-tag">${o.contract_year ? 'Contract' : 'Stay'}</span>` : ''}
          ${crestImg(o.crest, 'cr-offer-crest')}
          <b>${esc(o.franchise)}</b>
          <span class="cr-place">${esc(crPlace(o))}</span>
          ${strength(o)}
          ${deal(o, i)}
        </div>`).join('')}</div>
      <p class="room-note">A three-year deal fixes your price for three seasons and keeps you
        safe from release, but you cannot move or earn a raise. Stronger squads win more
        titles, but you may play a smaller part in them.</p>
      ${p.can_retire ? `<div class="foot-actions" style="justify-content:center">
        <button class="act" onclick="crRetire()">Retire instead</button></div>` : ''}
    </div>`;
}

// --- a mega-auction year [A212] --------------------------------------------------------------
// Every contract ends. Your club may come to you with a retention offer at one of the real
// slabs; take it, or go into the auction at a base price you choose.

function crMega(d){
  const p = d.pending, m = p.mega, last = d.seasons[d.seasons.length - 1];
  CR.base = m.suggested;
  const retain = m.retain ? `
    <div class="cr-offer stay cr-retain ${crestClass(m.retain.crest)}">
      <span class="cr-tag">Retention</span>
      ${crestImg(m.retain.crest, 'cr-offer-crest')}
      <b>${esc(m.retain.franchise)}</b>
      <span class="cr-place">${esc(crPlace(m.retain))}</span>
      <div class="cr-deal"><button class="act lead" onclick="crMove('k')">Stay · ${esc(m.retain.price)}</button></div>
      <p class="room-note">Your club wants to keep you before the auction, at a retention slab.</p>
    </div>` : '';
  const noRetain = m.retain ? '' : `<p class="room-note cr-mega-note">${esc(last.franchise)} have not asked to retain you.
      Retention is offered from a season card of ${m.retain_card}.</p>`;
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="over-line">${p.year} · MEGA AUCTION · SEASON ${p.season_no}</div>
      <h2 class="cr-h">Every contract ends this year</h2>
      ${p.squad ? `<p class="cr-warn">Last season was poor: whoever buys you, you start as a squad player.</p>` : ''}
      ${noRetain}
      <div class="cr-offers cr-mega${m.retain ? '' : ' solo'}">
        ${retain}
        <div class="cr-offer cr-auction">
          <span class="cr-tag">Auction</span>
          <b>Go into the auction</b>
          <span class="cr-place">Your base price</span>
          <div class="cr-bases">${m.bases.map((b, i) => `<button class="room-choice${i === m.suggested ? ' sel' : ''}"
            onclick="crPickBase(${i}, this)">${esc(b)}${i === m.suggested ? '<i>usual</i>' : ''}</button>`).join('')}</div>
          <div class="cr-deal"><button class="act${m.retain ? '' : ' lead'}" onclick="crMove('b' + CR.base)">Enter the auction</button></div>
          <p class="room-note">Every club bids what it thinks you are worth. Set your base too high
            and nobody may meet it: then a club signs you late, at your base, as a squad player.</p>
        </div>
      </div>
      ${p.can_retire ? `<div class="foot-actions" style="justify-content:center">
        <button class="act" onclick="crRetire()">Retire instead</button></div>` : ''}
    </div>`;
}

function crPickBase(i, btn){
  CR.base = i;
  document.querySelectorAll('.cr-bases .room-choice').forEach(b => b.classList.toggle('sel', b === btn));
}

// The bidding, played back: at most ~4 s whatever the number of bids (the auction floor's
// own budget, A146), then the hammer. With the floor's sound and auctioneer [A218]: a knock
// per bid (at most one every 90 ms, so a long war rattles rather than roars), the strip of
// the last six bids, the stage in the leading club's colours, and the sale announced.
const CR_KNOCK_GAP = 90;

function crAucSound(){
  const el = document.getElementById('crAucSound');
  if (!el) return;
  el.innerHTML = `
    <button class="auc-sound-btn${SOUND_ON ? ' on' : ''}" onclick="setSound(${!SOUND_ON}); crAucSound()"
      title="Gavel and bids">${SOUND_ON ? 'Sound on' : 'Sound off'}</button>
    <button class="auc-sound-btn${VOICE_ON ? ' on' : ''}" onclick="setVoice(${!VOICE_ON}); crAucSound()"
      title="The auctioneer announces the sale">${VOICE_ON ? 'Voice on' : 'Voice off'}</button>`;
}

function crAucTrail(bids){
  const el = document.getElementById('crAucTrail');
  if (!el) return;
  if (!bids.length){ el.innerHTML = '<span class="auc-trail-sum">No bids yet</span>'; return; }
  const teams = [];
  bids.forEach(b => { if (!teams.includes(b.short)) teams.push(b.short); });
  const who = teams.length === 2 ? `${teams[0]} v ${teams[1]}` : teams.length === 1 ? teams[0] : `${teams.length} teams`;
  const last = bids.slice(-6);
  el.innerHTML = `<span class="auc-trail-sum"><b>${bids.length}</b> bid${bids.length === 1 ? '' : 's'} · ${esc(who)}</span>
    <span class="auc-trail-bids">${last.map((b, i) => `<span class="auc-trail-bid ${crestClass(b.crest)}${i === last.length - 1 ? ' new' : ''}"
      title="${esc(b.short)} ${esc(b.price)}">${crestImg(b.crest, '', `<i>${esc(b.short)}</i>`)}${esc(b.price)}</span>`).join('')}</span>`;
}

function crAuctionPlay(a, done){
  const name = (CR.data && CR.data.name) || 'You';
  const british = VOICE_STYLE === 'british';
  const finish = `<div class="foot-actions" style="justify-content:center">
      <button class="act lead" id="crAucGo">Continue</button></div>`;
  if (a.retained){
    $('#crStage').innerHTML = `<div class="cr-step"><div class="cr-auc-stage sold ${crestClass(a.crest)}">
      <div class="cr-auc-sound" id="crAucSound"></div>
      <div class="over-line">RETAINED</div>${crestImg(a.crest, 'cr-auc-crest')}
      <b class="cr-auc-price">${esc(a.price)}</b><span>${esc(a.franchise)} keep you</span></div>${finish}</div>`;
    crAucSound();
    $('#crAucGo').onclick = done;
    play('sold', true);
    speak(british ? ['Retained.', `${name} stays with ${a.franchise},`, `at ${priceSpoken(a.price_lakh)}.`]
                  : `${name} retained by ${a.franchise} for ${priceWords(a.price_lakh)}.`);
    return;
  }
  $('#crStage').innerHTML = `<div class="cr-step"><div class="cr-auc-stage" id="crAuc">
      <div class="cr-auc-sound" id="crAucSound"></div>
      <div class="over-line">ON THE BLOCK · BASE ${esc(a.base)}</div>
      <div class="cr-auc-lead" id="crAucLead"><b class="cr-auc-price">${esc(a.base)}</b><span>Who will open?</span></div>
      <div class="auc-trail cr-auc-trail" id="crAucTrail"></div>
      <div class="cr-auc-stamp" id="crAucStamp"></div></div>
      <div id="crAucAfter"></div></div>`;
  crAucSound();
  crAucTrail([]);
  speak(british ? [`${name}.`, 'Who will open the bidding?'] : `${name}. Base price ${a.base.replace('₹', '')}.`);
  const bids = a.bids, reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const gap = reduce ? 0 : Math.max(40, Math.min(220, 4000 / Math.max(1, bids.length)));
  const stage = $('#crAuc');
  let i = 0, lastKnock = 0, tint = '';
  const retint = cls => { if (tint) stage.classList.remove(tint); tint = cls; if (cls) stage.classList.add(cls); };
  const hammer = () => {
    stage.classList.add(a.sold ? 'sold' : 'unsold');
    retint(crestClass(a.crest));
    $('#crAucStamp').innerHTML = a.sold ? `SOLD · ${esc(a.short)}` : 'UNSOLD';
    $('#crAucLead').innerHTML = `${crestImg(a.crest, 'cr-auc-crest')}<b class="cr-auc-price">${esc(a.price)}</b>
      <span>${a.sold ? esc(a.franchise) : `Nobody met your base. ${esc(a.franchise)} sign you late, as a squad player.`}</span>`;
    $('#crAucAfter').innerHTML = finish;
    $('#crAucGo').onclick = done;
    if (a.sold){
      play('sold', true);
      speak(british ? ['Sold!', `${name}.`, `To ${a.franchise},`, `at ${priceSpoken(a.price_lakh)}.`]
                    : `Sold! ${name}, to ${a.franchise}, for ${priceWords(a.price_lakh)}.`);
    } else {
      play('unsold');
      speak(british ? [`${name}.`, 'No bid. Passed.'] : `${name}. Unsold.`);
    }
  };
  (function step(){
    if (i >= bids.length) return setTimeout(hammer, reduce ? 0 : 500);
    const b = bids[i++];
    $('#crAucLead').innerHTML = `${crestImg(b.crest, 'cr-auc-crest')}<b class="cr-auc-price">${esc(b.price)}</b><span>${esc(b.short)}</span>`;
    retint(crestClass(b.crest));
    crAucTrail(bids.slice(0, i));
    const now = performance.now();
    if (now - lastKnock >= CR_KNOCK_GAP){ lastKnock = now; play('bid', false); }
    setTimeout(step, gap);
  })();
}

// --- the captain's bowling plan [A213] ------------------------------------------------------
// A phase for each of your side's bowlers. The order the server lists them in is the order
// the move's digits follow: 0 any, 1 powerplay, 2 middle, 3 death.

const CR_PHASES = [['Any', null], ['Powerplay', 'powerplay'], ['Middle', 'middle'], ['Death', 'death']];

function crPlanStart(plan){
  CR.plan = plan.map(b => Math.max(0, CR_PHASES.findIndex(([, p]) => p === b.phase)));
  CR.planDirty = false;
}

function crPlanRows(plan){
  return plan.map((b, i) => {
    const facts = b.you ? 'You' : [b.style ? b.style[0].toUpperCase() + b.style.slice(1) : null,
      b.wickets != null ? `${b.wickets} wkts` : null, b.economy != null ? `econ ${b.economy.toFixed(2)}` : null]
      .filter(Boolean).join(' · ');
    return `<div class="cr-plan-row"><span class="cr-plan-who"><b>${b.you ? 'You' : esc(b.name)}</b><i>${esc(b.you ? 'your own overs' : facts)}</i></span>
      <div class="cr-plan-ph">${CR_PHASES.map(([label], j) => `<button class="room-choice${CR.plan[i] === j ? ' sel' : ''}"
        onclick="crPlanPick(${i}, ${j}, this)">${label}</button>`).join('')}</div></div>`;
  }).join('');
}

function crPlanPick(i, j, btn){
  CR.plan[i] = j;
  CR.planDirty = true;
  btn.parentElement.querySelectorAll('.room-choice').forEach(b => b.classList.toggle('sel', b === btn));
}

function crPlan(d){
  const p = d.pending, o = p.offers[0];
  crPlanStart(p.plan);
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="over-line">${p.year} · ${esc(o.franchise).toUpperCase()} · CAPTAIN</div>
      <h2 class="cr-h">Your bowling plan</h2>
      <p class="room-note" style="text-align:center">Give each bowler a phase. Their overs go there first;
        a bowler kept for the death is held back until he is needed. Nobody bowls more than four
        overs or two running, and a plan never leaves an innings short of bowlers.</p>
      <div class="cr-plan">${crPlanRows(p.plan)}</div>
      <div class="foot-actions" style="justify-content:center">
        <button class="act lead" onclick="crMove('p' + CR.plan.join(''))">Start the season</button></div>
    </div>`;
}

function crRetire(){
  if (!confirm('Retire now? Your career ends here.')) return;
  crMove('r');
}

function crEvent(d){
  const p = d.pending, e = p.event, o = p.offers[0];
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="over-line">${p.year} · ${esc(o.franchise).toUpperCase()} · BEFORE THE SEASON</div>
      <div class="cr-event cr-event-${esc(e.key)}">
        <div class="cr-event-icon">${CR_EVENT_ICON[e.key] || '◆'}</div>
        <h2 class="cr-h">${esc(e.title)}</h2>
        <p>${esc(e.text)}</p>
        <div class="cr-event-opts">${e.options.map((o, i) =>
          `<button class="cr-opt${i ? '' : ' lead'}" onclick="crMove('e${i}')">
            <b>${esc(o.label)}</b><span>${esc(o.hint)}</span></button>`).join('')}</div>
        <div class="cr-event-now">Your Rating going in: <b>${d.rating}</b></div>
      </div>
    </div>`;
}

const CR_EVENT_ICON = {quiet: '☀', form: '🔥', niggle: '🩹', captain: '©', coach: '🎓', injury: '🩹',
  fracture: '🦴', mentor: '🤝', rivalry: '⚔', slump: '🌧', home_league: '✈', one_more: '⏳'};

// --- the season as it happens [A202] ---------------------------------------------------------
// Your matches arrive one at a time, the table and the cap race moving with them. Playoff
// matches stop the feed and can be watched ball by ball on the solo season's scoreboard.

const CR_FEED_MS = 420;

function crStageName(stage, n){
  return stage === 'league' ? `Match ${n}` : stage;
}

// How a game went, for its colour [A218]: declared marks, presentation only -- a fifty or
// three wickets is a big game, thirty runs or two wickets a good one; out cheaply, or two
// overs or more at eleven an over without a wicket, a poor one. A bowler out cheaply at
// number ten has not had a poor game, so a batting failure is marked only for a batter.
function crBatTone(l){
  if (l.runs >= 50) return 'great';
  if (l.runs >= 30) return 'good';
  return l.out && l.runs < 10 && !crBowls(CR.data) ? 'poor' : '';
}
function crBowlTone(l){
  if (l.wickets >= 3) return 'great';
  if (l.wickets >= 2) return 'good';
  return !l.wickets && l.bowl_balls >= 12 && 6 * l.bowl_runs / l.bowl_balls >= 11 ? 'poor' : '';
}

function crFigures(l){
  if (l.dropped) return '<span class="cr-dnp drop">Dropped</span>';
  if (!l.played) return l.absence
    ? `<span class="cr-dnp out">${crAbsenceLabel(l.absence)}</span>`
    : '<span class="cr-dnp">Did not play</span>';
  const bat = l.batted ? `<span class="cr-fig ${crBatTone(l)}"><b>${l.runs}${l.out ? '' : '*'}</b> <i>(${l.balls})</i></span>` : null;
  const bowl = l.bowl_balls ? `<span class="cr-fig ${crBowlTone(l)}"><b>${l.wickets}/${l.bowl_runs}</b> <i>(${crOvers(l.bowl_balls)})</i></span>` : null;
  const bits = crBowls(CR.data) ? [bowl, bat] : [bat, bowl];
  return bits.filter(Boolean).join(' · ') || '<span class="cr-dnp">Not needed</span>';
}

// Career milestones reached in this match [A209], under its row.
function crFeedMilestones(l){
  if (!l.milestones || !l.milestones.length) return '';
  return `<li class="cr-ms">${l.milestones.map(t => `<span>★ ${esc(t)}</span>`).join('')}</li>`;
}

const crOvers = balls => balls % 6 ? `${Math.floor(balls / 6)}.${balls % 6}` : String(balls / 6);

function crFeedRow(l, n){
  const open = l.card != null ? ` onclick="crScorecard(${l.card})" title="Open the scorecard"` : '';
  return `<li class="cr-fr ${l.result === 'W' ? 'won' : l.result === 'L' ? 'lost' : ''}${l.stage !== 'league' ? ' po' : ''}${open ? ' open' : ''}"${open}>
    <span class="cr-fr-n">${esc(crStageName(l.stage, n))}</span>
    <span class="cr-fr-res">${l.result}</span>
    <span class="cr-fr-opp">${crestImg(l.opp_crest, 'cr-fr-crest')}${esc(l.opp_short)}</span>
    <span class="cr-fr-you">${l.approach ? `<em class="cr-call${l.stop === 'make_or_break' ? ' mob' : ''}">${esc(l.approach)}</em> ` : ''}${crFigures(l)}${crVerdict(l)}${l.pom ? ' <em class="cr-pom" title="Player of the Match">PoM</em>' : ''}</span>
    <span class="cr-fr-score">${esc(l.score_for)} <i>v</i> ${esc(l.score_against)}</span>
  </li>`;
}

// A make-or-break's outcome, on the match that decided it [A205].
function crVerdict(l){
  if (!l.verdict) return '';
  return l.verdict === 'kept' ? ' <em class="cr-verdict kept">Kept your place</em>'
                              : ' <em class="cr-verdict dropped">Dropped</em>';
}

// Your last five matches and where they leave you among your kind this season [A205]: the
// same reading that decides a make-or-break.
function crForm(l){
  if (!l.form || !l.form.length) return '';
  const cls = {'In form': 'hot', 'Out of form': 'cold'}[l.form_word] || '';
  return `<div class="cr-form ${cls}"><span>Form</span><b>${esc(l.form_word || 'Settling in')}</b>
    <i>${l.form.map(esc).join(' · ')}</i></div>`;
}

function crFeedPanel(l){
  const pos = l.position != null ? `${crOrd(l.position)}` : '—';
  const cap = (label, who, n, you, unit) =>
    `<div class="cr-cap${you ? ' you' : ''}"><span>${label}</span><b>${esc(who || '—')}</b><i>${n} ${unit}</i></div>`;
  $('#crFeedPanel').innerHTML = `
    <div><span>Record</span><b>W ${l.wins} · L ${l.losses}</b></div>
    <div><span>Table</span><b>${pos}</b></div>
    <div><span>You</span><b>${crFigs(CR.data, l.season_runs, l.season_wickets).filter(Boolean)
      .join(' · ').replace(/<\/?b>/g, '')}</b></div>
    ${cap('Orange Cap', l.orange, crNum(l.orange_n), l.orange_you, 'runs')}
    ${cap('Purple Cap', l.purple, l.purple_n, l.purple_you, 'wkts')}
    ${crForm(l)}`;
}

// The feed survives between requests [A204]: a season can pause at a key moment, and the
// response to your call carries the season so far again. Only rows not yet shown are added.
function crSeasonYear(d){
  return d.season_play.complete ? d.seasons[d.seasons.length - 1].year : d.pending.year;
}

function crFeed(d){
  const sp = d.season_play, year = crSeasonYear(d);
  crFeedStop();
  const same = CR.feed && CR.feed.year === year && document.getElementById('crFeedList');
  if (same){
    Object.assign(CR.feed, {d, sp});
  } else {
    const club = d.season_play.complete ? d.seasons[d.seasons.length - 1].franchise
                                        : d.pending.offers[0].franchise;
    CR.feed = {d, sp, year, i: 0, timer: null, n: 0, watched: new Set()};
    $('#crStage').innerHTML = `
      <div class="cr-step cr-feed">
        <div class="over-line">${year} · ${esc(club).toUpperCase()}${d.pending && d.pending.squad ? ' · SQUAD PLAYER' : ''}</div>
        <div class="cr-feed-panel" id="crFeedPanel"></div>
        <ol class="cr-feed-list" id="crFeedList"></ol>
        <div class="foot-actions" id="crFeedAction" style="justify-content:center"></div>
      </div>`;
  }
  crFeedResume();
}

function crFeedStop(){
  if (CR.feed && CR.feed.timer){ clearTimeout(CR.feed.timer); CR.feed.timer = null; }
}

function crFeedAppend(){
  const f = CR.feed, idx = f.i, l = f.sp.feed[f.i++];
  if (l.stage === 'league') f.n += 1;
  // [A228] A match that made the papers carries its clipping under it.
  const clips = (f.sp.press || []).filter(c => c.match === idx);
  l.clipped = clips.length > 0;
  $('#crFeedList').insertAdjacentHTML('beforeend', crFeedRow(l, f.n) + crFeedMilestones(l)
    + clips.map(c => `<li class="cr-np-li">${crClip(c, 'mini')}</li>`).join(''));
  crFeedPanel(l);
  return l;
}

function crFeedStep(){
  const f = CR.feed;
  if (!f) return;
  if (f.i >= f.sp.feed.length) return f.sp.complete ? crFeedEnd() : crMomentCard();
  const l = crFeedAppend();
  $('#crFeedList').lastElementChild.scrollIntoView({block: 'nearest'});
  if (l.watch != null && !f.watched.has(l.watch)){
    f.watched.add(l.watch);
    // Your own call: play it straight away. A playoff you sat out: offer it.
    if (l.approach) return crWatch(l.watch);
    $('#crFeedAction').innerHTML = `
      <button class="act lead" onclick="crWatch(${l.watch})">Watch the ${esc(l.stage)} v ${esc(l.opp_short)}</button>
      <button class="act" onclick="crFeedResume()">Skip it</button>`;
    return;
  }
  f.timer = setTimeout(crFeedStep, l.clipped ? CR_FEED_MS * 7
    : l.milestones && l.milestones.length ? CR_FEED_MS * 4 : CR_FEED_MS);
}

function crFeedResume(){
  $('#crFeedAction').innerHTML = `<button class="act" onclick="crFeedSkip()">Skip to the next decision</button>`;
  crFeedStep();
}

function crFeedSkip(){
  const f = CR.feed;
  if (!f) return;
  crFeedStop();
  while (f.i < f.sp.feed.length){
    const l = crFeedAppend();
    if (l.watch != null) f.watched.add(l.watch);
  }
  return f.sp.complete ? crFeedEnd() : crMomentCard();
}

// A key moment [A204]: the season has stopped, and this match is yours to call.
function crMomentCard(){
  const m = CR.feed.d.pending.moment, mob = m.kind === 'make_or_break';
  const head = mob ? 'MAKE OR BREAK' : `KEY MOMENT · ${esc(m.stage === 'league' ? 'League' : m.stage).toUpperCase()}`;
  $('#crFeedAction').innerHTML = `
    <div class="cr-moment${mob ? ' mob' : ''}">
      <div class="cr-moment-top">${crestImg(m.opp_crest, 'cr-moment-crest')}
        <div><div class="over-line">${head}</div>
          <b>v ${esc(m.opponent)}</b></div></div>
      <p class="cr-moment-stakes">${esc(m.stakes)}</p>
      ${CR.feed.d.pending.plan ? `<details class="cr-plan-moment"><summary>Change the bowling plan</summary>
        <div class="cr-plan">${(crPlanStart(CR.feed.d.pending.plan), crPlanRows(CR.feed.d.pending.plan))}</div></details>` : ''}
      <div class="cr-moment-label">How do you play it?</div>
      <div class="cr-moment-opts">${m.options.map((o, i) => `
        <button class="cr-moment-opt${i === 1 ? ' mid' : ''}" onclick="crCall(${i})">
          <b>${esc(o.label)}</b><span>${esc(o.hint)}</span></button>`).join('')}</div>
    </div>`;
  $('#crFeedAction').scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

async function crCall(i){
  if (CR.busy) return;
  CR.busy = true;
  $('#crFeedAction').innerHTML = `<div class="cr-loading small"><div class="cr-ball"></div><b>Playing it…</b></div>`;
  try {
    const move = `m${i}` + (CR.planDirty ? 'p' + CR.plan.join('') : '');
    const d = await api('/api/career/move', {method: 'POST', headers: CR_JSON, body: JSON.stringify({token: CR.data.token, move})});
    crSave({token: d.token});
    CR.data = d;
    crHead(d, true);
    crTimeline(d, true);
    crFeed(d);
  } catch(e){
    slip(e.message || 'That did not go through. Try again.');
    crMomentCard();
  } finally { CR.busy = false; }
}

function crFeedEnd(){
  const f = CR.feed;
  crFeedStop();
  // The solo season's own points table [A153], so a career's league reads like every other.
  MY_SIDE = {short: f.sp.your_short, name: f.sp.your_name, crest: f.sp.your_crest, kit: null};
  $('#crFeedAction').outerHTML = `
    <div class="cr-table-wrap"><div class="cr-label">The league table</div>
      ${standingsHtml(f.sp.table, {complete: true})}</div>
    <div class="foot-actions" style="justify-content:center">
      <button class="act lead" onclick="crAfterFeed()">See your season card</button>
    </div>`;
  CR.feed = null;
  CR.after = f.d;
}

function crAfterFeed(){
  const d = CR.after || CR.data;
  CR.after = null;
  crHead(d);
  crTimeline(d, false, true);
  crSeason(d, d.seasons[d.seasons.length - 1]);
  window.scrollTo({top: $('#crMain').offsetTop - 70, behavior: 'smooth'});
}

// A playoff match ball by ball: the two innings in turn, as the daily plays its match.
function crWatch(idx){
  const f = CR.feed, m = f.sp.watch[idx];
  const line = f.sp.feed.find(l => l.watch === idx);
  CR.watchCard = line ? line.card : null;
  MY_SIDE = {short: f.sp.your_short, name: f.sp.your_name, crest: f.sp.your_crest, kit: null};
  const side = (short, name, crest) => short === MY_SIDE.short ? {...MY_SIDE}
    : {short, name: name || short, crest: crest || null, kit: null};
  const home = side(m.home, m.home_name, m.home_crest), away = side(m.away, m.away_name, m.away_crest);
  const steps = [];
  if (m.home_innings) steps.push([m.home_innings, `${m.stage} · ${home.name} batting`, null, {bat: home, bowl: away}]);
  if (m.away_innings) steps.push([m.away_innings, `${m.stage} · ${away.name} batting`,
    m.home_innings ? {innings: m.home_innings, battingLabel: home.name} : null, {bat: away, bowl: home}]);
  $('#career').classList.add('hide');
  $('#reveal').classList.remove('hide');
  window.scrollTo(0, 0);
  let i = 0;
  (function next(){
    if (i >= steps.length) return crWatchDone();
    const [innings, label, prior, matchup] = steps[i++];
    startOverStepper(innings, label, next, prior, matchup);
  })();
}

function crSkipMatch(){
  clearOverTimer();
  OVER_STEP = null;
  crWatchDone();
}

function crWatchDone(){
  hideAllRevealScreens();
  $('#reveal').classList.add('hide');
  $('#career').classList.remove('hide');
  const list = $('#crFeedList');
  if (list && list.lastElementChild) list.lastElementChild.scrollIntoView({block: 'center'});
  // A watched match ends on its scorecard [A214], as it would on a broadcast; the season
  // waits behind it until it is closed.
  const card = CR.watchCard;
  CR.watchCard = null;
  if (card == null) return crFeedResume();
  $('#crFeedAction').innerHTML = `<button class="act" onclick="crFeedSkip()">Skip to the next decision</button>`;
  CR.feed.paused = true;
  crScorecard(card);
}

// [A214] Any of your matches, as the solo season's scorecard. Paused while it is open, so
// the season does not run on behind it.
function crScorecard(i){
  const sp = (CR.feed && CR.feed.sp) || (CR.data && CR.data.season_play);
  const r = sp && sp.cards && sp.cards[i];
  if (!r) return;
  MY_SIDE = {short: sp.your_short, name: sp.your_name, crest: sp.your_crest, kit: null};
  renderScorecard(r);
  if (CR.feed && CR.feed.timer){ crFeedStop(); CR.feed.paused = true; }
}

function crCloseScorecard(e){
  if (e && e.target !== e.currentTarget) return;     // a click inside the card keeps it open
  hideScorecard();
  if (CR.feed && CR.feed.paused){ CR.feed.paused = false; crFeedStep(); }
}

function crBadge(on, cls, text){ return on ? `<span class="cr-badge ${cls}">${text}</span>` : ''; }

function crBat(r){
  if (!r.balls && !r.runs) return '';
  const sr = Math.round(100 * r.runs / r.balls), avg = r.outs ? (r.runs / r.outs).toFixed(1) : '—';
  return `<div><b>${crNum(r.runs)}</b> runs<span>SR ${sr} · avg ${avg} · HS ${r.highest}${r.hundreds ? ` · ${r.hundreds}×100` : ''}${r.fifties ? ` · ${r.fifties}×50` : ''}</span></div>`;
}

function crBowl(r){
  if (!r.bowl_balls) return '';
  return `<div><b>${r.wickets}</b> wickets<span>econ ${(6 * r.bowl_runs / r.bowl_balls).toFixed(2)} · best ${r.best}</span></div>`;
}

function crSeason(d, r){
  const finish = r.champion ? 'Champions' : r.finalist ? 'Runners-up'
    : `${crOrd(r.position)} of ${r.teams}${r.playoffs ? ' · playoffs' : ''}`;
  const next = d.pending ? `<button class="act lead" onclick="crShow(CR.data, null)">${d.pending.kind === 'epilogue' ? 'What next?' : 'Next season'}</button>`
                         : `<button class="act lead" onclick="crEnd(CR.data)">See your career</button>`;
  // The bar above already shows next season's Rating; say so here, beside this season's.
  const last = r === d.seasons[d.seasons.length - 1];
  const nextR = last && d.pending && r.rating && d.rating !== r.rating
    ? `<em class="${d.rating > r.rating ? 'up' : 'down'}">→ ${d.rating} next season</em>` : '';
  const lines = crBowls(d) ? crBowl(r) + crBat(r) : crBat(r) + crBowl(r);
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="cr-season${r.champion ? ' champ' : ''} ${crestClass(r.crest)}">
        ${r.champion ? `<div class="cr-ribbon">🏆 IPL champions ${r.year}</div>` : ''}
        <div class="cr-season-top">
          ${crestImg(r.crest, 'cr-season-crest')}
          <div><div class="over-line">${r.year} · ${esc(r.franchise).toUpperCase()}</div>
            <div class="cr-finish">${finish}</div></div>
          <div class="cr-cards">
            ${r.rating ? `<div class="cr-card rating ${crTier(r.rating)}"><b>${r.rating}</b><span>Rating</span>${nextR}</div>` : ''}
            <div class="cr-card${r.card >= 90 ? ' elite' : ''}"><b>${r.card}</b><span>Season grade</span></div>
          </div>
        </div>
        <div class="cr-badges">
          ${crBadge(r.national === 'debut', 'gold', `${esc(CR.data.country)} debut`)}
          ${crBadge(r.national === 'recalled', 'gold', `Recalled by ${esc(CR.data.country)}`)}
          ${crBadge(r.national === 'kept', '', `${esc(CR.data.country)} · ${r.caps_total} internationals`)}
          ${crBadge(r.national === 'dropped', 'dim', `Dropped by ${esc(CR.data.country)}`)}
          ${crBadge(r.orange_cap, 'orange', 'Orange Cap')}
          ${crBadge(r.purple_cap, 'purple', 'Purple Cap')}
          ${crBadge(r.captain, '', 'Captain')}
          ${crBadge(r.contract_year > 0, '', `Contract year ${r.contract_year} of 3`)}
          ${crBadge(r.squad, 'dim', 'Squad player')}
          ${crBadge(r.missed - (r.dropped || 0) > 0, 'dim', `Missed ${r.missed - (r.dropped || 0)} match${r.missed - (r.dropped || 0) === 1 ? '' : 'es'}`)}
          ${crBadge(r.dropped > 0, 'dim', `Dropped for ${r.dropped} match${r.dropped === 1 ? '' : 'es'}`)}
          ${crBadge(r.card > r.form, 'dim', `Played to ${r.form}, reputation held the grade at ${r.card}`)}
          ${crBadge(!!r.mega, 'gold', esc(r.mega || ''))}
        </div>
        <div class="cr-lines">
          <div><b>${r.matches}</b> matches<span>${esc(r.price)}${r.stayed ? ' · stayed' : ''}</span></div>
          ${lines}
        </div>
        ${r.notes && r.notes.length ? `<div class="cr-notes"><div class="cr-label">What happened</div>
          <div class="cr-note-list">${r.notes.map((t, i) => { const tone = (r.note_tones || [])[i] || '';
            return `<p class="cr-note ${tone}"><i>${tone === 'up' ? '▲' : tone === 'down' ? '▼' : '•'}</i>${esc(t)}</p>`; }).join('')}</div></div>` : ''}
        ${crPapers(r)}
        ${r.plan && r.plan.length ? `<div class="cr-msl cr-plan-card"><div class="cr-label">Your bowling plan</div>
          <div>${r.plan.map(esc).join(' · ')}</div></div>` : ''}
        ${r.highlights.length ? `<div class="cr-hl"><div class="cr-label">Highlights</div>
          ${r.highlights.map(h => `<div><span>${esc(h.stage === 'league' ? 'League' : h.stage)} v ${esc(h.opponent)}</span><b>${esc(h.text)}</b></div>`).join('')}</div>` : ''}
      </div>
      ${!d.pending && d.summary ? `<p class="cr-warn">${d.summary.ending === 'released'
        ? 'Two poor seasons running, and no franchise came for you.' : 'That was the last season.'}</p>` : ''}
      <div class="foot-actions" style="justify-content:center">${next}${d.season_play && d.season_play.complete
        && d.season_play.analysis ? '<button class="act" id="crAnBtn" onclick="crAnalysis()">Season analysis</button>' : ''}</div>
      ${crMatchList(d)}
      <div class="an-wrap hide" id="crAnHost"></div>
    </div>`;
}

// Your matches of the season just played, each opening its scorecard [A214]. Only in the
// response that played it, as the feed is.
function crMatchList(d){
  const sp = d.season_play;
  if (!sp || !sp.complete || !sp.cards || !sp.cards.length) return '';
  let n = 0;
  // Every fixture is listed, dropped and missed ones included, so say how many you played:
  // the card above counts only those.
  const played = sp.feed.filter(l => l.played).length;
  const count = played === sp.feed.length ? `Your ${played} matches`
    : `Your ${sp.feed.length} fixtures · ${played} played`;
  return `<details class="cr-matches"><summary>${count} · tap one for its scorecard</summary>
    <ol class="cr-feed-list">${sp.feed.map(l => crFeedRow(l, l.stage === 'league' ? ++n : n)).join('')}</ol></details>`;
}

// The season's charts [A211], the draft season's own (analysis.js), drawn under the card. Only
// in the response that played the season, as its feed is: a reload goes to the card alone.
function crAnalysis(){
  const sp = CR.data && CR.data.season_play, host = document.getElementById('crAnHost');
  if (!sp || !sp.analysis || !host) return;
  host.classList.remove('hide');
  renderAnalysis(sp.analysis, host);
  document.getElementById('crAnBtn').remove();
  host.scrollIntoView({block: 'start', behavior: 'smooth'});
}

function crRank(n, what){
  if (n == null) return '';
  return n <= 25 ? `<div class="cr-rank"><b>${crOrd(n)}</b> all-time for ${what}</div>` : '';
}

function crEnd(d){
  const s = d.summary;
  if (!s) return;
  const bat = s.runs ? `<div><b>${crNum(s.runs)}</b> runs<span>SR ${s.strike_rate ?? '—'} · avg ${s.average ?? '—'} · HS ${s.highest}${s.hundreds ? ` · ${s.hundreds}×100` : ''}${s.fifties ? ` · ${s.fifties}×50` : ''}</span></div>` : '';
  const bowl = s.wickets ? `<div><b>${s.wickets}</b> wickets<span>econ ${s.economy ?? '—'} · avg ${s.bowling_average ?? '—'} · SR ${s.bowling_strike_rate ?? '—'}${s.best_bowling ? ` · best ${s.best_bowling}` : ''}</span></div>` : '';
  const ending = s.ending === 'retired' ? 'Retired' : s.ending === 'released' ? 'Released' : 'Played to 2026';
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="cr-final">
        <div class="over-line">THE CAREER OF</div>
        <h2 class="cr-final-name">${esc(d.name)}</h2>
        <div class="cr-final-sub">${esc(d.role_label)} · ${d.debut}–${d.debut + s.seasons - 1} · ${ending}</div>
        <div class="cr-final-crests">${d.seasons.filter((r, i, a) => a.findIndex(x => x.franchise === r.franchise) === i)
          .map(r => crestImg(r.crest, 'cr-final-crest')).join('')}</div>
        ${crRank(s.runs_rank, 'runs')}${crRank(s.wickets_rank, 'wickets')}
        <div class="cr-lines">
          <div><b>${s.matches}</b> matches<span>${s.seasons} seasons, ${s.regular_seasons} as a regular</span></div>
          ${crBowls(d) ? bowl + bat : bat + bowl}
          <div><b>${s.titles}</b> title${s.titles === 1 ? '' : 's'}<span>${s.finals} final${s.finals === 1 ? '' : 's'} · ${s.orange_caps} Orange, ${s.purple_caps} Purple Cap${s.orange_caps + s.purple_caps === 1 ? '' : 's'}</span></div>
          <div><b>${s.peak_rating || s.peak_card}</b> peak Rating<span>${s.peak_rating_year || s.peak_year} · best season grade ${s.peak_card} (${s.peak_year})</span></div>
          <div><b>${esc(s.earnings)}</b> earned<span>${s.franchises.length} franchise${s.franchises.length === 1 ? '' : 's'}</span></div>
          ${s.poms ? `<div><b>${s.poms}</b> Player of the Match<span>award${s.poms === 1 ? '' : 's'}</span></div>` : ''}
        </div>
        ${crAmbitionList(d)}
        ${crStory(d)}
      </div>
      ${crEndPapers(d)}
      <div class="cr-keep" id="crKeep"></div>
      <div class="foot-actions" style="justify-content:center">
        ${crReelItems(d).length > 2 ? '<button class="act lead" onclick="crReel()">Play the reel</button>' : ''}
        <button class="act${crReelItems(d).length > 2 ? '' : ' lead'}" onclick="crShowCard()">Your career card</button>
        <button class="act" onclick="pzShare(CR.data.share, 'Career copied. No names in it.')">Share as text</button>
        <button class="act" onclick="crNew()">Start a new career</button>
      </div>
    </div>`;
  crKeep(d);
}

// --- the papers [A228] ------------------------------------------------------------------------
// Every clipping is plain text from the server (game.press), escaped here. Three sizes: the
// lead story of a season, a smaller one beside it, and a mini under a match in the feed.

function crClip(c, size){
  const paper = (CR.options && CR.options.paper) || 'The Fine Leg Gazette';
  const full = size !== 'mini';
  return `<article class="np np-${size}${c.tone ? ' np-' + c.tone : ''}"${full ? '' : ' onclick="this.classList.toggle(\'open\')"'}>
    <header class="np-mast"><span>${esc(paper)}</span><span>IPL ${c.year} · ${esc(c.section)}</span></header>
    ${c.crest && size === 'lead' ? `<div class="np-photo">${crestImg(c.crest, 'np-crest')}</div>` : ''}
    <h3 class="np-head">${esc(c.headline)}</h3>
    ${c.dek ? `<p class="np-dek">${esc(c.dek)}</p>` : ''}
    <div class="np-body"><p>${c.dateline ? `<b>${esc(c.dateline)}:</b> ` : '<b>From our correspondent.</b> '}${c.body.map(esc).join(' ')}</p>
      ${c.quote ? `<p class="np-quote">${esc(c.quote)}</p>` : ''}</div>
  </article>`;
}

// A season in the papers: the biggest story, the rest beside it, the league's real news and
// the season's milestones in brief.
function crPapers(r){
  const clips = r.press || [], league = r.league || [], brief = r.milestones || [];
  if (!clips.length && !league.length && !brief.length) return '';
  return `<div class="cr-papers"><div class="cr-label">In the papers</div>
    ${clips.length ? crClip(clips[0], 'lead') : ''}
    ${clips.length > 1 ? `<div class="np-grid">${clips.slice(1).map(c => crClip(c, 'small')).join('')}</div>` : ''}
    <div class="np-cols">
      ${league.length ? `<aside class="np-side"><b>Around the league</b>${league.map(t => `<p>${esc(t)}</p>`).join('')}</aside>` : ''}
      ${brief.length ? `<aside class="np-side"><b>In brief</b>${brief.map(t => `<p>${esc(t)}</p>`).join('')}</aside>` : ''}
    </div></div>`;
}

// The career is over: where does the player go? [A228] One choice, for the last clipping.
function crEpilogue(d){
  const p = d.pending;
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="over-line">${p.year} · THE END OF THE ROAD</div>
      <div class="cr-event">
        <div class="cr-event-icon">📰</div>
        <h2 class="cr-h">Where are you now?</h2>
        <p>The last season is behind you. Years from now, the papers come looking. What do they find?</p>
        <div class="cr-event-opts">${p.epilogue.map((t, i) =>
          `<button class="cr-opt${i ? '' : ' lead'}" onclick="crMove('w${i}')"><b>${esc(t)}</b></button>`).join('')}</div>
      </div>
    </div>`;
}

function crAmbitionList(d){
  if (!d.ambitions || !d.ambitions.length) return '';
  const done = d.ambitions.filter(a => a.state === 'done').length;
  return `<div class="cr-msl cr-amb-list"><div class="cr-label">Ambitions · ${done} of ${d.ambitions.length}</div>
    ${d.ambitions.map(a => `<div class="cr-amb ${a.state}">${a.state === 'done' ? '✓' : a.state === 'broken' ? '✕' : '○'}
      ${esc(a.label)}${a.year ? `<span>${a.state === 'done' ? 'done' : 'gone'} ${a.year}</span>` : ''}</div>`).join('')}</div>`;
}

// The end of a career in the papers, and every clipping it ever made, folded away by year.
function crEndPapers(d){
  const s = d.summary, all = d.seasons.flatMap(r => r.press || []);
  return `<div class="cr-papers">
    ${s.farewell ? crClip(s.farewell, 'lead') : ''}
    ${s.epilogue ? crClip(s.epilogue, 'small') : ''}
    ${all.length ? `<details class="cr-cuttings"><summary>Press cuttings · ${all.length}</summary>
      <div class="np-grid">${all.map(c => crClip(c, 'small')).join('')}</div></details>` : ''}
  </div>`;
}

// --- the reel: a career in its headlines, one season at a time ------------------------------

function crReelItems(d){
  if (!d || !d.summary) return [];
  const items = d.seasons.filter(r => r.press && r.press.length).map(r => r.press[0]);
  if (d.summary.farewell) items.push(d.summary.farewell);
  if (d.summary.epilogue) items.push(d.summary.epilogue);
  return items;
}

const CR_REEL_MS = 4200;

function crReel(){
  const items = crReelItems(CR.data);
  if (!items.length) return;
  let box = document.getElementById('crReel');
  if (!box){
    box = document.createElement('div');
    box.id = 'crReel';
    box.className = 'cr-reel';
    document.body.appendChild(box);
  }
  CR.reel = {items, i: 0, timer: null};
  box.classList.remove('hide');
  crReelShow();
}

function crReelShow(){
  const r = CR.reel, box = document.getElementById('crReel');
  if (!r || !box) return;
  clearTimeout(r.timer);
  if (r.i >= r.items.length) return crReelClose();
  const c = r.items[r.i];
  box.innerHTML = `
    <div class="cr-reel-year">${c.year}</div>
    <div class="cr-reel-clip" onclick="crReelNext()">${crClip(c, 'lead')}</div>
    <div class="cr-reel-bar"><i style="animation-duration:${CR_REEL_MS}ms"></i></div>
    <div class="cr-reel-foot"><span>${r.i + 1} / ${r.items.length}</span>
      <button class="act" onclick="crReelNext()">Next</button>
      <button class="act" onclick="crReelClose()">Close</button></div>`;
  r.timer = setTimeout(crReelNext, CR_REEL_MS);
}

function crReelNext(){ if (CR.reel){ CR.reel.i += 1; crReelShow(); } }

function crReelClose(){
  if (CR.reel) clearTimeout(CR.reel.timer);
  CR.reel = null;
  const box = document.getElementById('crReel');
  if (box) box.classList.add('hide');
}
document.addEventListener('keydown', e => {
  if (!CR.reel) return;
  if (e.key === 'Escape') crReelClose();
  else if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); crReelNext(); }
});

// The career's milestones and injuries as one timeline, a row per year with that season's
// club [A219]. Coloured by the milestone's own kind, which the server sends (`key`), so a
// title, a cap or an injury is told apart without reading its words.
const CR_STORY_CLS = {title: 'gold', orange_cap: 'orange', purple_cap: 'purple', elite: 'elite',
                      captain: 'capt'};
function crStory(d){
  const s = d.summary, byYear = new Map();
  const add = (y, cls, text) => { if (!byYear.has(y)) byYear.set(y, []); byYear.get(y).push({cls, text}); };
  (s.milestones || []).forEach(m => add(m.year, CR_STORY_CLS[m.key] || '', m.text));
  (d.injuries || []).forEach(t => { const i = t.indexOf(' · '); add(Number(t.slice(0, i)), 'inj', t.slice(i + 3)); });
  if (!byYear.size) return '';
  const years = [...byYear.keys()].sort((a, b) => a - b);
  return `<div class="cr-story"><div class="cr-label">The story</div><ol>${years.map(y => {
    const season = d.seasons.find(r => r.year === y);
    return `<li class="${season && season.champion ? 'champ' : ''}">
      <div class="cr-story-when"><b>${y}</b>${season ? crestImg(season.crest, 'cr-story-crest') : ''}</div>
      <div class="cr-story-what">${byYear.get(y).map(e =>
        `<span class="cr-story-chip ${e.cls}">${e.cls === 'inj' ? '✚ ' : e.cls === 'gold' ? '🏆 ' : ''}${esc(e.text)}</span>`).join('')}</div>
    </li>`; }).join('')}</ol></div>`;
}

// --- the career card as an image [A219] ------------------------------------------------------
// Drawn on a canvas in the journey card's own language (reveal.js's helpers), 1080 x 1350 so
// it fits a phone screen and a chat preview. Your name, your clubs and your numbers; never
// the real players the two spins were drawn from (A199's share line keeps them back too).

// A career's rates [A220], as two short lines each: a batter's strike rate and average, then
// his best score and hundreds and fifties; a bowler's economy and average, then his strike
// rate and best figures. A discipline never practised says so rather than printing noughts.
function crBatRates(s){
  if (!s.runs && s.strike_rate == null) return ['DID NOT BAT', ''];
  const tons = [s.hundreds ? `${s.hundreds}×100` : null, s.fifties ? `${s.fifties}×50` : null].filter(Boolean);
  return [`SR ${s.strike_rate ?? '–'} · AVG ${s.average ?? '–'}`, [`HS ${s.highest}`, ...tons].join(' · ')];
}
function crBowlRates(s){
  if (s.economy == null) return ['DID NOT BOWL', ''];
  return [`ECON ${s.economy.toFixed(2)} · AVG ${s.bowling_average ?? '–'}`,
          [s.bowling_strike_rate != null ? `SR ${s.bowling_strike_rate}` : null,
           s.best_bowling ? `BB ${s.best_bowling}` : null].filter(Boolean).join(' · ')];
}

function crShowCard(){
  const d = CR.data;
  if (!d || !d.summary) return;
  $('#crCardOverlay').classList.remove('hide');
  crDrawCard(d).catch(() => slip('Could not draw the card.'));
}

function crHideCard(e){
  if (e && e.target !== e.currentTarget) return;
  $('#crCardOverlay').classList.add('hide');
}

async function crShareCard(){
  const canvas = $('#crCanvas');
  const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
  const file = blob && new File([blob], 'career-card.png', {type: 'image/png'});
  if (file && navigator.canShare && navigator.canShare({files: [file]})){
    try { await navigator.share({files: [file], text: CR.data.share}); return; }
    catch(e){ if (e && e.name === 'AbortError') return; }
  }
  $('#crCardDownload').click();
}

// The club a career is most remembered at: the crest it wore for the most seasons.
function crMainClub(seasons){
  const n = new Map();
  seasons.forEach(r => n.set(r.crest, (n.get(r.crest) || 0) + 1));
  let best = null, top = 0;
  for (const [crest, c] of n) if (c > top){ best = crest; top = c; }
  return seasons.find(r => r.crest === best) || seasons[0];
}

async function crDrawCard(d){
  if (document.fonts && document.fonts.load){
    await Promise.all([`400 80px ${CARD_DISPLAY}`, `700 30px ${CARD_BODY}`, `700 24px ${CARD_FIGS}`]
      .map(f => document.fonts.load(f).catch(() => null)));
  }
  const s = d.summary, seasons = d.seasons;
  const clubs = [];
  seasons.forEach(r => { const c = clubs.find(x => x.crest === r.crest && x.franchise === r.franchise);
    if (c) c.last = r.year; else clubs.push({crest: r.crest, franchise: r.franchise, first: r.year, last: r.year}); });
  const main = crMainClub(seasons);
  const [mainImg] = await loadImages([main && main.crest]);
  const clubImgs = await loadImages(clubs.map(c => c.crest));
  const yearImgs = await loadImages(seasons.map(r => r.crest));
  const canvas = $('#crCanvas'), ctx = canvas.getContext('2d');
  const gold = themeColor('gold'), gold2 = themeColor('gold-2'), ink = themeColor('ink'),
        ink2 = themeColor('ink-2'), ink3 = themeColor('ink-3'), mint = themeColor('mint'),
        line = themeColor('line'), lineStrong = themeColor('line-strong'), bg = themeColor('bg'),
        bg2 = themeColor('bg-2'), capOrange = themeColor('cap-orange'), capPurple = themeColor('cap-purple');
  const col = sideColours({crest: main && main.crest});
  const W = 1080, H = 1350, M = 60, inner = W - 2 * M, HEAD_H = 400;
  canvas.width = W; canvas.height = H;

  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, bg2); bgGrad.addColorStop(1, bg);
  ctx.fillStyle = bgGrad; ctx.fillRect(0, 0, W, H);

  // --- the header, in the main club's colours ---------------------------------------------
  ctx.save();
  const hg = ctx.createLinearGradient(0, 0, W, HEAD_H);
  hg.addColorStop(0, col.deep); hg.addColorStop(1, bg2);
  ctx.fillStyle = hg; ctx.fillRect(0, 0, W, HEAD_H);
  ctx.globalAlpha = .14; ctx.fillStyle = col.team;
  for (let i = -2; i < 9; i++){
    ctx.beginPath();
    ctx.moveTo(560 + i * 90, 0); ctx.lineTo(620 + i * 90, 0);
    ctx.lineTo(500 + i * 90, HEAD_H); ctx.lineTo(440 + i * 90, HEAD_H); ctx.fill();
  }
  ctx.globalAlpha = 1;
  if (mainImg){ ctx.globalAlpha = .18; drawImageFit(ctx, mainImg, W - M - 300, 40, 300); ctx.globalAlpha = 1; }
  const fade = ctx.createLinearGradient(0, HEAD_H - 120, 0, HEAD_H);
  fade.addColorStop(0, 'rgba(0,0,0,0)'); fade.addColorStop(1, bg2);
  ctx.fillStyle = fade; ctx.fillRect(0, HEAD_H - 120, W, 120);
  ctx.restore();
  ctx.fillStyle = col.team; ctx.fillRect(0, 0, W, 10);

  // the peak Rating, as the player card's badge
  const peak = s.peak_rating || s.peak_card, bx = M, by = 92, bw = 190, bh = 210;
  const tier = crTier(peak);
  ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 26);
  if (tier === 'elite'){
    const eg = ctx.createLinearGradient(bx, by, bx + bw, by + bh);
    eg.addColorStop(0, '#ffd77a'); eg.addColorStop(1, gold);
    ctx.fillStyle = eg; ctx.fill();
  } else { ctx.fillStyle = 'rgba(255,255,255,.04)'; ctx.fill(); }
  ctx.lineWidth = 5; ctx.strokeStyle = tier === 'base' ? lineStrong : tier === 'good' ? mint : gold; ctx.stroke();
  ctx.textAlign = 'center';
  ctx.fillStyle = tier === 'elite' ? '#1a1204' : tier === 'star' ? gold2 : ink;
  ctx.font = `400 116px ${CARD_DISPLAY}`;
  ctx.fillText(String(peak), bx + bw / 2, by + 140);
  ctx.font = `700 18px ${CARD_FIGS}`;
  ctx.fillStyle = tier === 'elite' ? '#3a2a06' : ink3;
  ctx.fillText('PEAK RATING', bx + bw / 2, by + 182);

  const tx = bx + bw + 40, tw = W - M - tx;
  const ending = s.ending === 'retired' ? 'RETIRED' : s.ending === 'released' ? 'RELEASED' : 'PLAYED TO 2026';
  ctx.textAlign = 'left';
  ctx.fillStyle = col.ink; ctx.font = `700 22px ${CARD_FIGS}`;
  ctx.fillText(`BUILD A CAREER · ${d.role_label.toUpperCase()}`, tx, 122);
  ctx.fillStyle = ink;
  ctx.fillText(fitText(ctx, d.name.toUpperCase(), tw, 400, 88, CARD_DISPLAY, 40), tx, 214);
  ctx.fillStyle = ink2; ctx.font = `600 24px ${CARD_FIGS}`;
  ctx.fillText(fitText(ctx, `${d.debut}–${d.debut + s.seasons - 1} · ${s.seasons} SEASONS · ${ending}`, tw, 600, 24, CARD_FIGS, 16), tx, 262);
  const ranks = [s.runs_rank && s.runs_rank <= 25 ? `${cardOrdinal(s.runs_rank)} ALL-TIME FOR RUNS` : null,
                 s.wickets_rank && s.wickets_rank <= 25 ? `${cardOrdinal(s.wickets_rank)} ALL-TIME FOR WICKETS` : null].filter(Boolean);
  if (ranks.length){
    ctx.fillStyle = gold2; ctx.font = `400 34px ${CARD_DISPLAY}`;
    ctx.fillText(fitText(ctx, ranks.join(' · '), tw, 400, 34, CARD_DISPLAY, 20), tx, 312);
  }

  // --- four tiles, your own discipline first, each with its rates under it [A220] ----------
  let y = HEAD_H + 10;
  const bat = [crNum(s.runs), 'RUNS', ...crBatRates(s)], bowl = [String(s.wickets), 'WICKETS', ...crBowlRates(s)];
  const tiles = [...(crBowls(d) ? [bowl, bat] : [bat, bowl]),
    [String(s.matches), 'MATCHES', `${s.regular_seasons} OF ${s.seasons} SEASONS`, 'AS A REGULAR'],
    [String(s.titles), s.titles === 1 ? 'TITLE' : 'TITLES', s.finals ? `${s.finals} FINAL${s.finals === 1 ? '' : 'S'}` : 'NO FINALS', '']];
  const TILE_H = 214, tg = 18, tw4 = (inner - tg * 3) / 4;
  tiles.forEach(([val, label, rate1, rate2], i) => {
    const x = M + i * (tw4 + tg);
    ctx.fillStyle = 'rgba(255,255,255,.035)';
    ctx.beginPath(); ctx.roundRect(x, y, tw4, TILE_H, 14); ctx.fill();
    ctx.strokeStyle = line; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = col.team; ctx.fillRect(x + 22, y + 22, 34, 4);
    ctx.fillStyle = label.startsWith('TITLE') && s.titles ? gold2 : ink;
    ctx.fillText(fitText(ctx, val, tw4 - 44, 400, 66, CARD_DISPLAY, 30), x + 22, y + 106);
    ctx.fillStyle = ink3; ctx.font = `700 17px ${CARD_FIGS}`;
    ctx.fillText(label, x + 22, y + 138);
    ctx.fillStyle = ink2;
    [rate1, rate2].forEach((t, k) => { if (t) ctx.fillText(fitText(ctx, t, tw4 - 44, 600, 17, CARD_FIGS, 11), x + 22, y + 170 + 24 * k); });
  });

  // --- honours, one line --------------------------------------------------------------------
  y += TILE_H + 56;
  const honours = [];
  if (s.orange_caps) honours.push([`ORANGE CAP ×${s.orange_caps}`, capOrange]);
  if (s.purple_caps) honours.push([`PURPLE CAP ×${s.purple_caps}`, capPurple]);
  honours.push([`${s.earnings.replace('₹', '₹ ')} EARNED`, mint]);
  ctx.font = `700 22px ${CARD_FIGS}`;
  let hx = M;
  honours.forEach(([text, c]) => {
    const w = ctx.measureText(text).width + 36;
    ctx.strokeStyle = c; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.roundRect(hx, y - 30, w, 44, 22); ctx.stroke();
    ctx.fillStyle = c; ctx.fillText(text, hx + 18, y);
    hx += w + 14;
  });

  // --- the career path: a bar per season, its crest and year under it ------------------------
  y += 70;
  ctx.fillStyle = ink; ctx.font = `400 36px ${CARD_DISPLAY}`;
  ctx.fillText('THE CAREER PATH', M, y);
  ctx.textAlign = 'right'; ctx.fillStyle = ink3; ctx.font = `700 16px ${CARD_FIGS}`;
  ctx.fillText('RATING EACH SEASON · 🏆 A TITLE', W - M, y);
  ctx.textAlign = 'left';
  const plotTop = y + 30, plotH = 196, n = seasons.length;
  const slot = inner / Math.max(n, 8), barW = Math.min(46, slot * .62);
  const yOf = v => plotTop + plotH - plotH * Math.max(0, Math.min(1, (v - 55) / (99 - 55)));
  [70, 80, 90].forEach(g => {
    ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(M, yOf(g)); ctx.lineTo(W - M, yOf(g)); ctx.stroke();
  });
  seasons.forEach((r, i) => {
    const rt = r.rating || r.card, cx = M + slot * i + slot / 2, top = yOf(rt);
    const t = crTier(rt);
    ctx.fillStyle = t === 'elite' ? gold : t === 'star' ? 'rgba(245,184,61,.75)' : t === 'good' ? 'rgba(52,211,153,.6)' : 'rgba(52,211,153,.32)';
    ctx.globalAlpha = r.squad ? .45 : 1;
    ctx.beginPath(); ctx.roundRect(cx - barW / 2, top, barW, plotTop + plotH - top, [8, 8, 2, 2]); ctx.fill();
    ctx.globalAlpha = 1;
    ctx.textAlign = 'center';
    ctx.fillStyle = t === 'elite' || t === 'star' ? '#1a1204' : '#04150d';
    if (plotTop + plotH - top > 34){ ctx.font = `700 ${barW > 36 ? 18 : 14}px ${CARD_FIGS}`; ctx.fillText(String(rt), cx, top + 24); }
    if (r.champion){ ctx.font = `400 24px ${CARD_BODY}`; ctx.fillText('🏆', cx, top - 8); }
    if (yearImgs[i]) drawImageFit(ctx, yearImgs[i], cx - 16, plotTop + plotH + 10, 32);
    ctx.fillStyle = r.champion ? gold2 : ink3; ctx.font = `700 16px ${CARD_FIGS}`;
    ctx.fillText(String(r.year).slice(2), cx, plotTop + plotH + 66);
  });
  ctx.textAlign = 'left';

  // --- the clubs ------------------------------------------------------------------------------
  y = plotTop + plotH + 100;
  // Three to a row, two rows: up to six, and a longer list keeps five and says how many
  // more rather than dropping them.
  const shown = clubs.length > 6 ? clubs.slice(0, 5) : clubs;
  const cw = inner / 3, rowH = 74;
  if (clubs.length > 6){
    ctx.fillStyle = ink2; ctx.font = `400 30px ${CARD_DISPLAY}`;
    ctx.fillText(`+${clubs.length - 5} MORE`, M + 2 * cw, y + rowH + 38);
  }
  shown.forEach((c, i) => {
    const x = M + (i % 3) * cw, y0 = y + Math.floor(i / 3) * rowH;
    if (clubImgs[i]) drawImageFit(ctx, clubImgs[i], x, y0, 54);
    ctx.fillStyle = ink; ctx.font = `700 21px ${CARD_BODY}`;
    ctx.fillText(fitText(ctx, c.franchise, cw - 80, 700, 21, CARD_BODY, 14), x + 66, y0 + 24);
    ctx.fillStyle = ink3; ctx.font = `600 16px ${CARD_FIGS}`;
    ctx.fillText(c.first === c.last ? String(c.first) : `${c.first}–${String(c.last).slice(2)}`, x + 66, y0 + 48);
  });

  // --- footer and rim --------------------------------------------------------------------------
  const fy = H - 46;
  ctx.strokeStyle = lineStrong; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(M, fy - 40); ctx.lineTo(W - M, fy - 40); ctx.stroke();
  ctx.fillStyle = ink; ctx.font = `400 30px ${CARD_DISPLAY}`;
  ctx.fillText('FINE LEG', M, fy);
  const lw = ctx.measureText('FINE LEG ').width;
  ctx.fillStyle = gold; ctx.fillText('XI', M + lw, fy);
  ctx.textAlign = 'right'; ctx.fillStyle = ink3; ctx.font = `700 18px ${CARD_FIGS}`;
  ctx.fillText('BUILD A CAREER', W - M, fy - 4);
  ctx.textAlign = 'left';
  ctx.strokeStyle = col.team; ctx.lineWidth = 6; ctx.strokeRect(3, 3, W - 6, H - 6);

  $('#crCardDownload').href = canvas.toDataURL('image/png');
}

// A finished career goes into the Hall of Fame on your profile [A210]: saved for a signed-in
// player as soon as it ends (saving one twice is a no-op), or offered to somebody signed out.
async function crKeep(d){
  const box = document.getElementById('crKeep');
  if (!box) return;
  try { await CR.me; } catch(e){}
  if (!ME || !ME.account_id){
    box.innerHTML = `<span>Sign in to keep this career in your Hall of Fame.</span>
      <button class="act" onclick="openAuthModal('login')">Sign in</button>`;
    return;
  }
  box.innerHTML = '<span>Saving to your Hall of Fame…</span>';
  try {
    const r = await api('/api/career/save', {method: 'POST', headers: CR_JSON, retry: true,
                                             body: JSON.stringify({token: d.token})});
    box.innerHTML = `<span>★ In your Hall of Fame · ${r.careers} career${r.careers === 1 ? '' : 's'}</span>
      <a class="act" href="/profile#hall">See them</a>`;
  } catch(e){
    box.innerHTML = `<span>Could not save it just now.</span>
      <button class="act" onclick="crKeep(CR.data)">Try again</button>`;
  }
}
document.addEventListener('signedin', () => { if (CR.data && CR.data.summary) crKeep(CR.data); });

function crNew(){
  crSave({token: null});
  crShowCreate();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

// --- boot ----------------------------------------------------------------------------------

(async function crBoot(){
  if (typeof restoreOverSpeed === 'function') restoreOverSpeed();
  CR.me = loadMe();                    // who you are decides whether a finished career is saved
  loadMeta().then(renderDeckStats).catch(() => {});
  try { CR.options = await api('/api/career/options'); }
  catch(e){ slip('Could not load the career page. Try again.'); return; }
  const token = crStore().token;
  if (token){
    try {
      const d = await api('/api/career/view', {method: 'POST', headers: CR_JSON, retry: true, body: JSON.stringify({token})});
      return crShow(d, null);
    } catch(e){
      if (e.status && e.status < 500) crSave({token: null});      // a token that no longer reads
    }
  }
  crShowCreate();
})();
