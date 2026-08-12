// Shared boot for the four static content pages (about/faq/terms/privacy) -- all four
// need exactly the same thing: the auth control filled in and the imprint's footer
// stats line, nothing page-specific. A game page's own boot() differs enough (draft
// state, room polling, season state) that sharing further than this would mean sharing
// nothing real; these four are identical to each other, which is the actual bar for
// putting logic in one file rather than duplicating it.

async function boot(){
  loadMe();   // fire-and-forget -- the auth control fills in whenever it resolves
  const m = await loadMeta();
  const s = m.seasons;
  $('#deckPill').textContent = `${s[0]}–${s[s.length-1]} · ${m.franchise_seasons} squads`;
  $('#footStats').textContent =
    `${m.cards.toLocaleString()} player-seasons · ${m.franchise_seasons} squads · ${s.length} seasons`;
}

boot();
