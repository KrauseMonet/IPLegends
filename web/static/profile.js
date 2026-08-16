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

async function boot(){
  const m = await loadMeta();
  const s = m.seasons;
  $('#deckPill').textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  $('#footStats').textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
}

boot().then(async () => {
  await loadMe();   // awaited here, unlike every other page -- this page's own content
                     // depends entirely on being logged in, so there's nothing useful
                     // to show before this resolves.
  if (!ME || !ME.account_id){
    location.href = '/';
    return;
  }
  try {
    render(await api('/api/profile'));
  } catch(e){ slip(e.message); }
});
