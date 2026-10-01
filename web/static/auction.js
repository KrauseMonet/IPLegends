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

// Team colours are no longer picked here: they are derived from each crest and served as
// `.crest-KEY` classes in crests.css (tools.build_crests), and an auction franchise's short
// code IS its crest key. The crest image itself comes from META.crests.
function teamCrestUrl(short){ return ((META && META.crests) || {})[short] || null; }
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
  const url = teamCrestUrl(short);
  return `<span class="auc-chip crest-${short}${big ? ' big' : ''}">${
    url ? `<img src="${url}" alt="">` : ''}${short}</span>`;
}

// One franchise button, for the solo setup screen and an auction room's lobby alike.
function franchiseButton(s, name, attrs, inner){
  const url = teamCrestUrl(s);
  return `<button class="auc-fr crest-${s}" ${attrs}>
    ${url ? `<img class="auc-fr-crest" src="${url}" alt="">` : ''}
    <b>${s}</b>${inner ?? `<span>${name}</span>`}</button>`;
}

// --- setup -------------------------------------------------------------------------------

function renderSetup(){
  $('#aucFranchises').innerHTML = FRANCHISES.map(([s, name]) =>
    franchiseButton(s, name, `data-team="${s}" onclick="chooseTeam('${s}')" title="${name}"`)
  ).join('');
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
    if (d.phase === 'bid' && opensSet(prev, d)) showSetCard(d.lot);
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
  if (sale) await stamp(sale, isRecord(sale, prev));
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
  await playBids(fresh, bids.slice(0, prev.bids.length));
  if (sale) await stamp(sale, isRecord(sale, prev));
  else if (stillOpen && fresh.length) announceBid(next.leader, next.price);
}

function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

async function playBids(bids, before = []){
  if (!bids.length) return;
  const shown = before.slice();
  // A lot averages ~30 bids: the whole exchange plays in about two seconds however long
  // it is, a quick patter for a long war and a readable beat for a short one.
  const step = Math.max(55, Math.min(320, 2200 / bids.length));
  $('#aucPriceLabel').textContent = 'Current bid';
  let lastSound = 0;
  for (const b of bids){
    shown.push(b);
    setPrice(b.price);
    setLeader(b.team, true);
    renderTrail(shown, true);
    markRoom(shown, b.team, true);
    // A long war would machine-gun the speaker; a knock every ~90ms reads as a rattle.
    const now = performance.now();
    if (now - lastSound > 90 || b.team === (A && A.you)){
      play('bid', b.team === (A && A.you));
      lastSound = now;
    }
    await sleep(step);
  }
}

function setPrice(p){
  const el = showPrice(p);
  el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump');
}
function showPrice(p){
  const el = $('#aucPrice');
  el.textContent = cr(p);
  priceTier(el, p);
  return el;
}

// --- the bidding war ----------------------------------------------------------------------
//
// Presentation only: everything here reads bids the server already decided, and it runs
// inside playBids' two-second budget rather than adding time after it [A146].

// A price's colour rises with it, so a ₹25 cr war does not look like a ₹30L sale. The same
// scale colours the sold feed, which makes the big buys stand out when scanning it.
const PRICE_TIERS = [[2000, 'tier-hot'], [1000, 'tier-high']];   // lakh: ₹20 cr, ₹10 cr
function tierOf(p){ const t = p == null ? null : PRICE_TIERS.find(([at]) => p >= at); return t ? t[1] : ''; }
function priceTier(el, p){
  el.classList.remove('tier-high', 'tier-hot');
  const t = tierOf(p);
  if (t) el.classList.add(t);
}

// Swap an element's team colours: `.crest-KEY` classes carry --team/--team-deep/--team-ink.
function setTeamClass(el, short){
  el.className = el.className.replace(/\bcrest-[A-Z0-9]+\b/g, '').trim();
  if (short) el.classList.add('crest-' + short);
}

function setLeader(short, flash){
  const el = $('#aucLeader'), box = $('#aucBidBox');
  setTeamClass(box, short);
  box.classList.toggle('has-lead', !!short);
  if (!short){ el.innerHTML = ''; return; }
  const you = A && short === A.you;
  el.innerHTML = `${chip(short, true)}<span>${you ? 'You lead' : 'Leading'}</span>`;
  el.classList.toggle('you', !!you);
  if (flash){ box.classList.remove('lead-flash'); void box.offsetWidth; box.classList.add('lead-flash'); }
}

const TRAIL_SHOWN = 6;
function trailEl(){
  let el = $('#aucTrail');
  if (!el){
    // Built here rather than in the markup: auction.html and room.html both carry the floor.
    el = document.createElement('div');
    el.id = 'aucTrail';
    el.className = 'auc-trail';
    $('#aucBidBox .auc-bidrow').after(el);
  }
  return el;
}

