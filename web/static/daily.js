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

// Signed out, the day is played under a random id this browser keeps. It decides the deal
// the same way an account id does, and it is all the server is ever told: nothing about an
// anonymous player is stored, and their result is never ranked -- clearing the browser
// deals a fresh hand, which is exactly why it cannot be. Sent on every call, and simply
// ignored by the server whenever somebody is signed in.
const DAILY_DEVICE_KEY = 'iplegends_daily_device';
const DAILY_ANON_KEY = 'iplegends_daily_anon';

function dailyDeviceId(){
  let id = null;
  try { id = localStorage.getItem(DAILY_DEVICE_KEY); } catch(e){ /* blocked storage */ }
  if (!/^[0-9a-f]{32}$/.test(id || '')){
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    id = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    try { localStorage.setItem(DAILY_DEVICE_KEY, id); } catch(e){ /* per-visit id then */ }
  }
  return id;
}
API_HEADERS = {'X-Daily-Device': dailyDeviceId()};

// A signed-out attempt lives only here, so a reload shows the result instead of a fresh
// draft. Keyed by date: yesterday's attempt is simply not today's.
function keptAnonAttempt(date){
  try {
    const kept = JSON.parse(localStorage.getItem(DAILY_ANON_KEY) || 'null');
    return kept && kept.date === date ? kept.state : null;
  } catch(e){ return null; }
}
function keepAnonAttempt(date, state){
  try { localStorage.setItem(DAILY_ANON_KEY, JSON.stringify({date, state})); } catch(e){}
}

// The challenge, before a ball is drafted -- in the result card's own language, tinted in
// the opposition's colours, so the page looks the same before the match and after it.
function dailyBanner(d){
  $('#dailyDate').textContent = `Daily challenge · ${d.challenge_date} · ${d.stage}`;
  $('#dailyScenario').textContent = d.scenario;
  $('#dailyCrest').innerHTML = crestImg(d.opposition_crest, 'daily-crest');
  const banner = $('#dailyBanner');
  const t = sideTint({crest: d.opposition_crest});
  banner.className = 'mr dbn' + (t.cls ? ' ' + t.cls : '');
  // A day carries ONE bonus, rotated, so this reads as a thing to chase rather than a
  // list header. The plural form is kept for the stored days generated before the
  // rotation, which really do offer several.
  const bs = d.bonuses;
  $('#dailyBonuses').innerHTML = (bs.length
      ? `<span class="dr-chip gold"><i>${bs.length === 1 ? "Today's bonus" : 'Bonuses'}</i>${
          bs.map(esc).join(' · ')}</span>`
      : '') + streakLine(d, false);
}

// Shown BEFORE playing as something to keep, and after as something kept -- which is the
// only moment either sentence is worth reading. A streak of one is not mentioned before
// the day is played: telling a first-timer they are on a one-day streak they have not yet
// extended is noise, and telling them they might lose it is worse.
function streakLine(d, done){
  if (!d.streak) return '';
  const best = d.longest_streak > d.streak ? ` <em>best ${d.longest_streak}</em>` : '';
  if (done) return `<span class="dr-chip fire"><i>Streak</i>🔥 ${d.streak} day${d.streak === 1 ? '' : 's'}${best}</span>`;
  if (d.streak < 2) return '';
  return `<span class="dr-chip fire"><i>Streak</i>🔥 ${d.streak} days${best} <em>play today to keep it</em></span>`;
}

