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
  // A day carries ONE bonus, rotated, so this reads as a thing to chase rather than a
  // list header. The plural form is kept for the stored days generated before the
  // rotation, which really do offer several.
  const bs = d.bonuses;
  $('#dailyBonuses').innerHTML = (bs.length
      ? (bs.length === 1 ? `Today's bonus: <em>${bs[0]}</em>`
                         : 'Bonus: ' + bs.map(b => `<em>${b}</em>`).join(' · '))
      : '') + streakLine(d, false);
}

// Shown BEFORE playing as something to keep, and after as something kept -- which is the
// only moment either sentence is worth reading. A streak of one is not mentioned before
// the day is played: telling a first-timer they are on a one-day streak they have not yet
// extended is noise, and telling them they might lose it is worse.
function streakLine(d, done){
  if (!d.streak) return '';
  const best = d.longest_streak > d.streak ? ` · best ${d.longest_streak}` : '';
  if (done) return `<div class="margin">🔥 <em>${d.streak}-day streak</em>${best}</div>`;
  if (d.streak < 2) return '';
  return `<div class="margin">🔥 <em>${d.streak}-day streak</em>${best} — play today to keep it.</div>`;
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
      dailyReveal();
    } catch(e){ slip(e.message); }
  });
}

// The match plays out ball by ball, exactly as it does in a season and in a room --
// `reveal.js`'s stepper, unmodified, fed this match instead of that one. The daily was
// the only mode that resolved a match server-side and then just printed the answer.
//
// Both innings, in the order they were bowled. `home` is whoever batted first, which on a
// "bowl first" day is the opposition -- their innings is the one your own five bowlers
// held down, and it is what makes the target mean something rather than being a number in
// a banner.
function dailyReveal(){
  const m = DAY.match;
  if (!m){ showDone(); return; }
  // Toggled directly rather than through a `go()` of its own: season.js and room.js each
  // define one over their own list of sections, and a third copy here would be a third
  // list to keep in step with a page's markup.
  $('#draft').classList.add('hide');
  $('#dailyDone').classList.add('hide');
  $('#reveal').classList.remove('hide');
  window.scrollTo(0, 0);

  const steps = [];
  if (m.home_innings) steps.push([m.home_innings, `${m.stage} · ${m.home} batting`, null]);
  if (m.away_innings) steps.push([m.away_innings, `${m.stage} · ${m.away} batting`,
    m.home_innings ? {innings: m.home_innings, battingLabel: m.home} : null]);

  let i = 0;
  (function next(){
    if (i >= steps.length){ dailyFinishReveal(); return; }
    const [innings, label, prior] = steps[i++];
    startOverStepper(innings, label, next, prior);
  })();
}

// Straight to the result, abandoning whatever is still animating. The same escape hatch
// solo and rooms offer, and for the same reason: the outcome is already decided, so this
// only skips the telling of it.
function dailySkipMatch(){
  clearOverTimer();
  OVER_STEP = null;
  dailyFinishReveal();
}

function dailyFinishReveal(){
  hideAllRevealScreens();
  $('#reveal').classList.add('hide');
  showDone();
}

// Balls as cricket writes them. `overs_words` in game/scenarios.py is the same rule; two
// copies of one formatting convention is the least of the things that could drift here,
// and the alternative is a round trip to render a number the page already has.
function oversWords(balls){ return `${Math.floor(balls / 6)}.${balls % 6}`; }

// A raw margin is not readable on its own -- "-14" and "7" mean entirely different things
// and neither says which. The unit is today's, from the scenario.
//
// The SIGN carries the failure, not `met`, and that distinction is load-bearing on two of
// the three units: a chase completed with too few wickets in hand, or a beat too slowly,
// MISSES the objective while still carrying a perfectly good positive margin. Reading
// "not met" as "fell short" would have printed "-2 short" at somebody who chased it.
function marginWords(met, margin, unit){
  if (unit === 'runs'){
    if (margin > 0) return `by ${margin} runs`;
    if (margin < 0) return `lost by ${-margin}`;
    return 'tied';
  }
  if (margin < 0) return `${-margin} short`;
  return unit === 'balls' ? `${oversWords(margin)} ov to spare`
                          : `${margin} wkt${margin === 1 ? '' : 's'} in hand`;
}

function outcomeLine(d){
  const r = d.result, unit = d.margin_unit;
  const verdict = r.objective_met ? 'Challenge met' : 'Challenge missed';
  if (unit === 'runs'){
    const what = r.margin > 0 ? `won by ${r.margin} runs`
               : r.margin < 0 ? `lost by ${-r.margin} runs` : 'tied';
    return `${verdict} — ${what}`;
  }
  if (r.margin < 0) return `${verdict} — fell ${-r.margin} short`;
  return `${verdict} — ` + (unit === 'balls'
    ? `chased with ${oversWords(r.margin)} overs to spare`
    : `chased with ${r.margin} wicket${r.margin === 1 ? '' : 's'} in hand`);
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

function dailyScorecard(){
  if (DAY && DAY.match) renderScorecard(DAY.match);
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
      ${streakLine(d, true)}
      ${r.bonuses.length
        ? `<div class="margin">Bonus earned: ${(r.bonus_labels || r.bonuses).join(', ')} (+${r.bonus_points})</div>`
        : '<div class="margin">No bonus today.</div>'}
      <div class="foot-actions">
        <button class="act lead" id="shareBtn" onclick="shareResult(this)">Share result</button>
        ${d.match ? '<button class="act" onclick="dailyScorecard()">Scorecard</button>' : ''}
        ${d.match ? '<button class="act" onclick="dailyReveal()">Watch again</button>' : ''}
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