// The last few bids as a strip, newest on the right, with a count that says what kind of
// fight it was: thirty bids between two teams is a war, thirty among six is a scramble.
function renderTrail(bids, fresh){
  const el = trailEl();
  if (!bids || !bids.length){
    el.innerHTML = '<span class="auc-trail-sum">No bids yet</span>';
    return;
  }
  const teams = [];
  bids.forEach(b => { if (!teams.includes(b.team)) teams.push(b.team); });
  const who = teams.length === 2 ? `${teams[0]} v ${teams[1]}`
            : teams.length === 1 ? teams[0] : `${teams.length} teams`;
  const last = bids.slice(-TRAIL_SHOWN);
  el.innerHTML = `<span class="auc-trail-sum"><b>${bids.length}</b> bid${bids.length === 1 ? '' : 's'} · ${who}</span>
    <span class="auc-trail-bids">${last.map((b, i) => {
      const url = teamCrestUrl(b.team);
      const isNew = fresh && i === last.length - 1;
      return `<span class="auc-trail-bid crest-${b.team}${isNew ? ' new' : ''}${b.team === (A && A.you) ? ' mine' : ''}"
        title="${b.team} ${cr(b.price)}">${url ? `<img src="${url}" alt="">` : `<i>${b.team}</i>`}${cr(b.price)}</span>`;
    }).join('')}</span>`;
}

// Light up the room panel: every team that has bid on this lot is marked as in it, the
// leader more strongly, and a team's row flashes the moment it raises the paddle.
function markRoom(bids, bidder, flash){
  const inLot = new Set((bids || []).map(b => b.team));
  const leader = bids && bids.length ? bids[bids.length - 1].team : null;
  document.querySelectorAll('#aucTeams .auc-team[data-team]').forEach(row => {
    const t = row.dataset.team;
    row.classList.toggle('in-lot', inLot.has(t));
    row.classList.toggle('leading', t === leader);
    if (flash && t === bidder){ row.classList.remove('bid-flash'); void row.offsetWidth; row.classList.add('bid-flash'); }
  });
}

async function stamp(sale, record){
  const el = $('#aucStamp');
  if (sale.team){
    const rtm = sale.rtm_holder
      ? `<em>${sale.rtm_matched ? 'Right to Match · ' + sale.rtm_holder + ' took him back'
                                 : sale.rtm_holder + ' played a card and did not match'}</em>` : '';
    const top = record ? '<i class="auc-record">Most expensive so far</i>' : '';
    el.innerHTML = `${top}<b>SOLD</b><span>${chip(sale.team)} ${cr(sale.price)}</span>${rtm}`;
    // The stamp takes the buyer's colour through its class, like every other team surface.
    el.className = el.className.replace(/\bcrest-[A-Z]+\b/g, '').trim();
    el.classList.add('crest-' + sale.team);
    el.style.setProperty('--stamp', 'var(--team)');
    el.classList.remove('unsold');
  } else {
    el.innerHTML = '<b>UNSOLD</b>';
    el.style.setProperty('--stamp', '#8a96b0');
    el.classList.add('unsold');
  }
  el.classList.remove('hide', 'on'); void el.offsetWidth; el.classList.add('on');
  clearClosing();
  // The gavel lands: the stage jolts once, and a buy of your own bursts in your colours.
  const stage = $('#aucStage');
  stage.classList.remove('gavel'); void stage.offsetWidth; stage.classList.add('gavel');
  if (sale.team && sale.team === (A && A.you)) burst(sale.team);
  play(sale.team ? 'sold' : 'unsold', sale.team === (A && A.you));
  announceSale(sale);
  await sleep(sale.team === (A && A.you) ? 1700 : 1150);
  el.classList.add('hide');
}


// --- the lot card and the sets [A156] --------------------------------------------------------
//
// Presentation only, like A155: none of this waits on anything or delays a lot. The set card
// and the burst sit over the stage with no pointer events, so the floor stays usable under
// them, and the countdown runs on regardless.

// The auctioneer's closing words on screen as well as spoken, in the same order (CLOSING).
const CLOSING_SHOWN = {3: 'Fair warning', 2: 'Going once', 1: 'Going twice'};
function stageOverlay(id, cls){
  let el = $('#' + id);
  if (!el){
    // Built here: auction.html and room.html each carry their own copy of the stage.
    el = document.createElement('div');
    el.id = id;
    el.className = cls;
    $('#aucStage').append(el);
  }
  return el;
}
function showClosing(whole){
  const el = stageOverlay('aucClosing', 'auc-closing');
  el.textContent = CLOSING_SHOWN[whole] || '';
  // Not `go`: that is the site's full-width button class, and it stretched this banner.
  el.classList.remove('on'); void el.offsetWidth; el.classList.add('on');
  $('#aucStage').classList.add('closing');
}
function clearClosing(){
  const el = $('#aucClosing');
  if (el){ el.textContent = ''; el.classList.remove('on'); }
  const stage = $('#aucStage');
  if (stage) stage.classList.remove('closing');
}

