// The career profile page: games played, titles won, and the top 5 batters / top 5
// bowlers by total runs/wickets across every game this account has saved (solo or
// room, migration 027). Read-only -- nothing here writes anything; the save hooks live
// in season.js/room.js instead, right where a game actually completes.

// A row is a bar: width is this player's share of the LEADER's total, so the gap between
// first and fifth is visible without reading a single number. Guarded against a zero max
// (a saved game where nobody scored) rather than dividing by it.
function capRows(rows, unit){
  if (!rows.length){
    return '<div class="cap-empty">Play and save a game to start filling this.</div>';
  }
  const max = Math.max(...rows.map(r => r.total)) || 1;
  return rows.map((r, i) => `
    <div class="cap-row${i === 0 ? ' lead' : ''}">
      <span class="bar" style="width:${Math.max(4, Math.round(100 * r.total / max))}%"></span>
      <span class="rk">${i + 1}</span>
      <span class="nm">${r.name}</span>
      <span class="val">${r.total} ${unit}</span>
    </div>`).join('');
}

function render(p){
  $('#profileUsername').textContent = p.username;
  // Derived, not stored -- and a dash rather than a fabricated 0% before any game exists,
  // the same "no evidence is a dash" convention the ratings use (A33/A43).
  const rate = (won, of) => of ? Math.round(100 * won / of) + '%' : '–';
  const n = v => (v == null ? '–' : v.toLocaleString());
  $('#profileGames').textContent = n(p.games_played);
  $('#profileTitles').textContent = n(p.titles_won);
  $('#profileRate').textContent = rate(p.titles_won, p.games_played);

  // `matches_won` already counts room matches, so solo is the difference.
  $('#soloGames').textContent = n(p.solo_games);
  $('#soloTitles').textContent = n(p.solo_titles);
  $('#soloMatches').textContent = n(p.matches_won - p.friend_matches_won);
  $('#roomGames').textContent = n(p.room_games);
  $('#roomTitles').textContent = n(p.friend_titles);
  $('#roomMatches').textContent = n(p.friend_matches_won);

  $('#totRuns').textContent = n(p.total_runs);
  $('#totWickets').textContent = n(p.total_wickets);

  $('#dailyStreak').textContent = p.daily_streak ? `🔥 ${p.daily_streak}` : '0';
  $('#dailyBest').textContent = n(p.daily_best);
  $('#dailyPlayed').textContent = n(p.daily_played);
  // Oldest on the left, as a form guide reads; a day is met or missed, nothing else.
  $('#dailyForm').innerHTML = p.recent_dailies.length
    ? [...p.recent_dailies].reverse().map(d => `<i class="${d.objective_met ? 'w' : 'l'}"
        title="${esc(d.challenge_date)} · ${d.objective_met ? 'met' : 'missed'}">${d.objective_met ? '✓' : '✕'}</i>`).join('')
    : '<span class="cap-empty">No daily played yet.</span>';

  $('#recentGames').innerHTML = p.recent_games.length
    ? p.recent_games.map(recentGameRow).join('')
    : '<div class="cap-empty">Play and save a season, or finish a room, to start your record.</div>';

  renderProfileKit();
  $('#profileBatters').innerHTML = capRows(p.top_batters, 'runs');
  $('#profileBowlers').innerHTML = capRows(p.top_bowlers, 'wkts');
}

// A saved game stored its kind, whether it was won and its match record -- and nothing
// else, so that is all a row can say. Rows saved before migration 028 have no record.
function recentGameRow(g){
  const when = new Date(g.completed_at);
  const date = isNaN(when) ? '' : when.toLocaleDateString(undefined, {day: 'numeric', month: 'short'});
  const rec = g.matches_played != null
    ? `${g.matches_won}–${g.matches_played - g.matches_won}` : '–';
  return `<div class="pf-game${g.champion ? ' champ' : ''}">
      <span class="pf-game-kind">${g.source === 'room' ? 'With friends' : 'Solo season'}</span>
      <span class="pf-game-rec" title="matches won–lost">${rec}</span>
      <span class="pf-game-res">${g.champion ? '🏆 Champions' : ''}</span>
      <span class="pf-game-date">${esc(date)}</span>
    </div>`;
}

// All three requests go out AT ONCE. They used to run strictly in series -- loadMeta,
// then loadMe, then /api/profile -- which put three full round trips on the critical path
// before anything rendered, and this page is the only one that did it (every other page
// fires loadMe fire-and-forget alongside loadMeta).
//
// The serial version's own reasoning was that the content "depends entirely on being
// logged in, so there's nothing useful to show before this resolves." True of the
// RENDER, and it does not follow that the REQUEST has to wait: `/api/profile` already
// answers 401 for a signed-out caller, so its own response carries the same fact
// `loadMe()` was being awaited for. Firing it immediately costs a wasted request in the
// signed-out case -- who is redirected away anyway -- and saves two round trips in the
// case that matters.
// [A151] Your team kit -- what your drafted sides wear in solo, the daily and rooms. This
// page is signed-in only, so saving here always saves to the account.
function renderProfileKit(){
  const kit = myKit();
  $('#profileBadge').innerHTML = kitBadge(kit, 'pf-badge');
  $('#profileTeam').textContent = kit.name;
  $('#profileHero').setAttribute('style', kitStyle(kit));
}
document.addEventListener('kitchange', () => { if (ME && ME.account_id) renderProfileKit(); });

async function boot(){
  const meta = loadMeta().then(m => { renderDeckStats(m); });
  const me = loadMe();
  const profile = api('/api/profile').catch(err => err);   // 401 handled below, not thrown

  await Promise.all([meta, me]);
  if (!ME || !ME.account_id){
    location.href = '/';
    return;
  }
  const p = await profile;
  if (p instanceof Error){ slip(p.message); return; }
  render(p);
}

boot();
