// The career profile page: games played, titles won, and the top 5 batters / top 4
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
  $('#profileUsername').textContent = '@' + p.username;
  $('#profileGames').textContent = p.games_played;
  $('#profileTitles').textContent = p.titles_won;
  // Derived, not stored -- and a dash rather than a fabricated 0% before any game exists,
  // the same "no evidence is a dash" convention the ratings use (A33/A43).
  $('#profileRate').textContent = p.games_played
    ? Math.round(100 * p.titles_won / p.games_played) + '%' : '–';
  const n = v => (v == null ? '–' : v.toLocaleString());
  $('#totRuns').textContent = n(p.total_runs);
  $('#totWickets').textContent = n(p.total_wickets);
  $('#totMatches').textContent = n(p.matches_won);
  $('#totFriendMatches').textContent = n(p.friend_matches_won);
  $('#totLeagues').textContent = n(p.solo_titles);
  $('#totFriendLeagues').textContent = n(p.friend_titles);

  $('#profileBatters').innerHTML = capRows(p.top_batters, 'runs');
  $('#profileBowlers').innerHTML = capRows(p.top_bowlers, 'wkts');
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
