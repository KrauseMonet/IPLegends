// The auction floor. [A136]
//
// The whole auction lives in the server's state string (web/auction_session.py) and the
// URL hash carries it, exactly like the draft. This file only draws and animates: every
// price, every rule and every computer bid comes back from the server, and the computer
// teams' limits never do -- the page sees the bidding, as a real auction room would.
//
// Two bidding styles over one engine: LIVE raises one step at a time against a countdown,
// LIMIT names a maximum and lets the auctioneer bid up to it. The server treats them
// identically (a clicker's last bid is exactly a maximum), so switching mid-auction is safe.

// [A139] The same floor serves an auction ROOM: room.html sets window.AUCTION_ROOM before
// loading this file, and room.js supplies `roomAuctionPost` (the network) and
// `roomAuctionDeadline` (the server's clock). The differences are all here, behind this flag:
// a room's moves go to /api/rooms/{code}/auction/*, its countdown is the server's deadline
// and never passes a lot for you, and a room's floor waits on other people.
const AUCTION_ROOM = !!window.AUCTION_ROOM;

// Ratings are always shown on the auction floor. On the room page room.js already defines
// `effectiveDraftMode` for the draft, so this one is only installed where it is alone.
if (!AUCTION_ROOM) window.effectiveDraftMode = () => 'stat';

const TEAM_COLOURS = {
  CSK: ['#f9cd05', '#1a1204'], MI: ['#1b5fc1', '#fff'], RCB: ['#d11d26', '#fff'],
  KKR: ['#4b2d7f', '#f5c24c'], RR: ['#ea1a85', '#fff'], DC: ['#2561c6', '#fff'],
  SRH: ['#f26522', '#1a0c02'], PBKS: ['#dd1f2d', '#fff'], GT: ['#1c2c5b', '#9fd4f2'],
  LSG: ['#a72056', '#fff'],
};
const FRANCHISES = [
  ['CSK', 'Chennai Super Kings'], ['MI', 'Mumbai Indians'],
  ['RCB', 'Royal Challengers Bengaluru'], ['KKR', 'Kolkata Knight Riders'],
  ['RR', 'Rajasthan Royals'], ['DC', 'Delhi Capitals'], ['SRH', 'Sunrisers Hyderabad'],
  ['PBKS', 'Punjab Kings'], ['GT', 'Gujarat Titans'], ['LSG', 'Lucknow Super Giants'],
];
const LIVE_SECONDS = 8;
const STYLE_KEY = 'iplegends_auction_style';
const FORMAT_KEY = 'iplegends_auction_format';

let A = null;              // the last AuctionOut drawn
let CHOSEN_TEAM = null;
let BID_STYLE = 'live';
let FORMAT = 'mega';       // 'mega' = retentions and Right to Match [A138]; 'open' = none
let RETAIN = [];           // pool indexes picked, in the order picked (the slab order)
let RETAIN_SEASON = {};    // person_id -> the pool index of the season showing for him
let LIMIT = null;          // the limit being composed, in lakh
let BUSY = false;
let COUNTDOWN = null;
let TWELVE = null;         // [11 order indexes..., impact index] being edited
let TWELVE_SEL = null;     // position in TWELVE the user tapped, awaiting a swap

try { BID_STYLE = localStorage.getItem(STYLE_KEY) || 'live'; } catch(e) {}
try { FORMAT = localStorage.getItem(FORMAT_KEY) || 'mega'; } catch(e) {}

// --- money -----------------------------------------------------------------------------

function cr(lakh){
  if (lakh == null) return '—';
  return lakh >= 100 ? `₹${(lakh / 100).toFixed(2).replace(/\.?0+$/, '')} cr` : `₹${lakh}L`;
}
function increment(p){ return p < 100 ? 5 : p < 200 ? 10 : p < 300 ? 20 : 25; }
function stepUp(p){ return p + increment(p); }
function stepDown(p, floor){
  // the largest ladder price below p, never under floor
  let q = floor;
  while (stepUp(q) < p) q = stepUp(q);
  return q;
}

function chip(short, big){
  const [bg, fg] = TEAM_COLOURS[short] || ['#334', '#fff'];
  return `<span class="auc-chip${big ? ' big' : ''}" style="background:${bg};color:${fg}">${short}</span>`;
}

// --- setup -------------------------------------------------------------------------------

function renderSetup(){
  $('#aucFranchises').innerHTML = FRANCHISES.map(([s, name]) => {
    const [bg, fg] = TEAM_COLOURS[s];
    return `<button class="auc-fr" data-team="${s}" onclick="chooseTeam('${s}')"
      style="--fr:${bg};--fr-ink:${fg}"><b>${s}</b><span>${name}</span></button>`;
  }).join('');
  setBidStyle(BID_STYLE);
  setFormat(FORMAT);
  show('aucSetup');
}

