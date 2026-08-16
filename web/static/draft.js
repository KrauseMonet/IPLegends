// The solo draft page. Reached only by navigation from home (a fresh POST /api/draft
// result folded into the URL hash) or by a direct/reloaded URL carrying a draft state
// in the hash -- this page's own boot() is the resume path for both.
let S = null;

// Mode is chosen on the home page and travels here as '#state?mode=memory' (mirroring
// the existing hash-decoration convention render() below re-writes on every pick, so a
// reload of THIS page's own URL still remembers it). No UI here to change it mid-draft --
// there never was one on the draft screen itself, only on home.
let DRAFT_MODE = 'stat';
function effectiveDraftMode(){ return DRAFT_MODE; }

/* --- draft --- */

// [A73] A pick names both the candidate AND the slot he bats at, in one request --
// placement is final the instant it lands, so there is no bench and nothing to move
// again afterward. If a candidate has exactly one open slot he's eligible for right now,
// taking him settles it immediately; with more than one, PENDING holds him while the
// order sheet asks which slot, and clicking an eligible row finishes the pick.
let LAST_DEAL_FS = null;   // fs_id last shown in "Dealt to you", so render() only replays
                            // the roll flourish when the deal actually changed
let PENDING = null;   // {index, card} for a deal option awaiting a slot choice, or null
let REPOSITION_PENDING = null;   // slot number of an already-placed pick awaiting a swap target, or null

function eligibleForSlot(card, slot){
  return slot === 12 || card.positions.includes(slot);
}

function openSlots(s){
  const open = new Set(Array.from({length:11}, (_, i) => i + 1));
  open.add(12);   // the Impact pseudo-slot, open until s.impact is chosen
  s.order.forEach((c, i) => { if (c) open.delete(i + 1); });
  if (s.impact) open.delete(12);
  return open;
}

async function submitPick(i, slot, ctrl){
  await busyClick(ctrl, 'Taking…', async () => {
    try {
      S = await api(`/api/draft/${S.state}/pick`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({index: i, slot})});
      PENDING = null;
      render(S);
    } catch(e){ slip(e.message); }
  });
}

function take(i, ctrl){
  // Always a two-step confirm, even when only one slot is eligible -- clicking "Take"
  // must never itself commit the pick. A single eligible row still gets highlighted
  // (the order sheet's own eligibility check already handles that), the player just has
  // to click it, the same as when there's a real choice among several rows.
  REPOSITION_PENDING = null;   // a new pick and a reposition are never chosen at once
  const card = S.deal.options[i];
  PENDING = (PENDING && PENDING.index === i) ? null : {index: i, card};
  render(S);   // the deal list's "pending" highlight lives here too, not just the order sheet
}

function rowClick(slot, ctrl){
  if (PENDING){
    submitPick(PENDING.index, slot, ctrl);
    return;
  }
  repositionClick(slot, ctrl);
}

// Repositioning: click an already-placed pick to select him, click a second already-placed
// pick to swap them (both must be legally eligible for the other's slot -- the server has
// final say, this is just the affordance). Click the same row again to deselect. Nothing
// here touches PENDING/take()'s new-pick flow; the two selections are mutually exclusive
// by construction (rowClick only reaches this branch when no new pick is PENDING).
function repositionClick(slot, ctrl){
  const got = slot === 12 ? S.impact : S.order[slot - 1];
  if (REPOSITION_PENDING === slot){
    REPOSITION_PENDING = null;
    renderPanels();
    return;
  }
  if (REPOSITION_PENDING === null){
    if (!got) return;   // an empty slot has nothing to select
    REPOSITION_PENDING = slot;
    renderPanels();
    return;
  }
  submitReposition(REPOSITION_PENDING, slot, ctrl);
}

async function submitReposition(fromSlot, toSlot, ctrl){
  const from = fromSlot;
  REPOSITION_PENDING = null;
  await busyClick(ctrl, null, async () => {
    try {
      S = await api(`/api/draft/${S.state}/reposition`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({from_slot: from, to_slot: toSlot})});
      render(S);
    } catch(e){ slip(e.message); renderPanels(); }
  });
}

async function rerollDeal(kind, ctrl){
  await busyClick(ctrl, 'Rerolling…', async () => {
    try {
      S = await api(`/api/draft/${S.state}/reroll`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({kind})});
      PENDING = null;
      render(S);
    } catch(e){ slip(e.message); }
  });
}

