// The Puzzles hub [A186]: which of today's puzzles are finished, and one streak across them.
//
// Nothing here knows how a puzzle works. Each game keeps its own progress in this browser
// under its own key (see `puzzle.js`), and a finished day is a date in that game's `days`
// list. The hub only reads those lists. A day counts toward the streak if ANY daily puzzle
// was finished on it -- the forgiving rule, chosen so a player who only likes one of them
// still has a streak worth keeping.

function pzHubDays(key){
  return pzStore(key).days || [];
}

async function pzHub(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  let today;
  try { today = (await api('/api/bingo/today')).date; }
  catch(e){ return; }                        // no date, no statuses: the cards still link
  const cards = [...document.querySelectorAll('.pz-card[data-key]')];
  const all = new Set();
  let done = 0;
  for (const card of cards){
    const days = pzHubDays(card.dataset.key);
    days.forEach(d => all.add(d));
    const finished = days.includes(today);
    card.classList.toggle('done', finished);
    card.querySelector('.pz-go').textContent = finished ? 'Done today ✓' : 'Play →';
    if (finished) done++;
  }
  $('#pzCount').textContent = `· ${done} of ${cards.length} done`;
  $('#pzStreak').innerHTML = pzStreakHtml([...all].sort(), today, 'puzzle');
}

// The day's boards, one tab each. Nothing is cached here: a board is a thing to look at again.
async function pzHubBoard(game){
  document.querySelectorAll('#pzTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.game === game));
  await pzLoadBoard(game, '#pzHubBoard');
}

pzHub();
pzHubBoard('overall');
document.addEventListener('signedin', () => {
  const on = document.querySelector('#pzTabs .sel');
  pzHubBoard(on ? on.dataset.game : 'overall');
});
