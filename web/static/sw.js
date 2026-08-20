// The service worker. Its job here is INSTALLABILITY and fast repeat loads -- not offline
// play, which this app cannot honestly offer: the draft, the daily and every room need the
// API, so pretending otherwise would mean an installed icon that opens into a dead screen.
//
// Three rules, and the first two exist because of specific things this project has already
// been bitten by.
//
// 1. NEVER cache /api/*. A room polls every two seconds, the daily allows one attempt, and
//    a leaderboard is live. A cached API response is not a stale nicety here, it is a wrong
//    answer -- and A119 has already spent a session chasing a client that acted on state
//    older than it thought.
//
// 2. HTML is NETWORK-FIRST, cache only as an offline fallback. A106 shipped a real
//    regression by letting a cached page pair with a newer script; pages are served
//    must-revalidate for exactly that reason, and a cache-first worker would quietly undo
//    it. The content-hashed asset URLs (A106's stamping) mean a fresh page always asks for
//    the assets it was built with.
//
// 3. Hashed static assets are CACHE-FIRST and long-lived, which is safe precisely because
//    the URL changes when the content does. That is the whole point of stamping them.
const VERSION = 'almanack-v1';
const SHELL = `${VERSION}-shell`;
const ASSETS = `${VERSION}-assets`;
const PAGES = `${VERSION}-pages`;

// Kept deliberately small: the offline card and the icons it shows. Everything else is
// cached as it is actually used, so a precache list cannot go stale against a deploy.
const PRECACHE = ['/offline.html', '/static/icon-192.png'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL)
      .then(c => c.addAll(PRECACHE))
      // A precache failure must not block installation -- an app that will not install
      // because one icon 404'd is worse than one without an offline card.
      .catch(() => {})
      .then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(names => Promise.all(
        names.filter(n => !n.startsWith(VERSION)).map(n => caches.delete(n))))
      .then(() => self.clients.claim()));
});

function isStaticAsset(url){
  return url.pathname.startsWith('/static/');
}

self.addEventListener('fetch', event => {
  const req = event.request;
  // Only GET. A pick, a toss, a submission must always reach the server, and a worker
  // that answered one from a cache would be inventing a result.
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // never touch a third party
  if (url.pathname.startsWith('/api/')) return;      // rule 1: live data, always network

  if (isStaticAsset(url)){
    // Rule 3. Cache-first: these URLs carry a content hash, so a hit is by definition the
    // right bytes for whatever page asked.
    event.respondWith(
      caches.match(req).then(hit => hit || fetch(req).then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(ASSETS).then(c => c.put(req, copy)); }
        return res;
      })));
    return;
  }

  // Rule 2. Pages: network first, and the cache is a fallback for being offline rather
  // than a source of truth. A deploy is therefore picked up on the next load, exactly as
  // it is without a worker installed.
  event.respondWith(
    fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(PAGES).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then(hit => hit || caches.match('/offline.html'))));
});
