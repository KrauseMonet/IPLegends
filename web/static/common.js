// Cross-feature utilities shared by every page (home, draft, season, rooms, room).
// Deliberately minimal: only functions with no dependency on any page-specific global
// (DRAFT_MODE, ROOM_CODE, SEASON_STATE, etc.) live here. Anything whose behavior varies
// by page (e.g. ratingBadge's draft-mode gating) stays defined per-page instead, even at
// the cost of a little duplication -- see CLAUDE.md's own "no premature abstraction"
// stance and SPEC.md's "no heavyweight framework" line, which this split follows rather
// than reaching for a templating/module system this project has never used.

const $ = s => document.querySelector(s);

// Every request that ever hung indefinitely -- diagnosed as drafts intermittently
// freezing on a pick, the whole panel dimmed by busyClick's own busy-wait with no way
// out -- did so because nothing here ever gave up on it. The server now fails a stuck
// request fast on its own (web/app.py's lock_timeout/connect_timeout), but a request
// can still stall in flight (a slow network, a cold path the server-side bound doesn't
// cover) with nothing to time IT out from this side. API_TIMEOUT_MS bounds that: past
// it the fetch is aborted and rejects with a clear, retryable message instead of
// leaving the caller waiting on a promise that may never settle. Comfortably above a
// normal round trip (every request here is at least one Neon hop) but well under
// anything a user would sit through without assuming something broke.
const API_TIMEOUT_MS = 15000;

async function api(path, opts){
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), API_TIMEOUT_MS);
  let r;
  try {
    r = await fetch(path, {...opts, signal: ctrl.signal});
  } catch(e){
    if (e.name === 'AbortError'){
      throw new Error('That took too long to respond -- the server may be busy. Try again.');
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    // `.status` carried on the thrown Error, not just the message text -- lets a
    // caller (room-session resume, elsewhere) tell "this is genuinely gone" (404) apart
    // from "the server is busy" or a transient network blip, without parsing prose.
    const err = new Error(body.detail || r.statusText);
    err.status = r.status;
    throw err;
  }
  return body;
}

function slip(msg){
  const t = document.createElement('div');
  t.className = 'slip'; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 4000);
}

// Real feedback for a click that waits on the network (every request here is at least
// one round trip to Neon through Vercel, never truly instant): freeze the entire screen
// the control lives on so a second click can't fire a second request, and relabel the
// control itself so the wait is visibly registered rather than looking dead. `busyLabel`
// only ever replaces a <button>'s text -- a clicked row (a "Take" target, an order-sheet
// slot) can be a complex element with its own child spans, and blanking its content to
// plain text would be a worse flash than just dimming it via the section-wide freeze;
// pass null for those callers and rely on the freeze alone.
// `ctrl` may be null: some calls are made by a timer rather than a click (a room's own
// auto-advance countdown), and those have no button to dim or relabel. The busy scope
// still applies -- the request is just as real -- so the page still shows it is working.
async function busyClick(ctrl, busyLabel, fn){
  const scope = (ctrl && ctrl.closest('section')) || document.body;
  scope.classList.add('busy-wait');
  const relabel = busyLabel && ctrl && ctrl.tagName === 'BUTTON';
  const prevText = ctrl ? ctrl.textContent : null;
  if (relabel) ctrl.textContent = busyLabel;
  try { await fn(); }
  finally {
    scope.classList.remove('busy-wait');
    if (relabel) ctrl.textContent = prevText;
  }
}

// META is fetched once per page load and used by whichever page-specific code needs it
// (home's own stat row, draft's deck size references, etc.) -- loadMeta() does only the
// fetch; each page's own boot does its own page-specific rendering with the result.
let META = null;
// `/api/meta` is the deck's SHAPE -- season range, squad count, twelve size, overseas cap.
// It changes only when the ETL chain re-runs and the site is redeployed, yet this is a
// multi-page app, so every Home -> Draft -> Season -> Rooms navigation is a full page load
// that was paying a serverless round trip for it. Worse, the four content pages
// (about/faq/terms/privacy) are otherwise pure static and were waking the function purely
// to fill a masthead pill.
//
// Cache-first with background revalidation, NOT a plain TTL cache: the cached copy renders
// immediately and a fresh copy is fetched anyway, so a stale value can survive at most the
// one page view that displayed it, and the next navigation is already correct. That
// matters because `meta` carries board constants, not just decoration -- the point of
// serving them (A19: no second copy of the rules in JavaScript) would be lost if a cache
// could pin an old shape indefinitely.
const META_CACHE_KEY = 'iplegends_meta_v1';
const META_TTL_MS = 6 * 60 * 60 * 1000;

