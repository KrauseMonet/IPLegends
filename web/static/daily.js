// The daily challenge page. Everything about DRAFTING is draft.js -- picks, placement,
// repositions, the order sheet, eligibility -- and this file only supplies the four things
// a daily does differently: where picks go, where the resume state comes from, that there
// are no rerolls, and what a finished twelve leads to.
//
// `window.DAILY_PAGE` (set in the page itself, before draft.js loads) tells draft.js not
// to run its own hash-resume boot; this file boots instead.

let DAY = null;

// The daily is always played from memory -- no ratings on the cards while drafting. Not a
// per-player setting: everybody is answering the same question off the same squads, so a
// board comparing a memory draft with a stat-assisted one would be comparing two games.
DRAFT_MODE = 'memory';
DRAFT_API = '/api/daily/draft';

function dailyBanner(d){
  $('#dailyDate').textContent = 'Daily challenge · ' + d.challenge_date;
  $('#dailyScenario').textContent = d.scenario;
  $('#dailyBonuses').innerHTML = d.bonuses.length
    ? 'Bonus: ' + d.bonuses.map(b => `<em>${b}</em>`).join(' · ') : '';
}

// draft.js calls this instead of its own "Play the season" behaviour once the twelve is
// full. A daily has no season to play: the match is one scenario, resolved server-side on
// submission, so the only thing left for the player to do is commit.
function dailyOnComplete(s){
  const sim = $('#simBtn');
  sim.classList.toggle('hide', !s.squad_complete);
  if (!s.squad_complete) return;
  sim.disabled = !s.playable;
  sim.textContent = s.playable ? "Play today's challenge" : 'Not yet legal';
  sim.onclick = () => dailySubmit(sim);
  $('#simModeLabel').classList.add('hide');
  $('#simModeChoices').classList.add('hide');
}

async function dailySubmit(ctrl){
  await busyClick(ctrl, 'Playing…', async () => {
    try {
      DAY = await api('/api/daily/submit', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({state: S.state})});
      await showDone();
    } catch(e){ slip(e.message); }
  });
}

// A raw margin is not readable on its own -- "-14" and "7" mean entirely different things
// and neither says which. The unit is today's, from the scenario, and a NEGATIVE margin on
// a chase means runs short rather than wickets (game/scenarios.py has the why).
function marginWords(met, margin, unit){
  if (unit === 'runs'){
    if (margin > 0) return `by ${margin} runs`;
    if (margin < 0) return `lost by ${-margin}`;
    return 'tied';
  }
  return met ? `${margin} wkt${margin === 1 ? '' : 's'} in hand` : `${-margin} short`;
}

function outcomeLine(d){
  const r = d.result;
  const unit = d.margin_unit;
  if (r.objective_met){
    return unit === 'runs'
      ? `Challenge met — won by ${r.margin} runs`
      : `Challenge met — chased with ${r.margin} wickets in hand`;
  }
  // A failed chase carries a NEGATIVE margin: how close it came, not wickets in hand
  // (game/scenarios.py explains why ranking a failure on wickets rewards blocking out).
  if (unit === 'wickets') return `Fell ${Math.abs(r.margin)} short`;
  return r.margin > 0 ? `Won by ${r.margin} runs, short of the target margin`
                      : `Lost by ${Math.abs(r.margin)} runs`;
}

// Prefer the platform's own share sheet where there is one -- on a phone that is how
// people actually send something to a friend, and it reaches WhatsApp or Messages in one
// tap where a clipboard copy needs them to go and paste it. Falls back to the clipboard,
// and then to showing the text, which is the same three-step ladder `copyLink` already
// uses elsewhere in this app.
//
// The text itself is the SERVER's (`share_text`), never assembled here: its wording has to
// agree with the scoring, and a second copy in this file would be a second place for
// "7 wickets in hand" to drift from what actually happened.
async function shareResult(btn){
  const text = DAY && DAY.share_text;
  if (!text){ slip('Nothing to share yet.'); return; }
  if (navigator.share){
    try { await navigator.share({text}); return; }
    catch(e){ if (e && e.name === 'AbortError') return; }   // they closed the sheet: not an error
  }
  try {
    await navigator.clipboard.writeText(text);
    slip('Result copied — spoiler-free, so you can post it anywhere.');
  } catch(e){ slip(text); }
}

async function showDone(){
  const d = DAY, r = d.result;
  $('#draft').classList.add('hide');
  const done = $('#dailyDone');
  done.classList.remove('hide');

  let board = [];
  try { board = await api('/api/daily/leaderboard'); } catch(e){ /* the result still stands */ }
  const rows = board.map(b => `<div class="line${b.username === (ME && ME.username) ? ' you' : ''}">
      <span class="pos">${b.rank}</span>
      <span class="nm">${b.username}</span>
      <span class="sc">${b.objective_met ? '✓' : ''}</span>
      <span class="sc">${marginWords(b.objective_met, b.margin, d.margin_unit)}${
        b.bonus_points ? ` <em>+${b.bonus_points}</em>` : ''}</span>
    </div>`).join('');

  done.innerHTML = `<div class="report compact">
      <div class="over-line">Daily challenge · ${d.challenge_date}</div>
      <div class="call ${r.objective_met ? 'won' : ''}">${outcomeLine(d)}</div>
      <div class="margin">${d.scenario}</div>
      ${d.rank ? `<div class="margin">You are <em>#${d.rank}</em> of ${d.players_today} today.</div>` : ''}
      ${r.bonuses.length
        ? `<div class="margin">Bonus earned: ${(r.bonus_labels || r.bonuses).join(', ')} (+${r.bonus_points})</div>`
        : '<div class="margin">No bonus today.</div>'}
      <div class="foot-actions">
        <button class="act lead" id="shareBtn" onclick="shareResult(this)">Share result</button>
        <a class="act" href="/">Home</a>
      </div>
    </div>
    <div class="col-head" style="margin:20px 0 0"><span>Today's leaderboard</span></div>
    ${rows || '<div class="margin">Nobody has finished today yet.</div>'}
    <div class="margin" style="margin-top:14px">One attempt a day. Come back tomorrow for a
      new scenario and a new set of squads.</div>`;
}

boot().then(async () => {
  let d;
  try {
    d = await api('/api/daily');
  } catch(e){
    // The auth gate. 401 is the ONLY expected failure here, and it is a routing signal
    // rather than an error: send them somewhere they can sign in, and bring them back.
    if (e.status === 401){
      $('#dailyGate').classList.remove('hide');
      $('#gateSignIn').href = '/profile?next=' + encodeURIComponent('/daily');
      return;
    }
    slip(e.message);
    return;
  }
  DAY = d;
  dailyBanner(d);
  if (d.played){ await showDone(); return; }

  ON_COMPLETE = dailyOnComplete;
  $('#draft').classList.remove('hide');
  try {
    render(await api(`/api/daily/draft/${d.state}`));
  } catch(e){ slip(e.message); }
});
