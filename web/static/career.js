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

function crPick(group, btn){
  document.querySelectorAll(`#${group} .room-choice`).forEach(b => b.classList.toggle('sel', b === btn));
}
const crChosen = group => (document.querySelector(`#${group} .room-choice.sel`) || {}).dataset;

// --- create --------------------------------------------------------------------------------

function crShowCreate(){
  CR.data = null;
  $('#crMain').classList.add('hide');
  $('#crCreate').classList.remove('hide');
  const o = CR.options;
  $('#crRoles').innerHTML = o.roles.map((r, i) =>
    `<button class="room-choice${i ? '' : ' sel'}" data-v="${r.key}" onclick="crPick('crRoles', this)">${esc(r.label)}</button>`).join('');
  const years = [];
  for (let y = o.first_debut; y <= o.last_debut; y++) years.push(y);
  $('#crDebut').innerHTML = years.map(y => `<option value="${y}"${y === 2012 ? ' selected' : ''}>${y}</option>`).join('');
}

async function crStart(btn){
  if (CR.busy) return;
  const name = $('#crName').value.trim();
  if (!name){ slip('Give your player a name.'); $('#crName').focus(); return; }
  CR.busy = true; btn.disabled = true;
  try {
    const d = await api('/api/career/start', {method: 'POST', headers: CR_JSON, retry: true, body: JSON.stringify({
      name, role: crChosen('crRoles').v, debut: Number($('#crDebut').value),
      overseas: crChosen('crPassport').v === '1'})});
    crSave({token: d.token});
    CR.seen = 0;
    crShow(d, 'spin');
  } catch(e){ slip(e.message || 'Could not start a career.'); }
  finally { CR.busy = false; btn.disabled = false; }
}

// --- one move ------------------------------------------------------------------------------

async function crMove(move){
  if (CR.busy) return;
  CR.busy = true;
  const before = CR.data.seasons.length;
  if (move !== 'r') crLoading(`Playing ${CR.data.pending.year}…`);
  try {
    const d = await api('/api/career/move', {method: 'POST', headers: CR_JSON, body: JSON.stringify({token: CR.data.token, move})});
    crSave({token: d.token});
    crShow(d, d.seasons.length > before ? 'season' : null);
  } catch(e){
    slip(e.message || 'That did not go through. Try again.');
    crShow(CR.data, null);
  } finally { CR.busy = false; }
}

function crLoading(text){
  $('#crStage').innerHTML = `<div class="cr-loading"><div class="cr-ball"></div><b>${esc(text)}</b>
    <span>Every match of the season, ball by ball.</span></div>`;
}

// --- drawing -------------------------------------------------------------------------------

function crShow(d, moment){
  CR.data = d;
  $('#crCreate').classList.add('hide');
  $('#crMain').classList.remove('hide');
  crHead(d);
  crTimeline(d);
  if (moment === 'spin') return crSpin(d);
  if (moment === 'season') return crSeason(d, d.seasons[d.seasons.length - 1]);
  if (!d.pending) return crEnd(d);
  return d.pending.kind === 'offer' ? crOffers(d) : crEvent(d);
}

function crHead(d){
  const s = d.seasons, last = s[s.length - 1];
  $('#crHead').innerHTML = `
    <div class="cr-who"><b>${esc(d.name)}</b><span>${esc(d.role_label)}${d.overseas ? ' · overseas' : ''} · debut ${d.debut}</span></div>
    <div class="cr-tot">${s.length ? `<span><b>${s.length}</b> season${s.length === 1 ? '' : 's'}</span>
      <span><b>${crNum(s.reduce((a, r) => a + r.runs, 0))}</b> runs</span>
      <span><b>${s.reduce((a, r) => a + r.wickets, 0)}</b> wkts</span>
      <span><b>${s.filter(r => r.champion).length}</b> titles</span>
      <span>card <b>${last.card}</b></span>` : ''}</div>`;
}

function crTimeline(d){
  $('#crTimeline').innerHTML = d.seasons.map(r => `
    <div class="cr-tl${r.champion ? ' champ' : ''}${r.squad ? ' squad' : ''}" title="${r.year} ${esc(r.franchise)}">
      ${crestImg(r.crest, 'cr-tl-crest')}<b>${r.card}</b><span>${String(r.year).slice(2)}</span>
    </div>`).join('');
}

function crTemplateCard(label, t, cls){
  return `<div class="cr-spin-card ${cls}">
    <div class="cr-spin-label">${label}</div>
    ${crestImg(t.crest, 'cr-spin-crest')}
    <b class="cr-roll" data-final="${esc(t.name)}">${esc(t.name)}</b>
    <span>${t.year} · ${esc(t.franchise)}</span>
  </div>`;
}

function crSpin(d){
  $('#crStage').innerHTML = `
    <div class="cr-spin">
      ${crTemplateCard('You start like', d.start, 'start')}
      ${crTemplateCard('Your ceiling', d.ceiling, 'ceiling')}
    </div>
    <p class="room-note">Your ability moves from the first toward the second as your career
      grows, then falls away. How you play each season is down to the matches.</p>
    <div class="foot-actions" style="justify-content:center">
      <button class="act lead" onclick="crShow(CR.data, null)">See who wants you</button>
    </div>`;
  crRoll();
}