// draft.js calls this instead of its own "Play the season" behaviour once the twelve is
// full. A daily has no season to play: the match is one scenario, resolved server-side on
// submission, so the only thing left for the player to do is commit.
function dailyOnComplete(s){
  const sim = $('#simBtn');
  sim.classList.toggle('hide', !s.squad_complete);
  if (!s.squad_complete) return;
  // [A157] The order stays open until the match is played: say so, since nothing else on a
  // finished sheet suggests the rows can still be tapped.
  $('#legality').insertAdjacentHTML('beforeend',
    '<div class="note" style="padding:6px 0 0">Tap a player, then another, to swap their ' +
    'places. The order is yours to set until you play.</div>');
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
        method: 'POST', retry: true, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({state: S.state})});
      if (DAY.anonymous) keepAnonAttempt(DAY.challenge_date, S.state);
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
// [A151] The engine calls your side 'You'; every screen here draws your kit instead.
// `you_home` says which side that is, so the lookup never depends on the label text alone.
function setDailySide(m){
  const k = myKit();
  const short = !m ? 'You'
    : m.you_home === true ? m.home : m.you_home === false ? m.away
    : (m.home === 'You' ? m.home : m.away);
  MY_SIDE = {short, name: k.name, crest: null, kit: k};
}

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
  setDailySide(m);
  const home = resolveSide(m.home, m.home_crest), away = resolveSide(m.away, m.away_crest);
  if (m.home_innings) steps.push([m.home_innings, `${m.stage} · ${home.name} batting`, null,
    {bat: home, bowl: away}]);
  if (m.away_innings) steps.push([m.away_innings, `${m.stage} · ${away.name} batting`,
    m.home_innings ? {innings: m.home_innings, battingLabel: home.name} : null,
    {bat: away, bowl: home}]);

  let i = 0;
  (function next(){
    if (i >= steps.length){ dailyFinishReveal(); return; }
    const [innings, label, prior, matchup] = steps[i++];
    startOverStepper(innings, label, next, prior, matchup);
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

// The outcome's wording comes from the server (`result.outcome`, `margin_words` on each
// board row): it must agree with the scoring, and two copies of it here had already
// drifted into calling a too-slow chase "1.0 overs to spare".

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
  if (DAY && DAY.match){ setDailySide(DAY.match); renderScorecard(DAY.match); }
}

// The finished day as a broadcast card -- the season's own result card (A169), with the
// challenge strip and today's board under it. Both scores lead, because the old screen
// printed a verdict and never the match it was a verdict on.
async function showDone(){
  const d = DAY, r = d.result, m = d.match;
  $('#draft').classList.add('hide');
  const done = $('#dailyDone');
  done.classList.remove('hide');

  let board = [];
  try { board = await api('/api/daily/leaderboard'); } catch(e){ /* the result still stands */ }

  let teams = '', stars = '', tint = {cls: '', style: ''}, won = null;
  if (m){
    setDailySide(m);
    const home = resolveSide(m.home, m.home_crest), away = resolveSide(m.away, m.away_crest);
    const winner = m.winner === m.home ? home : (m.winner === m.away ? away : null);
    won = winner ? winner.short === MY_SIDE.short : null;
    tint = sideTint(winner || MY_SIDE);
    teams = `<button class="mr-teams" onclick="dailyScorecard()" title="Open the scorecard">
        ${resultTeamHtml(home, m.home_score, m.home_innings, m.winner === m.home)}
        <span class="mr-v">v</span>
        ${resultTeamHtml(away, m.away_score, m.away_innings, m.winner === m.away)}
      </button>`;
    if (m.home_innings && m.away_innings){
      stars = `<div class="mr-stars">
        <div><span class="mr-lbl">${esc(home.name)}</span>${sideStarsHtml(m.home_innings, m.away_innings)}</div>
        <div><span class="mr-lbl">${esc(away.name)}</span>${sideStarsHtml(m.away_innings, m.home_innings)}</div>
      </div>`;
    }
  }
  const met = r.objective_met;
  const matchLine = won === null ? '' : won ? 'You won the match' : 'You lost the match';

  done.innerHTML = `
    <div class="mr dr${met ? ' you-won' : ' you-lost'}${tint.cls ? ' ' + tint.cls : ''}"
        ${tint.style ? `style="${tint.style}"` : ''}>
      <div class="mr-stage">Daily challenge · ${esc(d.challenge_date)} · ${esc(d.stage)}</div>
      <div class="mr-headline ${met ? 'won' : 'lost'}">${met ? 'Challenge met' : 'Challenge missed'}</div>
      <div class="mr-margin">${esc(cap(r.outcome || ''))}${matchLine ? ` <span class="dr-match">· ${matchLine}</span>` : ''}</div>
      ${teams}
      ${stars}
      <div class="dr-ask">${crestImg(d.opposition_crest, 'dr-ask-crest')}<span>${esc(d.scenario)}</span></div>
      <div class="dr-chips">
        ${rankChip(d)}
        ${r.bonuses.length
          ? `<span class="dr-chip gold"><i>Bonus</i>${esc((r.bonus_labels || r.bonuses).join(', '))} <b>+${r.bonus_points}</b></span>`
          : `<span class="dr-chip"><i>Bonus</i>${d.bonuses.length ? esc(d.bonuses.join(', ')) + ' — not earned' : 'none today'}</span>`}
        ${streakLine(d, true)}
      </div>
    </div>
    <div class="mr-actions">
      <div class="actions mr-primary">
        ${d.anonymous ? `<a class="act lead" href="/profile?next=${encodeURIComponent('/daily')}"
          title="Your own deal and one ranked attempt at today's challenge.">Sign in to be ranked</a>` : ''}
        <button class="act ${d.anonymous ? '' : 'lead'}" id="shareBtn" onclick="shareResult(this)">Share result</button>
        ${m ? '<button class="act" onclick="dailyScorecard()">Scorecard</button>' : ''}
      </div>
      <div class="actions mr-secondary">
        ${m ? '<button class="act minor" onclick="dailyReveal()">Watch again</button>' : ''}
        <a class="act minor" href="/">Home</a>
        ${d.can_reset ? `<button class="act quiet" onclick="dailyReset(this)"
          title="Discard this attempt and play today again.">Reset attempt</button>` : ''}
      </div>
    </div>
    <div class="db">
      <div class="db-head"><span>Today's leaderboard</span><em>${board.length
        ? `${board.length} played` : ''}</em></div>
      ${board.length ? board.map(boardRow).join('')
        : '<div class="db-empty">Nobody has finished today yet. Yours could be the first name here.</div>'}
      <p class="db-foot">${d.anonymous
        ? `Signed in, you get your own deal and one ranked attempt at today's challenge, and
           every day you play counts toward a streak.`
        : `One attempt a day. Come back tomorrow for a new scenario and a new set of squads.`}</p>
    </div>`;
}

function cap(s){ return s.charAt(0).toUpperCase() + s.slice(1); }

function rankChip(d){
  if (d.rank) return `<span class="dr-chip"><i>Rank</i><b>#${d.rank}</b> of ${d.players_today}</span>`;
  if (d.anonymous && d.would_rank) return `<span class="dr-chip"><i>Would rank</i><b>#${d.would_rank}</b>
    of ${d.players_today + 1} <em>not recorded</em></span>`;
  return '';
}

// One board row. A player without a kit gets a badge from their name, the same fallback
// every other unbadged side uses.
function boardRow(b){
  const you = ME && b.username === ME.username;
  const side = {short: b.username, name: b.username, kit: b.kit};
  return `<div class="db-row${you ? ' you' : ''}${b.rank <= 3 ? ' top' : ''}">
      <span class="db-rank">${b.rank}</span>
      ${sideBadge(side, 'db-badge')}
      <span class="db-name">${esc(b.username)}${you ? ' <em>you</em>' : ''}</span>
      <span class="db-met ${b.objective_met ? 'y' : 'n'}" title="${b.objective_met ? 'Challenge met' : 'Challenge missed'}">${b.objective_met ? '✓' : '✕'}</span>
      <span class="db-margin">${esc(b.margin_words)}</span>
      <span class="db-bonus">${b.bonus_points ? '+' + b.bonus_points : ''}</span>
    </div>`;
}

// The reveal speed is one preference across every page [A148]: Normal until the player
// changes it, and whatever they chose from then on. This page used to skip the restore,
// so a player who had picked Fast on a season still watched their daily at Normal.
restoreOverSpeed();
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
  // Signed out, the server remembers nothing, so an attempt already made today is the one
  // this browser kept -- scored again (the same state always plays the same match) rather
  // than offering a fresh draft to somebody who has had their go.
  const kept = d.anonymous && keptAnonAttempt(d.challenge_date);
  if (kept){
    try {
      DAY = await api('/api/daily/submit', {
        method: 'POST', retry: true, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({state: kept})});
      await showDone();
      return;
    } catch(e){ /* unreadable or no longer valid: fall through to a fresh draft */ }
  }
  await dailyStartDraft(d);
});


