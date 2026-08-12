// The career profile page: games played, titles won, and the top 5 batters / top 4
// bowlers by total runs/wickets across every game this account has saved (solo or
// room, migration 027). Read-only -- nothing here writes anything; the save hooks live
// in season.js/room.js instead, right where a game actually completes.

function leaderRowHtml(rank, r, unit){
  return `<div class="fx">
    <span class="wl">${rank}</span>
    <span>${r.name}</span>
    <span class="sc">${r.total} ${unit}</span>
  </div>`;
}

function render(p){
  $('#profileUsername').textContent = '@' + p.username;
  $('#profileGames').textContent = p.games_played;
  $('#profileTitles').textContent = p.titles_won;

  $('#profileBatters').innerHTML = p.top_batters.length
    ? p.top_batters.map((r, i) => leaderRowHtml(i + 1, r, 'runs')).join('')
    : '<div class="note">Play and save a game to see your top scorers here.</div>';

  $('#profileBowlers').innerHTML = p.top_bowlers.length
    ? p.top_bowlers.map((r, i) => leaderRowHtml(i + 1, r, 'wkts')).join('')
    : '<div class="note">Play and save a game to see your top wicket-takers here.</div>';
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