function readMetaCache(){
  try {
    const raw = localStorage.getItem(META_CACHE_KEY);
    if (!raw) return null;
    const {at, meta} = JSON.parse(raw);
    return (Date.now() - at) < META_TTL_MS ? meta : null;
  } catch(e){ return null; }   // private browsing, disabled storage, corrupt entry
}

function writeMetaCache(meta){
  try { localStorage.setItem(META_CACHE_KEY, JSON.stringify({at: Date.now(), meta})); }
  catch(e){ /* caching is an optimisation; failing to cache must never fail a page */ }
}

async function loadMeta(){
  const cached = readMetaCache();
  if (cached){
    META = cached;
    // Revalidate anyway. Deliberately not awaited: the caller renders off the cached copy
    // now, and this corrects the stored copy for the next navigation.
    api('/api/meta').then(fresh => {
      writeMetaCache(fresh);
      if (JSON.stringify(fresh) !== JSON.stringify(cached)){ META = fresh; renderDeckStats(fresh); }
    }).catch(() => { /* offline or cold: the cached copy is still serviceable */ });
    return META;
  }
  META = await api('/api/meta');
  writeMetaCache(META);
  return META;
}

// The two masthead/footer elements every page shares. Pulled out of the four callers so a
// background revalidation can repaint them without knowing which page it is on; a page
// that lacks either element simply skips it.
function renderDeckStats(m){
  const s = m.seasons;
  const pill = $('#deckPill'), foot = $('#footStats');
  if (pill) pill.textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  if (foot) foot.textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
}

// A bat, a ball, both, a glove. Drawn at 16x16 on a shared grid so they sit on one
// baseline; the ball is the only filled mark, which is what makes a bowler scannable.
const BAT  = '<path d="M10.6 2.2 13.4 5 7.2 11.2 4.4 8.4Z"/><path d="M4.4 8.4 2.4 12.6l4.2-1.4"/>';
const BALL = '<circle cx="8" cy="8" r="5.4"/><path d="M4.6 4.1a9 9 0 0 1 0 7.8M11.4 4.1a9 9 0 0 0 0 7.8"/>';
const GLOVE= '<path d="M4.4 13.4V6.8a1.4 1.4 0 0 1 2.8 0V4.2a1.4 1.4 0 0 1 2.8 0v1.2a1.4 1.4 0 0 1 2.4 1v6a2 2 0 0 1-2 2H6.4a2 2 0 0 1-2-2Z"/>';
function svg(inner){ return `<svg viewBox="0 0 16 16" aria-hidden="true">${inner}</svg>`; }
const ICON = {
  batter:     `<span class="ic bat" title="Batter">${svg(BAT)}</span>`,
  bowler:     `<span class="ic ball" title="Bowler">${svg(BALL)}</span>`,
  allrounder: `<span class="ic all" title="All-rounder">${svg(
      '<path d="M9.6 1.6 12 4 7 9 4.6 6.6Z"/><path d="M4.6 6.6 2.8 10.4l3.8-1.3"/>' +
      '<circle cx="11.4" cy="11.4" r="3.2" fill="currentColor"/>')}</span>`,
  keeper:     `<span class="ic glove" title="Wicketkeeper">${svg(GLOVE)}</span>`,
  unrated:    `<span class="ic none" title="Not rated"></span>`,
};

// [A77] A career keeper still counts toward the squad's keeper requirement even in a
// season the archive says someone else kept -- invisible otherwise, so this small
// secondary glove marks it wherever the primary icon isn't already "keeper".
const KEEPER_BADGE =
  `<span class="ic glove-badge" title="Also a proven keeper -- kept in another season">${svg(GLOVE)}</span>`;
function keeperBadge(card){
  return (card.keeper_eligible && card.kind !== 'keeper') ? KEEPER_BADGE : '';
}

// `forceReveal` bypasses the draft_mode gate entirely -- used once the squad is done,
// on the squad-review screen, where the whole point is to finally show every rating
// regardless of how the draft itself was played. Every other call site omits it and
// keeps today's behaviour exactly. `effectiveDraftMode` is deliberately NOT defined
// here -- each page that draws cards (draft/season/room) defines its own, since what
// it reads from (a local DRAFT_MODE, or a room's shared mode) is page-specific.
// A103-era scale runs 70-99 (A58/A60), so every card's badge sat in a 30-point band and
// they all rendered identically -- a 99 and a 74 were the same small green chip, which
// threw away the one number the draft is actually played on. Four tiers, because the
// interesting question a drafter asks is "is this one special", not "what exactly is it":
// the figure itself is still there for anyone who wants it.
function ratingTier(v){
  if (v >= 95) return ' elite';    // top ~1% of the deck; four seasons ever reach 99
  if (v >= 88) return ' strong';
  if (v >= 80) return ' good';
  return '';
}