function chooseTeam(s){
  CHOSEN_TEAM = s;
  document.querySelectorAll('.auc-fr').forEach(b => b.classList.toggle('sel', b.dataset.team === s));
  $('#aucStartBtn').disabled = false;
}

function setBidStyle(style){
  BID_STYLE = style;
  try { localStorage.setItem(STYLE_KEY, style); } catch(e) {}
  // The setup screen's choice and the floor's own switch are the same setting.
  document.querySelectorAll('.room-choice[data-style]').forEach(b =>
    b.classList.toggle('sel', b.dataset.style === style));
  if (A && A.phase === 'bid') renderBidControls();
}

function setFormat(format){
  FORMAT = format;
  try { localStorage.setItem(FORMAT_KEY, format); } catch(e) {}
  document.querySelectorAll('#aucFormatChoices .room-choice').forEach(b =>
    b.classList.toggle('sel', b.dataset.format === format));
}

function startAuction(ctrl){
  if (!CHOSEN_TEAM) return;
  busyClick(ctrl, 'Opening the room…', async () => {
    const d = await api('/api/auction', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                         body: JSON.stringify({team: CHOSEN_TEAM, mega: FORMAT === 'mega'})});
    await apply(d, false);
  });
}

// --- screens -------------------------------------------------------------------------------

function show(id){
  // A room page carries only the floor, the fill round, the twelve and the wait panel.
  ['aucSetup', 'aucRetain', 'aucFloor', 'aucFill', 'aucTwelve', 'aucWait'].forEach(s => {
    const el = $('#' + s);
    if (el) el.classList.toggle('hide', s !== id);
  });
}

async function apply(d, animate){
  const prev = A;
  stopCountdown();
  // The address bar carries the single-player auction's state. A room's URL is the room.
  if (!AUCTION_ROOM) history.replaceState(null, '', '#' + d.state);
  closeRtm();
  if (animate && prev && prev.phase === 'bid') await animateFrom(prev, d);
  else if (animate && prev && (isRtm(prev.phase) || prev.phase === 'rtm_watch'))
    await stampAfterRtm(prev, d);
  A = d;
  // [A141] A legend you claimed but another person's franchise had the better season of.
  if (d.retention_lost && d.retention_lost.length && !(prev && prev.retention_lost
      && prev.retention_lost.length)) d.retention_lost.forEach(m => slip(m));
  if (d.phase === 'retain'){ renderRetain(); show('aucRetain'); }
  else if (d.phase === 'bid' || isRtm(d.phase) || d.phase === 'rtm_watch'){
    const entering = $('#aucFloor').classList.contains('hide');
    show('aucFloor'); renderFloor();
    // On a phone the masthead fills the first screen; bring the lot itself into view.
    if (entering && STACKED_DRAFT.matches) $('.auc-lotbar').scrollIntoView({block: 'start'});
    if (isRtm(d.phase)) openRtm();
  }
  else if (d.phase === 'fill'){ show('aucFill'); renderFill(); }
  else if (d.phase === 'wait' || d.phase === 'complete'){ renderWait(); show('aucWait'); }
  else { renderTwelve(); show('aucTwelve'); }
}

// --- the animation between two server responses ----------------------------------------------
//
// Only what changed is played: the new bids on the lot you are in, and if that lot has
// just been decided, its hammer. Lots decided without you (a set you skipped, a player you
// could not buy) go straight into the feed rather than making you watch them.

function saleKey(s){ return s.round + ':' + s.lot; }
// Your own franchise's row. Not "the human team": in a room several teams are people.
function yourTeam(d){ return d.teams.find(t => t.short === d.you) || d.teams.find(t => t.human); }
function isRtm(phase){ return phase === 'rtm_use' || phase === 'rtm_match' || phase === 'rtm_raise'; }

// Once a Right to Match question is answered the lot is decided: show its hammer, which
// now carries who ended up with the player, without replaying bidding already watched.
// A lot still being decided (use -> match) is not in `recent` yet, so nothing stamps early.
async function stampAfterRtm(prev, next){
  const sale = next.recent.find(s => s.lot === prev.lot.lot && s.round === prev.lot.round);
  if (sale) await stamp(sale);
}

async function animateFrom(prev, next){
  const lot = prev.lot;
  const sameLot = next.lot && next.lot.lot === lot.lot && next.lot.round === lot.round;
  const stillOpen = next.phase === 'bid' && sameLot;
  let bids, sale = null;
  if ((isRtm(next.phase) || next.phase === 'rtm_watch') && sameLot){
    // The hammer came down and a Right to Match question followed: play the bidding that
    // got there before the question opens, rather than jumping straight to the price.
    bids = next.bids;
  } else if (stillOpen){
    bids = next.bids;
  } else {
    sale = next.recent.find(s => s.lot === lot.lot && s.round === lot.round);
    bids = sale ? sale.bids : [];
  }
  const fresh = bids.slice(prev.bids.length);
  await playBids(fresh);
  if (sale) await stamp(sale);
}