// The draft in the address bar, if it is this player's own deal for today. `render` writes
// every pick there, so this is what a reload mid-draft still has -- and the server refuses
// any seed that is not theirs regardless, so this only decides what to ASK for.
function resumableState(d){
  const [h] = location.hash.slice(1).split('?');
  return h && d.state && h.split('-')[0] === d.state.split('-')[0] ? h : d.state;
}

// Open the draft for a day this player has not played -- resumed where the address bar
// left it, or fresh. Named rather than left inline in `boot`, because the reset below
// needs exactly this and a second copy would be a second place for the ON_COMPLETE wiring
// to be forgotten.
async function dailyStartDraft(d){
  ON_COMPLETE = dailyOnComplete;
  $('#draft').classList.remove('hide');
  const resumed = resumableState(d);
  try {
    render(await api(`/api/daily/draft/${resumed}`));
  } catch(e){
    if (resumed === d.state){ slip(e.message); return; }
    try { render(await api(`/api/daily/draft/${d.state}`)); }
    catch(e2){ slip(e2.message); }
  }
}


// Discard your own attempt and play the day again. Drawn only when the API says this
// caller may -- and that is presentation, not permission: the route checks again and
// answers 404 to anybody else, because a button that is merely absent is not a gate.
async function dailyReset(ctrl){
  await busyClick(ctrl, 'Resetting…', async () => {
    try {
      DAY = await api('/api/daily/reset', {method: 'POST'});
      // Straight back to a fresh draft of the same day rather than re-rendering the
      // finished screen: the whole point of the reset is to play, and the day itself is
      // unchanged -- same scenario, same sixteen squads, same bonus.
      $('#dailyDone').classList.add('hide');
      hideAllRevealScreens();
      $('#reveal').classList.add('hide');
      dailyBanner(DAY);
      // The address bar still holds the finished twelve under the same seed, and resuming
      // from it would hand back the attempt that was just discarded.
      history.replaceState(null, '', location.pathname);
      await dailyStartDraft(DAY);
    } catch(e){ slip(e.message); }
  });
}