// A short decelerating flicker of real names before the spin settles, like the deal's
// (rollDeal): the result is already fixed by the server, this only reveals it.
function crRoll(){
  const els = [...document.querySelectorAll('.cr-roll')];
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const pool = ['V Kohli', 'MS Dhoni', 'JJ Bumrah', 'SP Narine', 'AB de Villiers', 'R Ashwin',
                'CH Gayle', 'SL Malinga', 'RG Sharma', 'Rashid Khan', 'AD Russell', 'DA Warner'];
  els.forEach((el, n) => {
    let delay = 40, steps = 0;
    const final = el.dataset.final;
    (function tick(){
      if (steps++ > 14 + 6 * n){ el.textContent = final; el.classList.add('settled'); return; }
      el.textContent = pool[Math.floor(Math.random() * pool.length)];
      delay *= 1.16;
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
  const strength = o => o.strength == null ? '' : `<span class="cr-strength">Squad <b>${o.strength}</b></span>`;
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
        <div class="cr-offer${o.stay ? ' stay' : ''}">
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

function crRetire(){
  if (!confirm('Retire now? Your career ends here.')) return;
  crMove('r');
}

function crEvent(d){
  const p = d.pending, e = p.event, o = p.offers[0];
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="over-line">${p.year} · ${esc(o.franchise).toUpperCase()}</div>
      <div class="cr-event">
        <h2 class="cr-h">${esc(e.title)}</h2>
        <p>${esc(e.text)}</p>
        <div class="cr-event-opts">${e.options.map((o, i) =>
          `<div class="cr-opt"><button class="act${i ? '' : ' lead'}" onclick="crMove('e${i}')">${esc(o.label)}</button>
            <span>${esc(o.hint)}</span></div>`).join('')}</div>
      </div>
    </div>`;
}

function crBadge(on, cls, text){ return on ? `<span class="cr-badge ${cls}">${text}</span>` : ''; }

function crBat(r){
  if (!r.balls) return '';
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
  const next = d.pending ? `<button class="act lead" onclick="crShow(CR.data, null)">Next season</button>`
                         : `<button class="act lead" onclick="crEnd(CR.data)">See your career</button>`;
  $('#crStage').innerHTML = `
    <div class="cr-step">
      <div class="cr-season${r.champion ? ' champ' : ''}">
        <div class="cr-season-top">
          ${crestImg(r.crest, 'cr-season-crest')}
          <div><div class="over-line">${r.year} · ${esc(r.franchise).toUpperCase()}</div>
            <div class="cr-finish">${finish}</div></div>
          <div class="cr-card${r.card >= 90 ? ' elite' : ''}"><b>${r.card}</b><span>season card</span></div>
        </div>
        <div class="cr-badges">
          ${crBadge(r.champion, 'gold', '🏆 Champions')}
          ${crBadge(r.orange_cap, 'orange', 'Orange Cap')}
          ${crBadge(r.purple_cap, 'purple', 'Purple Cap')}
          ${crBadge(r.captain, '', 'Captain')}
          ${crBadge(r.contract_year > 0, '', `Contract year ${r.contract_year} of 3`)}
          ${crBadge(r.squad, 'dim', 'Squad player')}
          ${crBadge(r.missed > 0, 'dim', `Missed ${r.missed} match${r.missed === 1 ? '' : 'es'}`)}
          ${crBadge(r.card > r.form, 'dim', `Form ${r.form}, reputation held you at ${r.card}`)}
        </div>
        <div class="cr-lines">
          <div><b>${r.matches}</b> matches<span>${esc(r.price)}${r.stayed ? ' · stayed' : ''}</span></div>
          ${crBat(r)}${crBowl(r)}
        </div>
        ${r.highlights.length ? `<div class="cr-hl"><div class="cr-label">Highlights</div>
          ${r.highlights.map(h => `<div><span>${esc(h.stage === 'league' ? 'League' : h.stage)} v ${esc(h.opponent)}</span><b>${esc(h.text)}</b></div>`).join('')}</div>` : ''}
      </div>
      ${!d.pending && d.summary ? `<p class="cr-warn">${d.summary.ending === 'released'
        ? 'Two poor seasons running, and no franchise came for you.' : 'That was the last season.'}</p>` : ''}
      <div class="foot-actions" style="justify-content:center">${next}</div>
    </div>`;
}

function crRank(n, what){
  if (n == null) return '';
  return n <= 25 ? `<div class="cr-rank"><b>${crOrd(n)}</b> all-time for ${what}</div>` : '';
}

function crEnd(d){
  const s = d.summary;
  if (!s) return;
  const bat = s.runs ? `<div><b>${crNum(s.runs)}</b> runs<span>SR ${s.strike_rate ?? '—'} · avg ${s.average ?? '—'} · HS ${s.highest} · ${s.hundreds}×100 · ${s.fifties}×50</span></div>` : '';
  const bowl = s.wickets ? `<div><b>${s.wickets}</b> wickets<span>econ ${s.economy ?? '—'}</span></div>` : '';
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
          ${bat}${bowl}
          <div><b>${s.titles}</b> title${s.titles === 1 ? '' : 's'}<span>${s.finals} final${s.finals === 1 ? '' : 's'} · ${s.orange_caps} Orange, ${s.purple_caps} Purple Cap${s.orange_caps + s.purple_caps === 1 ? '' : 's'}</span></div>
          <div><b>${s.peak_card}</b> peak card<span>${s.peak_year} · earned ${esc(s.earnings)}</span></div>
        </div>
      </div>
      <pre class="bg-share-preview">${esc(d.share)}</pre>
      <div class="foot-actions" style="justify-content:center">
        <button class="act lead" onclick="pzShare(CR.data.share, 'Career copied. No names in it.')">Share your career</button>
        <button class="act" onclick="crNew()">Start a new career</button>
      </div>
    </div>`;
}

function crNew(){
  crSave({token: null});
  crShowCreate();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

// --- boot ----------------------------------------------------------------------------------

(async function crBoot(){
  loadMe();
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