function render(s){
  S = s;
  // '?mode=memory', never '.memory' -- '.' already separates moves inside the state
  // string itself (and 'm' already opens a Reposition segment), so anything appended
  // with a '.' risks being parsed as one. '?' cannot collide with that grammar. 'stat'
  // is the default and stays unmarked, so an old shared link (no suffix at all) still
  // opens exactly as it always did.
  const modeSuffix = DRAFT_MODE === 'memory' ? '?mode=memory' : '';
  history.replaceState(null, '', '#' + s.state + modeSuffix);
  $('#pickCount').textContent = `${s.picks_made}/${s.picks_total}`;
  $('#pickBig').textContent = s.picks_made;
  $('#oversLine').textContent = `${s.overseas_taken} of ${s.overseas_cap} overseas`;
  $('#progBar').style.width = (100 * s.picks_made / s.picks_total) + '%';

  if (s.deal){
    if (s.deal.fs_id !== LAST_DEAL_FS){
      LAST_DEAL_FS = s.deal.fs_id;
      rollDeal($('#dealYear'), $('#dealTeam'), s.deal.season_year, s.deal.franchise);
    }
    $('#rerollRow').classList.remove('hide');
    const rerollsLeft = s.rerolls_allowed - s.rerolls_used;
    $('#rerollTeamBtn').disabled = rerollsLeft <= 0;
    $('#rerollSeasonBtn').disabled = rerollsLeft <= 0;
    $('#rerollCount').textContent = `(${rerollsLeft} left)`;
    const blocked = s.deal.blocked || [];
    $('#optCount').textContent = `${s.deal.options.length} of ${s.deal.options.length + blocked.length}`;
    const live = s.deal.options.map((card, i) => {
      const pending = PENDING && PENDING.index === i ? ' pending' : '';
      const nmClick = DRAFT_MODE === 'stat'
        ? ` clickable" onclick="showStat(S.deal.options[${i}])"` : '"';
      return `
      <div class="entry${pending}">
        <span class="nm${nmClick}>${ICON[card.kind] || ''}${keeperBadge(card)}<span class="flag ${card.overseas ? '' : 'home'}"
          title="${card.overseas ? 'overseas' : 'domestic'}"></span>${card.name}${ratingBadge(card)}</span>
        <span class="picks"><button class="take" onclick="take(${i}, this)">${pending ? 'Choose a row' : 'Take'}</button></span>
      </div>`;
    }).join('');
    // The rest of the squad, greyed with the reason. Kept on the sheet rather than
    // filtered away so the roster reads as a roster.
    const dead = blocked.length ? `<div class="roster-head">Unavailable</div>` +
      blocked.map((b, i) => {
        const nmClick = DRAFT_MODE === 'stat'
          ? ` clickable" onclick="showStat(S.deal.blocked[${i}])"` : '"';
        return `
      <div class="entry off">
        <span class="nm${nmClick}>${ICON[b.kind] || ''}${keeperBadge(b)}<span class="flag ${b.overseas ? '' : 'home'}"></span>${b.name}${ratingBadge(b)}</span>
        <span class="why">${b.blocked || ''}</span>
      </div>`;
      }).join('') : '';
    $('#options').innerHTML = live + dead;
  } else {
    LAST_DEAL_FS = null;
    clearTimeout(ROLL_TIMERS.get($('#dealTeam')));
    $('#dealYear').textContent = '';
    $('#dealTeam').textContent = 'Your twelve';
    $('#rerollRow').classList.add('hide');
    $('#optCount').textContent = '';
    // The deal column has nothing left to deal, so rather than leaving a third of the
    // screen blank at the moment the squad is finished, it shows what was just built: a
    // full field, every position set, with the squad's own three numbers under it.
    $('#options').innerHTML = `
      <div class="squad-done">
        ${fieldWheel(s.order.length, s.order.length)}
        <div class="done-line">Field set</div>
        <div class="ledger">${teamRatingsHtml(s)}</div>
      </div>`;
  }

  renderPanels();
}

