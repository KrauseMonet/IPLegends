// What every puzzle game shares [A183]: where its progress is kept, how a streak is counted,
// how a player's name is searched, and how a result is shared. Bingo and Guess the Player
// each had (or would have had) their own copy of these; a third would have been a third place
// for A130's streak rule to drift, so they live here.
//
// Nothing here knows about a particular game. A game passes its own storage key and its own
// text, and keeps its own rules.

// A shared link has to work for whoever reads it, so it names the real site rather than
// whatever host this page happens to be on (A129).
const PZ_ORIGIN = 'https://iplegends.vercel.app';
const PZ_JSON = {'Content-Type': 'application/json'};

// --- storage: one JSON object per game, in this browser only ------------------------------

function pzStore(key){
  try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; }
  catch(e){ return {}; }
}
function pzSave(key, patch){
  try { localStorage.setItem(key, JSON.stringify(Object.assign(pzStore(key), patch))); }
  catch(e){ /* private window, quota: the game still plays, it just will not resume */ }
}

// --- streak -------------------------------------------------------------------------------
// A streak is not broken until a day has actually been MISSED (A130): playing yesterday and
// not yet today leaves it alive, because nothing has been lost, only not yet extended. And
// "consecutive" means the next calendar DAY, not the next day-of-month.

function pzDayNum(iso){ return Math.round(Date.parse(iso + 'T00:00:00Z') / 86400000); }

function pzStreaks(days, todayIso){
  const set = new Set(days.map(pzDayNum));
  const today = pzDayNum(todayIso);
  let cur = 0, d = set.has(today) ? today : today - 1;
  while (set.has(d)){ cur++; d--; }
  let best = 0, run = 0, prev = null;
  for (const n of [...set].sort((a, b) => a - b)){
    run = prev !== null && n === prev + 1 ? run + 1 : 1;
    best = Math.max(best, run); prev = n;
  }
  return {cur, best};
}
// --- end streak

// The line under a daily's tabs. `kind` is what finishing means in this game ("grid",
// "puzzle"), so the nudge reads right.
function pzStreakHtml(days, todayIso, kind){
  const {cur, best} = pzStreaks(days, todayIso);
  if (!cur) return best ? `Best streak: ${best} days.` : '';
  const s = `Streak: <b>${cur}</b> day${cur === 1 ? '' : 's'}.`;
  return days.includes(todayIso) ? s : `${s} Finish today's ${kind} to keep it.`;
}

// --- names --------------------------------------------------------------------------------

function pzNorm(s){
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
          .replace(/[^a-z0-9 ]/g, ' ').trim();
}

let PZ_PLAYERS = null;
async function pzPlayers(){
  if (!PZ_PLAYERS){
    const list = await api('/api/puzzles/players');
    PZ_PLAYERS = list.map(p => ({id: p.id, name: p.name, words: pzNorm(p.name).split(/\s+/)}));
  }
  return PZ_PLAYERS;
}

// The best eight matches for what was typed, each token a prefix of some word of the name,
// a surname match ranking first. `whyNot(p)` returns a reason a player cannot be chosen
// ("on the grid", "tried here"), or '' -- those are listed but disabled, so the player can
// see WHY a name will not go in rather than wondering where it went. `active` is the first
// choosable one.
function pzMatches(query, whyNot){
  const q = pzNorm(query);
  if (!q || !PZ_PLAYERS) return {list: [], active: 0};
  const toks = q.split(/\s+/);
  const scored = [];
  for (const p of PZ_PLAYERS){
    let score = 0;
    for (const t of toks){
      const k = p.words.findIndex(w => w.startsWith(t));
      if (k < 0){ score = -1; break; }
      score += k === p.words.length - 1 ? 0 : 1;      // a surname match ranks first
    }
    if (score >= 0) scored.push({p, score});
  }
  scored.sort((a, b) => a.score - b.score || a.p.name.localeCompare(b.p.name));
  const list = scored.slice(0, 8).map(x => ({p: x.p, why: whyNot ? whyNot(x.p) : ''}));
  const active = Math.max(0, list.findIndex(x => !x.why));
  return {list, active};
}

// The result list's markup, shared so both games' pickers look and behave alike. `pick` is
// the name of the global function a click calls with the person id.
function pzResultsHtml(m, pick, emptyNote){
  if (!m.list.length) return `<li class="bg-none">${emptyNote}</li>`;
  return m.list.map((x, k) => `<li><button class="${k === m.active ? 'act-row' : ''}"
      ${x.why ? 'disabled' : ''} onclick="${pick}('${esc(x.p.id)}')">${esc(x.p.name)}${
      x.why ? `<em>${x.why}</em>` : ''}</button></li>`).join('');
}

// --- sharing ------------------------------------------------------------------------------
// The text is the server's, never assembled here, so its wording cannot drift from the
// scoring (A129). The ladder: the phone's share sheet, then the clipboard, then showing the
// text so it can still be copied by hand.

async function pzShare(text, copied){
  if (!text){ slip('Finish it first.'); return; }
  if (navigator.share){
    try { await navigator.share({text}); return; }
    catch(e){ if (e && e.name === 'AbortError') return; }    // closing the sheet is not an error
  }
  try { await navigator.clipboard.writeText(text); slip(copied || 'Result copied. It has no names in it, so post it anywhere.'); }
  catch(e){ slip(text); }
}

async function pzCopyLink(url){
  try { await navigator.clipboard.writeText(url); slip('Link copied.'); }
  catch(e){ slip(url); }
}
