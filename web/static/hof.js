// Hall of Fame rows, shared by the profile (top five) and /hall (every career).
// Loaded after common.js, which provides esc, crestImg and skillChips.

// Build a Career's Hall of Fame [A210]: your finished careers, the best first. Each opens to
// its milestones; where it would sit on the real all-time lists is the server's to say.
function hofRow(c){
  const t = c.card || {};
  const hon = c.career_honours || {};          // [A286]
  const years = `${c.debut}–${c.debut + c.seasons - 1}`;
  const rank = (n, what) => n != null && n <= 25 ? `<em class="hof-rank">${ordinal(n)} all-time for ${what}</em>` : '';
  const ms = (t.milestones || []).map(([y, text]) => `<li><span>${y}</span>${esc(text)}</li>`).join('');
  return `<details class="hof">
    <summary>
      <span class="hof-crests">${c.crests.map(u => crestImg(u, 'hof-crest')).join('')}</span>
      <span class="hof-who"><b>${esc(c.name)}</b>${hon.nickname ? `<em class="hof-nick">“${esc(hon.nickname)}”</em>` : ''}<span>${esc(c.role_label)}${c.overseas ? ' · overseas' : ''} · ${years}</span></span>
      <span class="hof-num"><b>${c.runs.toLocaleString()}</b> runs · <b>${c.wickets}</b> wkts</span>
      <span class="hof-num"><b>${c.titles}</b> title${c.titles === 1 ? '' : 's'} · peak <b>${c.peak_card}</b></span>
      ${rank(c.runs_rank, 'runs')}${rank(c.wickets_rank, 'wickets')}
      ${c.inducted ? `<span class="hof-honours">${c.honours.map(h => `<i>${esc(h)}</i>`).join('')}</span>` : ''}
      ${hon.finish && hon.finish !== 'Standard' ? `<span class="hof-finish finish-${hon.finish.toLowerCase()}" title="${esc((hon.titles || []).map(t => t.label).join(' · '))}">${esc(hon.finish)}</span>` : ''}
      ${(c.mastered || []).length ? `<span class="hof-mastered">${c.mastered.map(k => `<span class="sk mastered t${k.tier}" title="Mastered${k.year ? ' in ' + k.year : ''}">✦ ${esc(k.label)}</span>`).join('')}</span>` : ''}
      ${c.skills && c.skills.length ? skillChips({skills: c.skills.slice(0, 3)}, true) : ''}
    </summary>
    ${ms ? `<ol class="hof-ms">${ms}</ol>` : '<p class="pf-empty">No milestones in this one.</p>'}
  </details>`;
}

function ordinal(n){
  const t = n % 100, u = n % 10;
  return n + (t >= 11 && t <= 13 ? 'th' : u === 1 ? 'st' : u === 2 ? 'nd' : u === 3 ? 'rd' : 'th');
}

// [A225] Only careers holding two of the four honours are in the Hall of Fame; the rest
// stay saved (and can still be brought into an auction room) but fold away beneath it.
const HOF_RULE = 'two of: a peak Rating of 90+, two titles, an Orange or Purple Cap, a place in the all-time top 25';

// [A225] Inducted careers first, best first (the server's order); the rest fold away.
function hofSplit(list){
  return {inn: list.filter(c => c.inducted), rest: list.filter(c => !c.inducted)};
}