// A record beats every sale before it -- equalling the old record is not a new one. Compared
// with `prev` alone: the sale being stamped is the first to resolve after `prev`, while
// `next.top_price` may already include later lots (skipping a set resolves the whole set in
// one response), which would wrongly deny this one its record.
function isRecord(sale, prev){
  return !!(sale.team && (prev.top_price == null || sale.price > prev.top_price));
}

const BURST_PIECES = 22;
function burst(short){
  const stage = $('#aucStage');
  const el = document.createElement('div');
  el.className = 'auc-burst crest-' + short;
  let html = '';
  for (let i = 0; i < BURST_PIECES; i++){
    const angle = (360 / BURST_PIECES) * i + (i % 3) * 7;
    const dist = 110 + (i % 4) * 38;
    html += `<i style="--a:${angle}deg;--d:${dist}px;--delay:${(i % 5) * 18}ms"${i % 2 ? ' class="alt"' : ''}></i>`;
  }
  el.innerHTML = html;
  stage.append(el);
  setTimeout(() => el.remove(), 1400);
}

// A new set opens: on moving from one set (or round) to the next, and on the first lot of
// an auction reached from setup or retentions. A reload in the middle of a set shows none.
function opensSet(prev, d){
  const lot = d.lot;
  if (!lot) return false;
  if (prev && prev.lot && prev.phase !== 'retain')
    return prev.lot.set_code !== lot.set_code || prev.lot.round !== lot.round;
  return lot.lot === 0 && (!prev || prev.phase === 'retain' || !prev.lot);
}

function showSetCard(lot){
  const accelerated = lot.round === 'accelerated';
  const el = stageOverlay('aucSetCard', 'auc-setcard');
  const count = lot.upcoming.length + 1;
  el.classList.toggle('accel', accelerated);
  el.innerHTML = accelerated
    ? `<em>Accelerated round</em><b>The unsold, again</b><span>Every unsold player comes up once more</span>`
    : `<em>Now opening</em><b>${esc(lot.set_label)}</b><span>${count} player${count === 1 ? '' : 's'} in this set</span>`;
  el.classList.remove('on'); void el.offsetWidth; el.classList.add('on');
}


// --- sound [A143] -------------------------------------------------------------------------
//
// Synthesised with the Web Audio API rather than recorded: nothing to license, nothing to
// download, and a knock can be pitched per bidder. Browsers refuse to play sound before the
// page has been interacted with, so the context is created on the first click and every
// cue before that is simply silent -- never an error. The auctioneer's voice is the
// browser's own speech synthesis, in one of two styles [A144]: a classic British auction
// room (the default) or the original Indian English announcer. Sound, voice and style are
// all switchable on the floor and remembered.

const SOUND_KEY = 'iplegends_auction_sound', VOICE_KEY = 'iplegends_auction_voice';
const VOICE_STYLE_KEY = 'iplegends_auction_voice_style';
let SOUND_ON = true, VOICE_ON = true, AUDIO = null, VOICE_STYLE = 'british';
try {
  SOUND_ON = localStorage.getItem(SOUND_KEY) !== 'off';
  VOICE_ON = localStorage.getItem(VOICE_KEY) !== 'off';
  VOICE_STYLE = localStorage.getItem(VOICE_STYLE_KEY) === 'indian' ? 'indian' : 'british';
} catch(e) {}

function audio(){
  if (!SOUND_ON) return null;
  if (!AUDIO){
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    AUDIO = new Ctx();
  }
  if (AUDIO.state === 'suspended') AUDIO.resume();
  return AUDIO.state === 'running' ? AUDIO : null;
}
document.addEventListener('pointerdown', () => { if (SOUND_ON) audio(); }, {once: true});

function tone(freq, dur, {type = 'sine', gain = 0.18, at = 0, slide = null} = {}){
  const ctx = audio();
  if (!ctx) return;
  const t = ctx.currentTime + at;
  const osc = ctx.createOscillator(), amp = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t);
  if (slide) osc.frequency.exponentialRampToValueAtTime(slide, t + dur);
  amp.gain.setValueAtTime(gain, t);
  amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  osc.connect(amp).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + dur + 0.02);
}