function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

async function playBids(bids){
  if (!bids.length) return;
  // A lot averages ~30 bids: the whole exchange plays in about two seconds however long
  // it is, a quick patter for a long war and a readable beat for a short one.
  const step = Math.max(55, Math.min(320, 2200 / bids.length));
  $('#aucPriceLabel').textContent = 'Current bid';
  for (const b of bids){
    setPrice(b.price);
    setLeader(b.team);
    await sleep(step);
  }
}

function setPrice(p){
  const el = $('#aucPrice');
  el.textContent = cr(p);
  el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump');
}

function setLeader(short){
  const el = $('#aucLeader');
  if (!short){ el.innerHTML = ''; return; }
  const you = A && short === A.you;
  el.innerHTML = `${chip(short, true)}<span>${you ? 'You lead' : 'Leading'}</span>`;
  el.classList.toggle('you', !!you);
}

async function stamp(sale){
  const el = $('#aucStamp');
  if (sale.team){
    const [bg] = TEAM_COLOURS[sale.team] || ['#fff'];
    const rtm = sale.rtm_holder
      ? `<em>${sale.rtm_matched ? 'Right to Match · ' + sale.rtm_holder + ' took him back'
                                 : sale.rtm_holder + ' played a card and did not match'}</em>` : '';
    el.innerHTML = `<b>SOLD</b><span>${chip(sale.team)} ${cr(sale.price)}</span>${rtm}`;
    el.style.setProperty('--stamp', bg);
    el.classList.remove('unsold');
  } else {
    el.innerHTML = '<b>UNSOLD</b>';
    el.style.setProperty('--stamp', '#8a96b0');
    el.classList.add('unsold');
  }
  el.classList.remove('hide', 'go'); void el.offsetWidth; el.classList.add('go');
  await sleep(sale.team === (A && A.you) ? 1700 : 1150);
  el.classList.add('hide');
}

// --- the floor ---------------------------------------------------------------------------

function renderFloor(){
  const d = A, lot = d.lot, c = lot.card;
  $('#aucSetLabel').textContent = lot.round === 'accelerated' ? 'Accelerated round · the unsold, again'
                                                               : lot.set_label;
  $('#aucLotNo').textContent = `Lot ${lot.lot + 1} of ${lot.lots_total}`;

  const tag = c.overseas === true ? '<span class="auc-tag os">Overseas</span>'
            : c.overseas === false ? '<span class="auc-tag">India</span>' : '';
  $('#aucCard').innerHTML = `
    <div class="auc-card-top">
      <div>
        <div class="auc-card-season">${[c.franchise, c.season_year].filter(Boolean).join(' · ')}</div>
        <div class="auc-card-name">${c.name}</div>
        <div class="auc-card-meta">${ICON[c.kind] || ''}${keeperBadge(c)}
          <span>${roleLabel(c)}</span>${tag}</div>
      </div>
      <div class="auc-card-rating">${ratingBadge(c, true)}</div>
    </div>
    <div class="colophon stat-tiles auc-stats">${cardStatHtml(c)}</div>
    <div class="auc-card-base">Base price <b>${cr(lot.base)}</b></div>`;

  if (isRtm(d.phase) || d.phase === 'rtm_watch'){
    $('#aucPriceLabel').textContent = 'Hammer';
    $('#aucPrice').textContent = cr(d.rtm.price);
    setLeader(d.rtm.kind === 'rtm_raise' ? d.rtm.deciding || d.you : d.rtm.other);
  } else if (d.bids.length){
    $('#aucPriceLabel').textContent = 'Current bid';
    $('#aucPrice').textContent = cr(d.price);
    setLeader(d.leader);
  } else {
    $('#aucPriceLabel').textContent = 'Base price';
    $('#aucPrice').textContent = cr(lot.base);
    setLeader(null);
  }

  $('#aucUpNext').innerHTML = lot.upcoming.length
    ? `<span>Still to come in this set</span> ${lot.upcoming.slice(0, 8).join(' · ')}${lot.upcoming.length > 8 ? ' …' : ''}`
    : '';
  renderSide();
  if (d.phase === 'bid') renderBidControls();
  else {
    stopCountdown();
    $('#aucNote').textContent = d.phase === 'rtm_watch'
      ? `${d.rtm.deciding} are deciding on a Right to Match…` : '';
    // In a room every decision runs on the server's clock; show it.
    if (AUCTION_ROOM && (d.phase === 'rtm_watch' || isRtm(d.phase))) startRoomCountdown();
  }
}