function ratingBadge(card, forceReveal){
  return ((forceReveal || effectiveDraftMode() === 'stat') && card.rating != null)
    ? `<span class="rating-badge${ratingTier(card.rating)}">${card.rating}</span>` : '';
}

// Shared by solo's finished-draft screen and a room's squad-review screen -- both sit on
// an object exposing the same three fields (`overall_rating`/`batting_rating`/
// `bowling_rating`), from `SessionOut` and `RoomPlayerOut` respectively. `--` for a null
// bucket (A33/A43's own rule: no evidence is a dash, never a fabricated 0).
function teamRatingsHtml(d){
  const tiles = [[d.batting_rating, 'BATTING'], [d.bowling_rating, 'BOWLING'], [d.overall_rating, 'OVERALL']];
  return tiles.map(([v, label]) => `<div><b>${v == null ? '--' : v}</b><span>${label}</span></div>`).join('');
}

// A slot-machine flourish for the moment a franchise-season lands on screen: a quick
// flicker through a handful of OTHER real teams before settling on the actual deal,
// fast at first and slowing down like a wheel losing momentum, so it reads as landing
// rather than a jump-cut. Purely cosmetic -- the flickered names are never the real
// deal (the actual year/team are set only on the final step), so nobody can mistake a
// mid-roll frame for the real one even on a slow connection. Shared by solo dealing and
// a room's own deal card (showRoomDeal).
const ROLL_FLAVOUR = [
  'Mumbai Indians', 'Chennai Super Kings', 'Kolkata Knight Riders',
  'Royal Challengers Bangalore', 'Delhi Capitals', 'Rajasthan Royals',
  'Punjab Kings', 'Sunrisers Hyderabad', 'Gujarat Titans', 'Lucknow Super Giants',
  'Deccan Chargers', 'Pune Warriors', 'Gujarat Lions', 'Rising Pune Supergiants',
  'Kochi Tuskers Kerala',
];
const ROLL_STEP_MS = [55, 65, 80, 95, 120, 150, 190];   // decelerating; ~755ms total
const ROLL_TIMERS = new Map();   // teamEl -> pending timeout, so overlapping calls on
                                  // the same slot cancel each other rather than racing

function rollDeal(yearEl, teamEl, finalYear, finalTeam){
  clearTimeout(ROLL_TIMERS.get(teamEl));
  let i = 0;
  const step = () => {
    if (i >= ROLL_STEP_MS.length){
      yearEl.textContent = finalYear;
      teamEl.textContent = finalTeam;
      ROLL_TIMERS.delete(teamEl);
      return;
    }
    yearEl.textContent = 2008 + Math.floor(Math.random() * 19);
    teamEl.textContent = ROLL_FLAVOUR[Math.floor(Math.random() * ROLL_FLAVOUR.length)];
    ROLL_TIMERS.set(teamEl, setTimeout(step, ROLL_STEP_MS[i]));
    i++;
  };
  step();
}

/* --- per-player season stat popover -- shared by draft, season and room, all of
   which include the same #statOverlay/#statName/#statSub/#statBody markup. --- */
function oversStr(balls){ return Math.floor(balls / 6) + '.' + (balls % 6); }

function statTile(n, label){
  return `<div class="stat"><div><b>${n}</b><span>${label}</span></div></div>`;
}

function cardStatHtml(card){
  let out = '';
  if (card.bat_balls){
    out += statTile(card.bat_runs, 'runs') + statTile(card.bat_balls, 'balls')
         + statTile(card.bat_strike_rate.toFixed(1), 'strike rate');
  }
  if (card.bowl_balls){
    out += statTile(card.bowl_wickets, 'wickets') + statTile(oversStr(card.bowl_balls), 'overs')
         + statTile(card.bowl_economy.toFixed(2), 'economy');
  }
  return out || '<div class="note">No batting or bowling record this season.</div>';
}

function showStat(card){
  if (!card) return;
  $('#statName').textContent = card.name;
  $('#statSub').textContent = [card.franchise, card.season_year].filter(Boolean).join(' · ');
  $('#statBody').innerHTML = cardStatHtml(card);
  $('#statOverlay').classList.remove('hide');
}

