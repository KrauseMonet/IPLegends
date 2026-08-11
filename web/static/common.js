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
async function busyClick(ctrl, busyLabel, fn){
  const scope = ctrl.closest('section') || document.body;
  scope.classList.add('busy-wait');
  const relabel = busyLabel && ctrl.tagName === 'BUTTON';
  const prevText = ctrl.textContent;
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
async function loadMeta(){
  META = await api('/api/meta');
  return META;
}