function knock(at = 0, pitch = 1){
  const ctx = audio();
  if (!ctx) return;
  // A wooden knock: a short noise burst through a band-pass, over a falling low tone.
  const t = ctx.currentTime + at;
  const len = Math.floor(ctx.sampleRate * 0.06);
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len) ** 3;
  const src = ctx.createBufferSource(), band = ctx.createBiquadFilter(), amp = ctx.createGain();
  src.buffer = buf;
  band.type = 'bandpass'; band.frequency.value = 900 * pitch; band.Q.value = 1.4;
  amp.gain.value = 0.9;
  src.connect(band).connect(amp).connect(ctx.destination);
  src.start(t);
  tone(220 * pitch, 0.12, {type: 'triangle', gain: 0.25, at, slide: 110 * pitch});
}

const SOUNDS = {
  bid: mine => tone(mine ? 880 : 520, 0.07, {type: 'triangle', gain: mine ? 0.16 : 0.09}),
  tick: () => tone(1250, 0.05, {gain: 0.07}),
  sold: mine => {
    knock(0); knock(0.17);
    tone(70, 0.35, {gain: 0.3, at: 0.17});
    if (mine){ tone(880, 0.5, {gain: 0.12, at: 0.35}); tone(1320, 0.6, {gain: 0.1, at: 0.45}); }
  },
  unsold: () => knock(0, 0.7),
  attention: () => { tone(660, 0.18, {gain: 0.14}); tone(990, 0.3, {gain: 0.12, at: 0.16}); },
};

function play(name, arg){ try { SOUNDS[name](arg); } catch(e) {} }

function priceWords(lakh){
  if (lakh < 100) return `${lakh} lakh`;
  const crore = (lakh / 100).toFixed(2).replace(/\.?0+$/, '');
  return `${crore} crore`;
}

// --- the auctioneer's voice [A144] -------------------------------------------------------
//
// Only the browser's own voices: nothing recorded, nothing cloned, no speech service. The
// British style asks for a British male voice where the device has one (Daniel on Apple,
// Google UK English Male in Chrome, Ryan or George on Windows) and speaks lower and more
// deliberately, in short phrases with a beat between them -- the rhythm is what makes it an
// auction room, since the device decides the timbre. A device with no British voice gets
// any British one, then any English one, so the patter still runs in whatever voice exists.

const BRITISH_MALE = /daniel|arthur|oliver|george|ryan|thomas|uk english male|male/i;
let VOICES = [];
function refreshVoices(){ try { VOICES = speechSynthesis.getVoices(); } catch(e) {} }
if ('speechSynthesis' in window){
  refreshVoices();
  // Chrome fills the list asynchronously; the first getVoices() is usually empty.
  speechSynthesis.addEventListener && speechSynthesis.addEventListener('voiceschanged', refreshVoices);
}

function pickVoice(){
  if (!VOICES.length) refreshVoices();
  const lang = v => (v.lang || '').replace('_', '-');
  if (VOICE_STYLE === 'british'){
    const gb = VOICES.filter(v => lang(v) === 'en-GB');
    return gb.find(v => BRITISH_MALE.test(v.name) && !/female/i.test(v.name))
        || gb[0] || VOICES.find(v => lang(v).startsWith('en')) || null;
  }
  return VOICES.find(v => lang(v) === 'en-IN') || VOICES.find(v => lang(v).startsWith('en')) || null;
}

// A line is a list of phrases, queued as separate utterances so each gets its own beat.
function speak(text){
  if (!VOICE_ON || !('speechSynthesis' in window)) return;
  try {
    speechSynthesis.cancel();              // never queue behind a stale announcement
    const voice = pickVoice();
    const british = VOICE_STYLE === 'british';
    for (const phrase of [].concat(text)){
      const u = new SpeechSynthesisUtterance(phrase);
      u.voice = voice;
      if (voice) u.lang = voice.lang;
      u.rate = british ? 0.9 : 1.05;
      u.pitch = british ? 0.85 : 1;
      speechSynthesis.speak(u);
    }
  } catch(e) {}
}

function franchiseName(short){
  const team = A && A.teams.find(t => t.short === short);
  return team ? team.franchise : short;
}

// An auctioneer says "two crore, forty lakh", not "two point four crore".
function priceSpoken(lakh){
  const crore = Math.floor(lakh / 100), rest = lakh % 100;
  if (!crore) return `${rest} lakh`;
  return rest ? `${crore} crore, ${rest} lakh` : `${crore} crore`;
}

function announceSale(sale){
  if (VOICE_STYLE !== 'british'){
    if (!sale.team){ speak(`${sale.name}. Unsold.`); return; }
    speak(`Sold! ${sale.name}, to ${franchiseName(sale.team)}, for ${priceWords(sale.price)}.`);
    return;
  }
  if (!sale.team){ speak([`${sale.name}.`, 'No bid. Passed.']); return; }
  const mine = sale.team === (A && A.you);
  speak(['Sold!', `${sale.name}.`,
         mine ? 'To you,' : `To ${franchiseName(sale.team)},`,
         `at ${priceSpoken(sale.price)}.`]);
}