function roleLabel(c){
  const band = {top: 'top order', middle: 'middle order', finisher: 'finisher', tail: 'lower order'};
  const pos = c.positions && c.positions.length ? band[bandOf(c)] : '';
  const kind = {batter: 'Batter', bowler: 'Bowler', allrounder: 'All-rounder', keeper: 'Wicketkeeper'}[c.kind] || '';
  return [kind, pos].filter(Boolean).join(' · ');
}
function bandOf(c){
  const lo = Math.min(...c.positions);
  return lo === 1 ? 'top' : lo === 3 ? 'middle' : lo === 5 ? 'finisher' : 'tail';
}

function renderSide(){
  const d = A;
  const you = yourTeam(d);
  $('#aucYouShort').innerHTML = chip(d.you);
  $('#aucPurse').textContent = cr(you.purse);
  const open = d.squad_size - you.players;
  const reserve = you.purse - d.max_bid;
  $('#aucReserve').textContent = open > 1
    ? `Must keep ${cr(reserve)} for ${open - 1} more place${open - 1 === 1 ? '' : 's'}`
    : open === 1 ? 'Last place in your squad' : 'Squad complete';
  $('#aucPurseBar').style.width = (100 * you.purse / d.purse_total) + '%';

  $('#aucTeams').innerHTML = d.teams.map(t => `
    <div class="auc-team${t.short === d.you ? ' you' : ''}">
      ${chip(t.short)}${t.owner ? `<em class="auc-owner">${t.owner}</em>` : ''}
      <div class="auc-team-bar"><i style="width:${100 * t.purse / d.purse_total}%;background:${TEAM_COLOURS[t.short][0]}"></i></div>
      <b>${cr(t.purse)}</b>
      <span>${t.players}/${d.squad_size}${t.overseas ? ` · ${t.overseas} OS` : ''}${d.mega ? ` · ${t.rtm} RTM` : ''}</span>
    </div>`).join('');

  $('#aucSoldCount').textContent = d.sold;
  $('#aucFeed').innerHTML = d.recent.slice().reverse().slice(0, 14).map(s => `
    <div class="auc-feed-row${s.team ? '' : ' unsold'}${s.team === d.you ? ' mine' : ''}">
      <span class="auc-feed-name">${s.name} <em>${s.season_year || ''}</em>${s.rtm_holder && s.rtm_matched ? ' <em class="auc-rtm-tag">RTM</em>' : ''}</span>
      ${s.team ? `${chip(s.team)}<b>${cr(s.price)}</b>` : '<b class="dim">unsold</b>'}
    </div>`).join('') || '<div class="note">Nothing sold yet.</div>';

  $('#aucSquadCount').textContent = `${d.squad.length}/${d.squad_size}`;
  const slots = [];
  for (let i = 0; i < d.squad_size; i++){
    const s = d.squad[i];
    slots.push(s ? `
      <div class="auc-slot filled" onclick='showStat(${JSON.stringify(s.card).replace(/'/g, "&#39;")})'>
        ${ICON[s.card.kind] || ''}<span>${s.card.name}</span><b>${cr(s.price)}${s.retained ? ' · kept' : ''}</b>
      </div>` : '<div class="auc-slot"></div>');
  }
  $('#aucSquad').innerHTML = slots.join('');
}

// --- bidding -------------------------------------------------------------------------------

function renderBidControls(){
  const d = A;
  document.querySelectorAll('.room-choice[data-style]').forEach(b =>
    b.classList.toggle('sel', b.dataset.style === BID_STYLE));
  const liveMode = BID_STYLE === 'live';
  $('#aucLive').classList.toggle('hide', !liveMode);
  $('#aucLimit').classList.toggle('hide', liveMode);
  const note = $('#aucNote');

  if (!d.can_bid){
    $('#aucBidBtn').disabled = true;
    $('#aucLimitBtn').disabled = true;
    note.textContent = `You can bid at most ${cr(d.max_bid)} and still fill your squad.`;
  } else {
    $('#aucBidBtn').disabled = false;
    $('#aucLimitBtn').disabled = false;
    $('#aucBidBtn').textContent = `Bid ${cr(d.next_price)}`;
    const floor = Math.max(d.next_price, d.your_bid || 0);
    if (LIMIT == null || LIMIT < floor || (A._limitLot !== d.lot.lot)) LIMIT = floor;
    A._limitLot = d.lot.lot;
    renderLimit();
    note.textContent = d.your_bid
      ? `Outbid. You bid ${cr(d.your_bid)}; ${d.leader} lead at ${cr(d.price)}.`
      : (d.bids.length ? '' : 'Bidding opens at the base price.');
  }
  $('#aucPassBtn').textContent = d.your_bid ? 'Let him go' : 'Not interested';
  if (AUCTION_ROOM){
    if (d.leader === d.you) note.textContent = 'You lead. Waiting on the room…';
    else if (d.your_limit) note.textContent = `Your limit of ${cr(d.your_limit)} is bidding for you.`;
    else if (d.you_done) note.textContent = 'You passed on this one.';
    $('#aucPassBtn').disabled = d.you_done || d.leader === d.you;
    // In a room the clock is everyone's, whichever way you bid.
    startCountdown();
    return;
  }

  if (liveMode) startCountdown(); else stopCountdown();
}