function renderPanels(){
  const s = S;

  // the order sheet: eleven numbered rows, then Impact. An open row is clickable while
  // PENDING holds a new candidate awaiting a slot; a FILLED row is clickable to select or
  // complete a reposition (REPOSITION_PENDING) -- a swap between two already-placed picks,
  // never a bench (A73 stands: nothing here creates an unplaced pick).
  const open = openSlots(s);
  const rows = s.order.map((got, i) => orderRow(i + 1, got, `${i + 1}`));
  rows.push(orderRow(12, s.impact, 'IMP', true));
  $('#orderSheet').innerHTML = rows.join('');

  // legality: the server's own sentences, not a client-side re-derivation of the rules
  if (s.errors.length){
    $('#legality').innerHTML = s.errors.map(e => `<div class="bad">${e}</div>`).join('');
  } else if (s.squad_complete) {
    $('#legality').innerHTML = '<div class="ok">Legal, ready to play</div>';
  } else {
    $('#legality').innerHTML = '<div class="note" style="padding:0">Take players straight ' +
      'into a batting position (or Impact) until all twelve are filled. Click an already-' +
      'placed pick, then a second slot, to swap them or move him into an open one -- any ' +
      'time before the twelve is complete.</div>';
  }

  $('#simBtn').classList.toggle('hide', !s.squad_complete);
  $('#simBtn').disabled = !s.playable;
  $('#simBtn').textContent = s.playable ? 'Play the season' : 'Not yet legal';
  $('#simModeLabel').classList.toggle('hide', !s.squad_complete);
  $('#simModeChoices').classList.toggle('hide', !s.squad_complete);
  // The ratings live in the (otherwise finished) deal column now, under the field --
  // rendering them here too would put the same three numbers on screen twice.
  $('#teamRatings').classList.add('hide');

  function orderRow(slot, got, label, isImpact){
    const classes = ['orderline'];
    if (got) classes.push('filled');
    if (isImpact) classes.push('impactrow');
    if (PENDING){
      const eligible = !got && open.has(slot)
        && (slot === 12 || PENDING.card.positions.includes(slot));
      classes.push(eligible ? 'eligible' : 'ineligible');
    } else if (REPOSITION_PENDING !== null){
      if (slot === REPOSITION_PENDING){
        classes.push('reposition-selected');
      } else {
        // an empty target is a plain move (frees REPOSITION_PENDING's slot) and only
        // needs the SELECTED player's own eligibility; a filled target is a swap and
        // needs both directions.
        const selected = REPOSITION_PENDING === 12 ? s.impact : s.order[REPOSITION_PENDING - 1];
        const ok = got
          ? eligibleForSlot(got, REPOSITION_PENDING) && eligibleForSlot(selected, slot)
          : eligibleForSlot(selected, slot);
        classes.push(ok ? 'eligible' : 'ineligible');
      }
    }
    const whoClick = (got && DRAFT_MODE === 'stat')
      ? ` clickable" onclick="event.stopPropagation(); showOrderStat(${slot})"` : '"';
    const label2 = got ? (ICON[got.kind] || '') + keeperBadge(got) + got.name + ratingBadge(got, s.squad_complete)
                       : (isImpact ? 'no impact player' : 'to be named');
    return `<div class="${classes.join(' ')}" onclick="rowClick(${slot}, this)">
      <span class="num">${label}</span>
      <span class="who${whoClick}>${label2}</span>
    </div>`;
  }
}

function showOrderStat(slot){
  showStat(slot === 12 ? S.impact : S.order[slot - 1]);
}

/* --- handing off to the season --- */

// 'whole' and 'groupstage' resolve every remaining fixture with the declared default
// toss/Impact answers (`POST .../skip`) and just show the outcome. 'matchbymatch' is
// the only mode where anything is actually decided live. All three now hand off to
// /season entirely -- this page's own job ends the moment the squad is legal and
// "Play the season" is clicked; SIM_MODE travels as `?enter=` on the new URL rather
// than as a variable simulate() reads locally, per the plan for the season split.
let SIM_MODE = 'whole';

function setSimMode(mode){
  SIM_MODE = mode;
  document.querySelectorAll('#simModeChoices .room-choice').forEach(b =>
    b.classList.toggle('sel', b.dataset.simmode === mode));
}

function simulate(ctrl){
  busyClick(ctrl, 'Playing the season…', async () => {
    const enter = SIM_MODE === 'matchbymatch' ? 'reveal'
                : SIM_MODE === 'groupstage' ? 'groupstage' : 'whole';
    location.href = `/season?enter=${enter}#${S.state}`;
  });
}

function abandonDraft(){
  location.href = '/';
}

boot().then(() => {
  const [h, query] = location.hash.slice(1).split('?');
  if (query === 'mode=memory') DRAFT_MODE = 'memory';
  if (!h || !h.includes('-')){
    // No draft to resume -- there's nothing this page can show.
    location.href = '/';
    return;
  }
  api('/api/draft/' + h).then(s => render(s)).catch(e => { slip(e.message); location.href = '/'; });
});

async function boot(){
  loadMe();   // fire-and-forget -- the auth control fills in whenever it resolves
  const m = await loadMeta();
  renderDeckStats(m);
}