// British only: where the bidding stands once an exchange settles and the lot is still
// open, the way an auctioneer tells the room whose bid it is.
function announceBid(short, price){
  if (VOICE_STYLE !== 'british' || !short) return;
  speak(short === (A && A.you)
    ? [`With you, at ${priceSpoken(price)}.`]
    : [`${priceSpoken(price)}.`, `With ${franchiseName(short)}.`]);
}

// British only: the classic close on the countdown's last three seconds.
const CLOSING = {3: 'Fair warning.', 2: 'Going once.', 1: 'Going twice.'};
function announceClosing(whole){
  if (VOICE_STYLE === 'british' && CLOSING[whole]) speak(CLOSING[whole]);
}

function setVoiceStyle(style){
  VOICE_STYLE = style === 'indian' ? 'indian' : 'british';
  try { localStorage.setItem(VOICE_STYLE_KEY, VOICE_STYLE); } catch(e) {}
  renderSoundControls();
  if (VOICE_ON) speak(VOICE_STYLE === 'british' ? ['Good evening.', 'Lot one.'] : 'Welcome to the auction.');
}

function setSound(on){
  SOUND_ON = on;
  try { localStorage.setItem(SOUND_KEY, on ? 'on' : 'off'); } catch(e) {}
  if (on) audio();
  renderSoundControls();
}
function setVoice(on){
  VOICE_ON = on;
  try { localStorage.setItem(VOICE_KEY, on ? 'on' : 'off'); } catch(e) {}
  if (!on && 'speechSynthesis' in window) speechSynthesis.cancel();
  renderSoundControls();
}

// Drawn into the floor's own header, on both the single-player page and a room.
function renderSoundControls(){
  const bar = $('.auc-lotbar');
  if (!bar) return;
  let box = $('#aucSoundCtl');
  if (!box){
    bar.insertAdjacentHTML('beforeend', '<span class="auc-sound" id="aucSoundCtl"></span>');
    box = $('#aucSoundCtl');
  }
  box.innerHTML = `
    <button class="auc-sound-btn${SOUND_ON ? ' on' : ''}" onclick="setSound(${!SOUND_ON})"
      title="Gavel, bids and countdown">${SOUND_ON ? 'Sound on' : 'Sound off'}</button>
    <button class="auc-sound-btn${VOICE_ON ? ' on' : ''}" onclick="setVoice(${!VOICE_ON})"
      title="The auctioneer announces each sale">${VOICE_ON ? 'Voice on' : 'Voice off'}</button>
    <button class="auc-sound-btn${VOICE_ON ? ' on' : ''}" ${VOICE_ON ? '' : 'disabled'}
      onclick="setVoiceStyle('${VOICE_STYLE === 'british' ? 'indian' : 'british'}')"
      title="The auctioneer's style: a British auction room or an Indian English announcer">${
      VOICE_STYLE === 'british' ? 'British' : 'Indian'}</button>`;
}

// --- the floor ---------------------------------------------------------------------------