function renderLimit(){
  $('#aucLimitVal').textContent = cr(LIMIT);
  $('#aucLimitBtn').textContent = `Bid up to ${cr(LIMIT)}`;
}

function nudgeLimit(dir){
  const floor = Math.max(A.next_price, A.your_bid || 0);
  LIMIT = dir > 0 ? Math.min(stepUp(LIMIT), A.max_bid) : Math.max(stepDown(LIMIT, floor), floor);
  renderLimit();
}

function jumpLimit(lakh){
  let p = LIMIT;
  const target = Math.min(LIMIT + lakh, A.max_bid);
  while (stepUp(p) <= target) p = stepUp(p);
  LIMIT = p;
  renderLimit();
}

// Room mode: what a single-player move means as a ROOM move.
function roomMove(path, body){
  if (path === 'bid' && body.done) return ['limit', {max: body.ceiling}];
  if (path === 'bid') return ['bid', {price: body.ceiling}];
  return [path, body];
}

async function send(path, body, ctrl){
  if (BUSY) return;
  BUSY = true;
  stopCountdown();
  // The controls lock while an exchange plays out, visibly: a click that lands mid-
  // animation used to be swallowed by the BUSY guard while a stale "Bid ₹2 cr" still
  // looked pressable -- found by clicking it, not by reading the code.
  $('#aucBidBox').classList.add('rolling');
  $('#aucNote').textContent = 'The auctioneer takes bids…';
  // Not busyClick: that dims the whole section, and the exchange that plays out next is
  // the one moment the floor most needs to be watched.
  try {
    const d = AUCTION_ROOM
      ? await window.roomAuctionPost(...roomMove(path, body))
      : await api(`/api/auction/${A.state}/${path}`, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (d) await apply(d, true);
  } catch(e){
    slip(e.message);
    if (A && A.phase === 'bid' && BID_STYLE === 'live') startCountdown();
  } finally {
    BUSY = false;
    $('#aucBidBox').classList.remove('rolling');
  }
}

function liveBid(ctrl){ send('bid', {ceiling: A.next_price, done: false}, ctrl); }
function limitBid(ctrl){ send('bid', {ceiling: LIMIT, done: true}, ctrl); }
function passLot(scope, ctrl){ send('pass', {scope}, ctrl); }
function skipRest(ctrl){
  if (!confirm('Pass on every player left? The fill round will complete your squad at ₹30L each.')) return;
  send('pass', {scope: 'all'}, ctrl);
}

// Live mode's countdown. Only the human is waited on -- the computer teams have already
// decided -- so the clock is pressure rather than fairness, and running out simply means
// "not going higher".
function startCountdown(){
  stopCountdown();
  if (!A || A.phase !== 'bid') return;
  if (AUCTION_ROOM) return startRoomCountdown();
  const ring = $('#aucRing'), fill = $('#aucRingFill'), num = $('#aucRingNum');
  const total = LIVE_SECONDS * 1000, start = performance.now(), C = 2 * Math.PI * 19;
  ring.classList.remove('hide');
  fill.style.strokeDasharray = C;
  const tick = () => {
    const left = Math.max(0, total - (performance.now() - start));
    fill.style.strokeDashoffset = C * (1 - left / total);
    num.textContent = Math.ceil(left / 1000);
    ring.classList.toggle('urgent', left < 3000);
    if (left <= 0){ stopCountdown(); passLot('lot', null); }
  };
  tick();
  COUNTDOWN = setInterval(tick, 100);
}
// A room's lot clock is the SERVER's deadline, the same for everybody; running out closes
// the lot on the server, never here -- this only shows how long is left.
function startRoomCountdown(){
  const ring = $('#aucRing'), fill = $('#aucRingFill'), num = $('#aucRingNum');
  const C = 2 * Math.PI * 19, total = 15;
  ring.classList.remove('hide');
  fill.style.strokeDasharray = C;
  const tick = () => {
    const left = Math.max(0, window.roomAuctionDeadline());
    fill.style.strokeDashoffset = C * (1 - Math.min(1, left / total));
    num.textContent = Math.ceil(left);
    ring.classList.toggle('urgent', left < 4);
  };
  tick();
  COUNTDOWN = setInterval(tick, 200);
}

function stopCountdown(){
  clearInterval(COUNTDOWN); COUNTDOWN = null;
  const ring = $('#aucRing');
  if (ring) ring.classList.add('hide');
}

document.addEventListener('keydown', e => {
  if (!A || A.phase !== 'bid' || e.target.tagName === 'INPUT') return;
  if (e.key === 'b' || e.key === 'B' || e.key === ' '){
    e.preventDefault();
    if (!A.can_bid) return;
    BID_STYLE === 'live' ? liveBid($('#aucBidBtn')) : limitBid($('#aucLimitBtn'));
  } else if (e.key === 'n' || e.key === 'N'){
    passLot('lot', $('#aucPassBtn'));
  }
});

// --- retentions [A138] --------------------------------------------------------------------

function retentionPeople(){
  // The pool is strongest first; group it by player, keeping that order.
  const people = new Map();
  for (const p of A.retention_pool){
    if (!people.has(p.card.person_id)) people.set(p.card.person_id, []);
    people.get(p.card.person_id).push(p);
  }
  return [...people.values()];
}

function renderRetain(){
  const people = retentionPeople();
  // [A141] In a room the picks are sealed; say how a shared legend is settled.
  const note = $('#aucRetainRule');
  if (AUCTION_ROOM && !note){
    $('#aucRetain .auc-lede').insertAdjacentHTML('afterend', `<p class="auc-lede" id="aucRetainRule"
      style="margin-top:10px">Picks are sealed. If someone else keeps a player you keep too, he
      goes to the franchise that had his better season, and the place you lose becomes a
      Right to Match card.</p>`);
  }
  const picked = new Set(RETAIN);
  $('#aucRetainCount').textContent = `${people.length} players`;
  $('#aucRetainList').innerHTML = people.map(seasons => {
    const pid = seasons[0].card.person_id;
    const showing = RETAIN_SEASON[pid] ?? seasons[0].index;
    const cur = seasons.find(p => p.index === showing) || seasons[0];
    const kept = seasons.some(p => picked.has(p.index));
    const c = cur.card;
    const choose = seasons.length > 1
      ? `<select class="auc-season" onchange="pickSeason('${pid}', +this.value)" ${kept ? 'disabled' : ''}>
           ${seasons.map(p => `<option value="${p.index}" ${p.index === cur.index ? 'selected' : ''}>
             ${p.card.season_year} · ${p.card.rating}</option>`).join('')}
         </select>`
      : `<span class="auc-season-one">${c.season_year}</span>`;
    return `<div class="auc-retain-row${kept ? ' kept' : ''}">
      ${ICON[c.kind] || ''}${keeperBadge(c)}
      <span class="auc-retain-name" onclick='showStat(${JSON.stringify(c).replace(/'/g, "&#39;")})'>${c.name}
        ${c.overseas ? '<em class="auc-tag os">Overseas</em>' : ''}</span>
      ${choose}${ratingBadge(c, true)}
      <button class="act minor" onclick="toggleRetain(${cur.index})">${kept ? 'Release' : 'Keep'}</button>
    </div>`;
  }).join('');

  const slabs = A.retention_slabs;
  const byIndex = new Map(A.retention_pool.map(p => [p.index, p.card]));
  const spend = RETAIN.reduce((sum, _, i) => sum + slabs[i], 0);
  $('#aucRetainSpend').textContent = spend ? cr(spend) : '';
  $('#aucRetainSum').innerHTML = `
    ${RETAIN.map((i, n) => `<div class="auc-kept-row"><span>${n + 1}.</span>
        <b>${byIndex.get(i).name}</b><em>${byIndex.get(i).season_year}</em><i>${cr(slabs[n])}</i></div>`).join('')
      || '<div class="note">Nobody kept yet. You can start with a full purse and six Right to Match cards.</div>'}
    <div class="auc-retain-totals">
      <div><b>${cr(A.purse_total - spend)}</b><span>purse for the auction</span></div>
      <div><b>${A.rtm_places - RETAIN.length}</b><span>Right to Match cards</span></div>
    </div>`;
  $('#aucRetainBtn').disabled = RETAIN.length === 0;
}

function pickSeason(pid, index){ RETAIN_SEASON[pid] = index; renderRetain(); }

function toggleRetain(index){
  const pos = RETAIN.indexOf(index);
  if (pos >= 0){ RETAIN.splice(pos, 1); renderRetain(); return; }
  if (RETAIN.length >= A.retention_slabs.length){
    slip(`At most ${A.retention_slabs.length} retentions.`);
    return;
  }
  RETAIN.push(index);
  renderRetain();
}

function confirmRetain(ctrl, nobody){
  busyClick(ctrl, 'Opening the auction…', async () => {
    try {
      const picks = nobody ? [] : RETAIN;
      const d = AUCTION_ROOM ? await window.roomAuctionPost('retain', {picks})
        : await api(`/api/auction/${A.state}/retain`, {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({picks})});
      if (d) await apply(d, false);
    } catch(e){ slip(e.message); }
  });
}

// --- Right to Match [A138] ------------------------------------------------------------------

let RAISE = null;

function openRtm(){
  const d = A, r = d.rtm, c = d.lot.card;
  const who = `<b>${c.name}</b> <em>${[c.franchise, c.season_year].filter(Boolean).join(' · ')}</em>`;
  let body;
  if (r.kind === 'rtm_use'){
    body = `
      <div class="over-line">Right to Match · ${cardsLeft(d)}</div>
      <div class="call">Your old player</div>
      <p>${who} has gone to ${chip(r.other)} for <b>${cr(r.price)}</b>.</p>
      <p class="room-note">Play a card and ${r.other} get one final raise. Match it and he is
        yours; don't, and they keep him at the raised price. A card is only spent if you match.</p>
      <div class="actions"><button class="act lead" onclick="answerRtm(true, this)">Use Right to Match</button>
        <button class="act" onclick="answerRtm(false, this)">Let him go</button></div>`;
  } else if (r.kind === 'rtm_match'){
    body = `
      <div class="over-line">Right to Match · the final raise</div>
      <div class="call">${r.other} raise to ${cr(r.price)}</div>
      <p>Match it and ${who} is yours for <b>${cr(r.price)}</b>.</p>
      <div class="actions"><button class="act lead" onclick="answerRtm(true, this)"
          ${d.max_bid < r.price ? 'disabled' : ''}>Match ${cr(r.price)}</button>
        <button class="act" onclick="answerRtm(false, this)">Decline</button></div>
      ${d.max_bid < r.price ? `<p class="room-note">You can pay at most ${cr(d.max_bid)} and still fill your squad.</p>` : ''}`;
  } else {
    RAISE = r.price;
    body = `
      <div class="over-line">Right to Match · against you</div>
      <div class="call">${r.other} want him back</div>
      <p>You bought ${who} for <b>${cr(r.price)}</b>, and ${r.other} have played a card. Make one
        final raise: if they match it they take him, if they don't you keep him at that price.</p>
      <div class="auc-limit">
        <button class="act minor" onclick="nudgeRaise(-1)">−</button>
        <div class="auc-limit-val"><span>Final price</span><b id="aucRaiseVal">${cr(RAISE)}</b></div>
        <button class="act minor" onclick="nudgeRaise(1)">+</button>
      </div>
      <div class="actions"><button class="act lead" onclick="answerRtm(true, this)">Raise to <span id="aucRaiseBtn">${cr(RAISE)}</span></button>
        <button class="act" onclick="answerRtm(false, this)">No raise</button></div>`;
  }
  $('#aucRtmBody').innerHTML = `<div class="auc-rtm">${body}</div>`;
  $('#aucRtm').classList.remove('hide');
}

function cardsLeft(d){
  const n = yourTeam(d).rtm;
  return `${n} card${n === 1 ? '' : 's'} left`;
}

function closeRtm(){ const el = $('#aucRtm'); if (el) el.classList.add('hide'); }

function nudgeRaise(dir){
  const floor = A.rtm.price, top = A.rtm.max_raise;
  RAISE = dir > 0 ? Math.min(stepUp(RAISE), top) : Math.max(stepDown(RAISE, floor), floor);
  $('#aucRaiseVal').textContent = cr(RAISE);
  $('#aucRaiseBtn').textContent = cr(RAISE);
}

function answerRtm(yes, ctrl){
  const body = {yes};
  if (A.rtm.kind === 'rtm_raise' && yes) body.price = RAISE;
  send('rtm', body, ctrl);
}

// --- the fill round ------------------------------------------------------------------------

function renderFill(){
  const d = A;
  const you = yourTeam(d);
  const need = d.squad_size - you.players;
  $('#aucFillLede').textContent = `The hammer has fallen with ${need} place${need === 1 ? '' : 's'} `
    + `still open in your squad. Take the players you want at ₹30L each, best first.`;
  $('#aucFillList').innerHTML = d.fill_options.map((c, i) => `
    <div class="auc-fill-row">
      ${ICON[c.kind] || ''}${keeperBadge(c)}
      <span class="auc-fill-name" onclick='showStat(${JSON.stringify(c).replace(/'/g, "&#39;")})'>${c.name}
        <em>${[c.franchise, c.season_year].filter(Boolean).join(' · ')}</em></span>
      ${ratingBadge(c, true)}
      ${c.overseas ? '<span class="auc-tag os">Overseas</span>' : ''}
      <button class="act minor" onclick="takeFill(${i}, this)">Take</button>
    </div>`).join('');
}

function takeFill(i, ctrl){
  busyClick(ctrl, null, async () => {
    try {
      const d = AUCTION_ROOM ? await window.roomAuctionPost('fill', {index: i})
        : await api(`/api/auction/${A.state}/fill`, {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({index: i})});
      if (d) await apply(d, false);
    } catch(e){ slip(e.message); }
  });
}

// --- a room waiting on other people [A139] ---------------------------------------------------

function renderWait(){
  const d = A, el = $('#aucWaitText');
  if (!el) return;
  const who = d.waiting_on && d.waiting_on.length ? d.waiting_on.join(', ') : 'the room';
  el.textContent = d.phase === 'complete'
    ? 'The auction is over. The season is about to start.'
    : `Waiting on ${who}…`;
}

// --- pick your twelve ----------------------------------------------------------------------

function renderTwelve(){
  const d = A;
  // In a room the season waits for everybody's twelve, so the button only locks yours in.
  if (AUCTION_ROOM) $('#aucSeasonBtn').textContent = 'Lock in my twelve';
  if (!TWELVE) TWELVE = (d.twelve || d.suggestion || []).slice();
  drawTwelve();
}

function slotName(i){ return i === 11 ? 'Impact' : `No. ${i + 1}`; }

function drawTwelve(){
  const d = A, squad = d.squad;
  const inTwelve = new Set(TWELVE);
  $('#aucOrder').innerHTML = TWELVE.map((si, i) => {
    const s = squad[si], c = s.card;
    const ok = i === 11 || c.positions.includes(i + 1);
    return `<div class="order-row auc-order-row${TWELVE_SEL === i ? ' sel' : ''}${ok ? '' : ' bad'}"
        onclick="twelveTap(${i})">
      <span class="auc-pos">${slotName(i)}</span>${ICON[c.kind] || ''}${keeperBadge(c)}
      <span class="auc-o-name">${c.name}</span>${ratingBadge(c, true)}<b>${cr(s.price)}</b>
    </div>`;
  }).join('');
  $('#aucBench').innerHTML = squad.map((s, si) => inTwelve.has(si) ? '' : `
    <div class="order-row auc-order-row bench${TWELVE_SEL != null ? ' target' : ''}" onclick="benchTap(${si})">
      ${ICON[s.card.kind] || ''}${keeperBadge(s.card)}
      <span class="auc-o-name">${s.card.name}</span>${ratingBadge(s.card, true)}<b>${cr(s.price)}</b>
    </div>`).join('');

  const cards = TWELVE.map(i => squad[i].card);
  const problems = [];
  cards.forEach((c, i) => { if (i < 11 && !c.positions.includes(i + 1)) problems.push(`${c.name} cannot bat at ${i + 1}`); });
  if (!cards.some(c => c.keeper_eligible)) problems.push('no wicketkeeper');
  // A bowling option is anyone who bowled that season; the server re-checks on submit.
  const bowlers = cards.filter(c => c.bowl_balls != null).length;
  if (bowlers < 5) problems.push(`only ${bowlers} of 5 bowling options`);
  const overseas = cards.filter(c => c.overseas === true).length;
  if (overseas > 4) problems.push(`${overseas} overseas players, more than four`);
  const ratings = cards.map(c => c.rating).filter(v => v != null);
  $('#aucTwelveRating').textContent = ratings.length
    ? 'avg ' + Math.round(ratings.reduce((a, b) => a + b, 0) / ratings.length) : '';
  $('#aucTwelveCheck').innerHTML = problems.length
    ? problems.map(p => `<div class="bad">${p}</div>`).join('')
    : `<div class="ok">✓ A legal twelve · ${overseas} overseas · ${bowlers} bowling options</div>`;
  $('#aucSeasonBtn').disabled = problems.length > 0;
}

function twelveTap(i){
  if (TWELVE_SEL === null){ TWELVE_SEL = i; drawTwelve(); return; }
  if (TWELVE_SEL !== i){ [TWELVE[TWELVE_SEL], TWELVE[i]] = [TWELVE[i], TWELVE[TWELVE_SEL]]; }
  TWELVE_SEL = null;
  drawTwelve();
}

function benchTap(si){
  if (TWELVE_SEL === null){ slip('Tap a place in the twelve first, then the player to bring in.'); return; }
  TWELVE[TWELVE_SEL] = si;
  TWELVE_SEL = null;
  drawTwelve();
}

function playSeason(ctrl){
  if (AUCTION_ROOM){
    busyClick(ctrl, 'Sending your twelve…', async () => {
      try {
        const d = await window.roomAuctionPost('twelve',
          {order: TWELVE.slice(0, 11), impact: TWELVE[11]});
        if (d) await apply(d, false);
      } catch(e){ slip(e.message); }
    });
    return;
  }
  busyClick(ctrl, 'Playing the season…', async () => {
    try {
      const d = await api(`/api/auction/${A.state}/twelve`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order: TWELVE.slice(0, 11), impact: TWELVE[11]})});
      location.href = `/season?enter=whole#${d.state}`;
    } catch(e){ slip(e.message); }
  });
}

// --- boot ----------------------------------------------------------------------------------

async function auctionBoot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  const h = location.hash.slice(1);
  if (!h){ renderSetup(); return; }
  try { await apply(await api('/api/auction/' + h), false); }
  catch(e){ slip(e.message); history.replaceState(null, '', location.pathname); renderSetup(); }
}
if (!AUCTION_ROOM) auctionBoot();