function hideStat(e){
  if (e && e.target !== e.currentTarget) return;
  $('#statOverlay').classList.add('hide');
}

/* --- the field wheel: this site's signature device (see style.css for the why) ---------
   A cricket field drawn as a fielding map / wagon wheel, used as real UI rather than
   decoration: one marker per SEAT, set evenly around the boundary the way a captain sets
   a field, filled when the seat is taken. Lives here rather than in rooms-setup.js because
   the same object serves the lobby at 300px and a room list row at 34px. */

const FORMAT_SEATS = {final: 2, cup: 4, league: 10};

function fieldWheel(seats, filled, opts){
  const o = opts || {};
  const cx = 100, cy = 100, R = 88;
  let spokes = '', dots = '';
  for (let i = 0; i < seats; i++){
    // Start at the top and go clockwise, so seat 1 is always at 12 o'clock however many
    // seats there are -- a field that re-centres itself as the format changes reads as a
    // different diagram rather than the same one gaining fielders.
    const a = (-90 + i * 360 / seats) * Math.PI / 180;
    const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
    const on = i < filled;
    spokes += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(2)}" y2="${y.toFixed(2)}"
      class="wheel-spoke${on ? ' on' : ''}"/>`;
    // Staggered entry: the field "sets" one fielder at a time instead of all at once.
    dots += `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${on ? 6.5 : 5}"
      class="wheel-dot${on ? ' on' : ''}" style="animation-delay:${(i * 45)}ms"/>`;
  }
  const chrome = o.mini ? '' : `
    <circle cx="100" cy="100" r="52" class="wheel-inner"/>
    <line x1="88" y1="78" x2="112" y2="78" class="wheel-crease"/>
    <line x1="88" y1="122" x2="112" y2="122" class="wheel-crease"/>
    <rect x="95" y="76" width="10" height="48" rx="1" class="wheel-pitch"/>`;
  return `<svg viewBox="0 0 200 200" class="wheel${o.mini ? ' wheel-mini' : ''}" aria-hidden="true">
    <defs><radialGradient id="wheelGrass" cx="50%" cy="42%">
      <stop offset="0%" stop-color="rgba(51,160,209,.10)"/>
      <stop offset="100%" stop-color="rgba(5,11,26,0)"/>
    </radialGradient></defs>
    <circle cx="100" cy="100" r="88" class="wheel-grass"/>
    <circle cx="100" cy="100" r="88" class="wheel-rope"/>
    ${spokes}${chrome}${dots}
  </svg>`;
}


/* --- tips ---------------------------------------------------------------------------------
   UPI rather than a hosted platform: this site's visitors are overwhelmingly IPL fans, i.e.
   overwhelmingly in India, where UPI is the default way money moves and a platform button
   is friction plus a cut. It also needs no backend at all, which matters here -- the API is
   stateless and serverless (A62), so a real payments integration would mean secrets, a
   webhook route and a table, for something a link and an image do.

   SET THIS to a real VPA to switch the feature on. While it is empty every entry point stays
   hidden rather than showing a half-built panel or, worse, a QR nobody owns. The QR itself is
   a committed static file generated by `tools/make_upi_qr.py` -- a VPA never changes, so
   generating it per request (or shipping a QR library to the browser) would be work done
   forever to produce the same bytes. */
const UPI_ID = 'mishrakoustav01-1@okaxis';
const UPI_PAYEE = 'The Legends Almanack';

function tipsEnabled(){ return !!UPI_ID; }

// `upi://pay` opens the payer's own UPI app directly. No amount is set: this is a tip, so
// the payer decides, and a fixed amount would need one QR per amount.
function upiLink(){
  return `upi://pay?pa=${encodeURIComponent(UPI_ID)}`
       + `&pn=${encodeURIComponent(UPI_PAYEE)}&cu=INR`;
}

function ensureTipChrome(){
  if (document.getElementById('tipOverlay')) return;
  const el = document.createElement('div');
  el.id = 'tipOverlay';
  el.className = 'card-overlay hide';
  el.setAttribute('onclick', 'closeTipModal(event)');
  el.innerHTML = `
    <div class="scorecard-shell tip-frame">
      <div class="scorecard-frame">
        <div class="tip-hero">
          <div class="tip-eyebrow">Support the Almanack</div>
          <div class="tip-title">Buy me a ball</div>
          <p class="tip-sub">This runs on a database, a host and a domain, paid for by one
            person. Anything you send covers those. Nothing here is paywalled and nothing
            ever will be — a tip buys you no advantage, just keeps the lights on.</p>
        </div>
        <div class="tip-body">
          <img class="tip-qr" src="/static/upi-qr.png" alt="UPI QR code" width="188" height="188">
          <div class="tip-id">
            <span class="tip-id-label">UPI ID</span>
            <code id="tipUpiId">${UPI_ID}</code>
            <button class="act" onclick="copyUpiId(this)">Copy</button>
          </div>
          <a class="act lead tip-open" href="${upiLink()}">Open your UPI app</a>
          <p class="tip-note">Scan on a laptop, or tap to open your UPI app on a phone.</p>
        </div>
      </div>
      <div class="scorecard-close"><button class="act" onclick="closeTipModal()">Close</button></div>
    </div>`;
  document.body.appendChild(el);
}

function openTipModal(){
  if (!tipsEnabled()) return;
  ensureTipChrome();
  document.getElementById('tipOverlay').classList.remove('hide');
}

function closeTipModal(e){
  if (e && e.target !== e.currentTarget) return;   // a click inside the frame stays open
  const el = document.getElementById('tipOverlay');
  if (el) el.classList.add('hide');
}

function copyUpiId(ctrl){
  navigator.clipboard.writeText(UPI_ID)
    .then(() => slip('UPI ID copied.'))
    .catch(() => slip(UPI_ID));   // same fallback shape copyLink/copyRoomCode already use
}

// Written by JS rather than sitting in ten copies of the markup, so an unset UPI_ID means
// it simply never appears. Deliberately NOT joined into the About/FAQ/Terms/Privacy row
// with a separator -- those are legal boilerplate you scan past, and sharing their row made
// this read as a fifth one. It is the only thing in the footer that DOES something, so it
// gets its own line and looks like a button.
function mountTipFooterLink(){
  if (!tipsEnabled()) return;
  document.querySelectorAll('.imprint').forEach(imp => {
    if (imp.querySelector('.tip-foot')) return;
    const row = document.createElement('div');
    row.className = 'tip-foot-row';
    row.innerHTML = `
      <button type="button" class="tip-foot" onclick="openTipModal()">
        <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.4"/>
          <path d="M4.6 4.1a9 9 0 0 1 0 7.8M11.4 4.1a9 9 0 0 0 0 7.8"/></svg>
        Support this project
      </button>`;
    imp.insertBefore(row, imp.firstChild);
  });
}

document.addEventListener('DOMContentLoaded', mountTipFooterLink);


// --- installable app -----------------------------------------------------------------------
//
// Registered from `common.js` because every page loads it, so the worker is picked up
// wherever a visitor happens to land rather than only on the home page.
//
// Registered at '/sw.js' and NOT '/static/sw.js', which is not a tidiness preference: a
// worker's scope is its own directory, so one served from /static could only ever control
// /static and would never see a page request at all.
// iOS gets the button revealed on load, since the event that reveals it elsewhere never
// arrives there -- and hidden once the app is actually running standalone, where offering
// to install it again is nonsense.
window.addEventListener('load', () => {
  if (alreadyInstalled()) return;
  if (isIOS()) document.querySelectorAll('[data-install]').forEach(el => el.classList.remove('hide'));
});

if ('serviceWorker' in navigator){
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Not worth telling anyone about. Without a worker the site behaves exactly as it
      // always has -- it simply cannot be installed.
    });
  });
}

// Chrome and Edge fire this instead of showing their own prompt, so the invitation has to
// be ours. Captured rather than acted on: firing it unprompted is the pattern browsers
// added this event to stop.
let INSTALL_PROMPT = null;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  INSTALL_PROMPT = e;
  document.querySelectorAll('[data-install]').forEach(el => el.classList.remove('hide'));
});
window.addEventListener('appinstalled', () => {
  INSTALL_PROMPT = null;
  document.querySelectorAll('[data-install]').forEach(el => el.classList.add('hide'));
});

// iOS never fires `beforeinstallprompt` and has no programmatic install at all -- the only
// route is Share > Add to Home Screen, so there it is instructions or nothing.
function isIOS(){
  return /iP(hone|ad|od)/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function alreadyInstalled(){
  return window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
}

async function installApp(btn){
  if (INSTALL_PROMPT){
    INSTALL_PROMPT.prompt();
    try { await INSTALL_PROMPT.userChoice; } catch(e){ /* dismissed */ }
    INSTALL_PROMPT = null;
    return;
  }
  if (isIOS()){
    slip('In Safari: tap Share, then "Add to Home Screen".');
    return;
  }
  slip('Use your browser menu — look for "Install" or "Add to Home Screen".');
}