function renderFloor(){
  const d = A, lot = d.lot, c = lot.card;
  renderSoundControls();
  $('#aucSetLabel').textContent = lot.round === 'accelerated' ? 'Accelerated round · the unsold, again'
                                                               : lot.set_label;
  $('#aucLotNo').textContent = `Lot ${lot.lot + 1} of ${lot.lots_total}`;

  const tag = c.overseas === true ? '<span class="auc-tag os">Overseas</span>'
            : c.overseas === false ? '<span class="auc-tag">India</span>' : '';
  // The lot wears the colours of the season it comes from -- Pietersen 2012 is a Delhi
  // Daredevils lot whoever ends up buying him.
  const card = $('#aucCard');
  card.className = card.className.replace(/\bcrest-[A-Z]+\b/g, '').trim() + ' ' + crestClass(c.crest);
  card.innerHTML = `
    ${c.crest ? `<img class="auc-card-wm" src="${c.crest}" alt="">` : ''}
    <div class="auc-card-top">
      <div>
        <div class="auc-card-season">${crestImg(c.crest, 'auc-card-crest')}${[c.franchise, c.season_year].filter(Boolean).join(' · ')}</div>
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
    showPrice(d.rtm.price);
    setLeader(d.rtm.kind === 'rtm_raise' ? d.rtm.deciding || d.you : d.rtm.other);
  } else if (d.bids.length){
    $('#aucPriceLabel').textContent = 'Current bid';
    showPrice(d.price);
    setLeader(d.leader);
  } else {
    $('#aucPriceLabel').textContent = 'Base price';
    showPrice(lot.base);
    setLeader(null);
  }
  renderTrail(d.bids, false);

  const UPNEXT_SHOWN = 8, more = lot.upcoming.length - UPNEXT_SHOWN;
  $('#aucUpNext').innerHTML = lot.upcoming.length
    ? `<span>Still to come in this set</span><div class="auc-up-chips">${
        lot.upcoming.slice(0, UPNEXT_SHOWN).map(n => `<i class="auc-up">${esc(n)}</i>`).join('')}${
        more > 0 ? `<i class="auc-up more">+${more} more</i>` : ''}</div>`
    : '';
  renderSide();
  markRoom(d.bids, null, false);
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
  if (!c.positions || !c.positions.length) return null;
  const lo = Math.min(...c.positions);
  return lo === 1 ? 'top' : lo === 3 ? 'middle' : lo === 5 ? 'finisher' : 'tail';
}
// [A158] Where a player can bat, as the squad panel and the twelve screen show it. A76 gives
// every card one band, so his positions are one unbroken run and a range says it exactly.
const BAND_ORDER = {top: 0, middle: 1, finisher: 2, tail: 3};
const BAND_SHORT = {top: 'Top', middle: 'Middle', finisher: 'Finisher', tail: 'Lower'};
function posRange(c){
  if (!c.positions || !c.positions.length) return '';
  const lo = Math.min(...c.positions), hi = Math.max(...c.positions);
  return lo === hi ? `${lo}` : `${lo}–${hi}`;
}
function bandTag(c){
  const b = bandOf(c);
  return b ? `<em class="auc-band band-${b}" title="${BAND_SHORT[b]} order: can bat at ${posRange(c)}"><span class="bn">${BAND_SHORT[b]} </span>${posRange(c)}</em>` : '';
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
    <div class="auc-team${t.short === d.you ? ' you' : ''}" data-team="${t.short}">
      ${chip(t.short)}${t.owner ? `<em class="auc-owner">${esc(t.owner)}</em>` : ''}
      <div class="auc-team-bar crest-${t.short}"><i style="width:${100 * t.purse / d.purse_total}%;background:var(--team)"></i></div>
      <b>${cr(t.purse)}</b>
      <span>${t.players}/${d.squad_size}${t.overseas ? ` · ${t.overseas} OS` : ''}${d.mega ? ` · ${t.rtm} RTM` : ''}</span>
    </div>`).join('');

  $('#aucSoldCount').textContent = d.sold;
  $('#aucFeed').innerHTML = d.recent.slice().reverse().slice(0, 14).map(s => `
    <div class="auc-feed-row${s.team ? '' : ' unsold'}${s.team === d.you ? ' mine' : ''}">
      <span class="auc-feed-name">${s.name} <em>${s.season_year || ''}</em>${s.rtm_holder && s.rtm_matched ? ' <em class="auc-rtm-tag">RTM</em>' : ''}</span>
      ${s.team ? `${chip(s.team)}<b class="${tierOf(s.price)}">${cr(s.price)}</b>` : '<b class="dim">unsold</b>'}
    </div>`).join('') || '<div class="note">Nothing sold yet.</div>';

  renderSquad(d, you);
}

// --- your squad ------------------------------------------------------------------------------
//
// The eighteen places read as a to-do list: what the squad still lacks for a legal twelve
// (a keeper, five bowling options) is written into the empty places, and the purse is shown
// split into what was kept, what was bought and what is left. The counts use the same
// predicates the twelve's legality check does -- `keeper_eligible` and `has_bowl` -- so
// "5/5 bowling" here cannot disagree with the twelve screen.

const KIND_ORDER = {keeper: 0, batter: 1, allrounder: 2, bowler: 3, unrated: 4};
let SQUAD_SEEN = new Set();   // person_ids already drawn, so only a new arrival animates

function squadNeedsEl(){
  let el = $('#aucSquadNeeds');
  if (!el){
    el = document.createElement('div');
    el.id = 'aucSquadNeeds';
    el.className = 'auc-squad-needs';
    $('#aucSquad').before(el);
  }
  return el;
}

function renderSquad(d, you){
  const cards = d.squad.map(s => s.card);
  const bowlNeed = (META && META.bowlers_needed) || 5;
  const keepers = cards.filter(c => c.keeper_eligible).length;
  const bowlers = cards.filter(c => c.has_bowl).length;
  const overseas = cards.filter(c => c.overseas === true).length;
  const osCap = d.squad_overseas_cap || 6;
  const open = d.squad_size - cards.length;

  const kept = d.squad.filter(s => s.retained).reduce((n, s) => n + s.price, 0);
  const bought = d.squad.filter(s => !s.retained).reduce((n, s) => n + s.price, 0);
  const pct = x => (100 * x / d.purse_total).toFixed(2) + '%';
  const pill = (label, have, need, cls) =>
    `<span class="auc-need ${cls}"><b>${have}/${need}</b>${label}</span>`;

  squadNeedsEl().innerHTML = `
    <div class="auc-spend crest-${d.you}">
      <div class="auc-spend-bar">
        ${kept ? `<i class="kept" style="width:${pct(kept)}"></i>` : ''}
        <i class="bought" style="width:${pct(bought)}"></i>
      </div>
      <div class="auc-spend-key">
        ${d.mega || kept ? `<span class="kept">Kept <b>${cr(kept)}</b></span>` : ''}
        <span class="bought">Bought <b>${cr(bought)}</b></span>
        <span class="left">Left <b>${cr(you.purse)}</b></span>
      </div>
    </div>
    <div class="auc-needs">
      ${pill('keeper', Math.min(keepers, 1), 1, keepers >= 1 ? 'ok' : 'want')}
      ${pill('bowling options', Math.min(bowlers, bowlNeed), bowlNeed, bowlers >= bowlNeed ? 'ok' : 'want')}
      ${pill('overseas', overseas, osCap, overseas >= osCap ? 'full' : '')}
      <span class="auc-need plain"><b>${open}</b>place${open === 1 ? '' : 's'} left</span>
    </div>
    <div class="auc-bands">${Object.keys(BAND_ORDER).map(b => {
      const n = cards.filter(c => bandOf(c) === b).length;
      return `<span class="auc-band band-${b}${n ? '' : ' none'}"><b>${n}</b> ${BAND_SHORT[b]}</span>`;
    }).join('')}</div>`;

  $('#aucSquadCount').textContent = `${d.squad.length}/${d.squad_size}`;
  // Grouped by where they bat, top order first -- the order a twelve is built in.
  const filled = d.squad.slice().sort((a, b) =>
    (BAND_ORDER[bandOf(a.card)] ?? 9) - (BAND_ORDER[bandOf(b.card)] ?? 9)
    || (KIND_ORDER[a.card.kind] ?? 9) - (KIND_ORDER[b.card.kind] ?? 9) || b.price - a.price);
  // What the empty places are for, in the order a drafter would chase them.
  const wants = [];
  if (!keepers) wants.push('Keeper');
  for (let i = bowlers; i < bowlNeed; i++) wants.push('Bowler');

  const slots = filled.map(s => {
    const c = s.card, isNew = SQUAD_SEEN.size && !SQUAD_SEEN.has(c.person_id);
    return `
      <div class="auc-slot filled ${crestClass(c.crest)}${isNew ? ' new' : ''}"
        onclick='showStat(${JSON.stringify(c).replace(/'/g, "&#39;")})'>
        <div class="auc-slot-top">${ICON[c.kind] || ''}${c.overseas === true ? '<em class="auc-slot-os">OS</em>' : ''}
          ${c.rating != null ? `<i class="auc-slot-rt">${c.rating}</i>` : ''}</div>
        <span>${c.name}</span>${bandTag(c)}<b class="${tierOf(s.price)}">${cr(s.price)}${s.retained ? ' · kept' : ''}</b>
      </div>`;
  });
  for (let i = 0; i < open; i++){
    const want = wants[i];
    slots.push(want ? `<div class="auc-slot want"><span>${want}</span><em>needed</em></div>`
                    : '<div class="auc-slot"></div>');
  }
  $('#aucSquad').innerHTML = slots.join('');
  SQUAD_SEEN = new Set(cards.map(c => c.person_id));
  if (!SQUAD_SEEN.size) SQUAD_SEEN = new Set(['']);   // an empty squad has "seen" nothing yet
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

// Room mode: what a single-player move means as a ROOM move. Each names the lot on screen
// [A146], so one that arrives after that lot closed is refused rather than landing on the
// next player -- and so the room can safely retry it if the answer is lost.
function roomMove(path, body){
  const on = (A && A.lot) ? {lot: A.lot.lot, round: A.lot.round} : {};
  if (path === 'bid' && body.done) return ['limit', {max: body.ceiling, ...on}];
  if (path === 'bid') return ['bid', {price: body.ceiling, ...on}];
  if (path === 'pass') return ['pass', {...body, ...on}];
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
  let lastWhole = null;
  const tick = () => {
    const left = Math.max(0, total - (performance.now() - start));
    fill.style.strokeDashoffset = C * (1 - left / total);
    num.textContent = Math.ceil(left / 1000);
    const whole = Math.ceil(left / 1000);
    if (whole !== lastWhole && whole > 0 && whole <= 3){ play('tick'); announceClosing(whole); showClosing(whole); }
    lastWhole = whole;
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
  let lastWhole = null;
  const tick = () => {
    const left = Math.max(0, window.roomAuctionDeadline());
    fill.style.strokeDashoffset = C * (1 - Math.min(1, left / total));
    num.textContent = Math.ceil(left);
    const whole = Math.ceil(left);
    if (whole !== lastWhole && whole > 0 && whole <= 3){ play('tick'); announceClosing(whole); showClosing(whole); }
    if (whole > 3) clearClosing();
    lastWhole = whole;
    ring.classList.toggle('urgent', left < 4);
  };
  tick();
  COUNTDOWN = setInterval(tick, 200);
}

function stopCountdown(){
  clearInterval(COUNTDOWN); COUNTDOWN = null;
  clearClosing();
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
  play('attention');
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
  const sim = $('#aucSimMode');
  if (sim) sim.classList.toggle('hide', AUCTION_ROOM);
  if (!TWELVE) TWELVE = (d.twelve || d.suggestion || []).slice();
  drawTwelve();
}

function slotName(i){ return i === 11 ? 'Impact' : `No. ${i + 1}`; }

function drawTwelve(){
  const d = A, squad = d.squad;
  const inTwelve = new Set(TWELVE);
  // [A158] With a place selected, everyone who could fill it is marked and everyone who
  // could not is dimmed -- in the twelve (a swap) and on the bench alike.
  const fits = (c, i) => i === 11 || c.positions.includes(i + 1);
  const hint = c => TWELVE_SEL == null ? '' : fits(c, TWELVE_SEL) ? ' fits' : ' nofit';
  $('#aucOrder').innerHTML = TWELVE.map((si, i) => {
    const s = squad[si], c = s.card;
    const ok = fits(c, i);
    const swap = TWELVE_SEL != null && TWELVE_SEL !== i
      ? (fits(c, TWELVE_SEL) && fits(squad[TWELVE[TWELVE_SEL]].card, i) ? ' fits' : ' nofit') : '';
    return `<div class="order-row auc-order-row${TWELVE_SEL === i ? ' sel' : ''}${ok ? '' : ' bad'}${swap}"
        onclick="twelveTap(${i})">
      <span class="auc-pos">${slotName(i)}</span>${ICON[c.kind] || ''}${keeperBadge(c)}
      <span class="auc-o-name">${c.name}</span>${bandTag(c)}${ratingBadge(c, true)}<b>${cr(s.price)}</b>
    </div>`;
  }).join('');
  $('#aucBench').innerHTML = squad.map((s, si) => inTwelve.has(si) ? '' : `
    <div class="order-row auc-order-row bench${TWELVE_SEL != null ? ' target' : ''}${hint(s.card)}" onclick="benchTap(${si})">
      ${ICON[s.card.kind] || ''}${keeperBadge(s.card)}
      <span class="auc-o-name">${s.card.name}</span>${bandTag(s.card)}${ratingBadge(s.card, true)}<b>${cr(s.price)}</b>
    </div>`).join('');

  const cards = TWELVE.map(i => squad[i].card);
  const problems = [];
  cards.forEach((c, i) => { if (i < 11 && !c.positions.includes(i + 1)) problems.push(`${c.name} cannot bat at ${i + 1}`); });
  if (!cards.some(c => c.keeper_eligible)) problems.push('no wicketkeeper');
  // The predicate order_errors itself uses (A155), so this cannot pass what the server refuses.
  const bowlers = cards.filter(c => c.has_bowl).length;
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

// [A148] How the season plays once the twelve is in -- the draft's own three choices. This
// was hard-coded to 'whole' when the auction was built, so an auction season went straight
// to the final table and nobody ever saw a toss or a ball of it. A room plays its own match
// phase instead, so the choice is single-player only (renderTwelve hides it in a room).
let AUC_SIM_MODE = 'whole';

function setAucSimMode(mode){
  AUC_SIM_MODE = mode;
  document.querySelectorAll('#aucSimModeChoices .room-choice').forEach(b =>
    b.classList.toggle('sel', b.dataset.simmode === mode));
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
      location.href = `/season?enter=${seasonEnterFor(AUC_SIM_MODE)}#${d.state}`;
    } catch(e){ slip(e.message); }
  });
}

// --- boot ----------------------------------------------------------------------------------

async function auctionBoot(){
  loadMe();
  // Awaited: the franchise buttons and every team chip draw their crest from META.
  try { renderDeckStats(await loadMeta()); } catch(e) { /* chips fall back to the code alone */ }
  const h = location.hash.slice(1);
  if (!h){ renderSetup(); return; }
  try { await apply(await api('/api/auction/' + h), false); }
  catch(e){ slip(e.message); history.replaceState(null, '', location.pathname); renderSetup(); }
}
if (!AUCTION_ROOM) auctionBoot();
