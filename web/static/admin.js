// The admin console [A154]. Everything here is read from /api/admin/*, which answers 404
// to anyone who is not an admin -- so this file holds no secret and no permission check
// of its own worth trusting. The page simply shows "not for you" when the overview 404s.
//
// Every string that came from a user (username, email, kit name, room seat name) is drawn
// through esc(): room names in particular are typed by strangers [A152].

const ADM = {accounts: {q: '', offset: 0}, loaded: {}};
const JSON_HEADERS = {'Content-Type': 'application/json'};

function fmtNum(n){ return (n == null ? '–' : Number(n).toLocaleString()); }

function ago(iso){
  if (!iso) return '–';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  if (s < 86400 * 30) return Math.floor(s / 86400) + 'd ago';
  return new Date(iso).toLocaleDateString();
}

function when(iso){
  return iso ? `<span title="${esc(new Date(iso).toLocaleString())}">${ago(iso)}</span>` : '–';
}

function adminTab(tab){
  document.querySelectorAll('#adminTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.tab === tab));
  document.querySelectorAll('.adm-panel')
    .forEach(p => p.classList.toggle('hide', p.id !== 'tab-' + tab));
  try { history.replaceState(null, '', '#' + tab); } catch(e){}
  if (ADM.loaded[tab]) return;
  ADM.loaded[tab] = true;
  if (tab === 'accounts') loadAccounts();
  if (tab === 'daily') loadDaily();
  if (tab === 'rooms') loadRooms();
}

// --- overview ------------------------------------------------------------------------

function bars(series, label){
  const max = Math.max(1, ...series.map(d => d.count));
  const total = series.reduce((a, d) => a + d.count, 0);
  return `<div class="adm-card">
    <div class="adm-card-head">${label}<em>${total} in ${series.length} days</em></div>
    <div class="adm-bars">${series.map(d => `
      <div class="adm-bar" title="${esc(d.date)}: ${d.count}">
        <span style="height:${Math.round(100 * d.count / max)}%"></span>
        <em>${d.count || ''}</em>
      </div>`).join('')}
    </div>
    <div class="adm-bars-axis"><span>${esc(series[0].date.slice(5))}</span>
      <span>${esc(series[series.length - 1].date.slice(5))}</span></div>
  </div>`;
}

function statTile(value, label){
  return `<div><b>${fmtNum(value)}</b><span>${label}</span></div>`;
}

async function loadOverview(){
  const o = await api('/api/admin/overview');
  const rs = o.rooms_by_status || {};
  const liveRooms = (rs.lobby || 0) + (rs.drafting || 0) + (rs.auctioning || 0);
  const h = o.health || {}, db = h.db || {};
  $('#tab-overview').innerHTML = `
    <div class="career-grid adm-stats">
      ${statTile(o.accounts, 'Accounts')}
      ${statTile(o.accounts_1d, 'New today (24h)')}
      ${statTile(o.accounts_7d, 'New this week')}
      ${statTile(o.active_accounts_7d, 'Active this week')}
      ${statTile(o.games_saved, 'Games saved')}
      ${statTile(o.games_7d, 'Games this week')}
      ${statTile(o.daily_today, "Today's daily")}
      ${statTile(liveRooms, 'Live rooms')}
    </div>
    <div class="adm-two">
      ${bars(o.signups_by_day, 'New accounts')}
      ${bars(o.games_by_day, 'Games saved')}
    </div>
    <div class="adm-card">
      <div class="adm-card-head">Newest accounts<em><a href="#accounts" onclick="adminTab('accounts');return false">All accounts →</a></em></div>
      <div id="adminNewest">Loading…</div>
    </div>
    <div class="adm-two">
      <div class="adm-card">
        <div class="adm-card-head">Rooms</div>
        <dl class="adm-dl">
          ${['lobby', 'drafting', 'auctioning', 'complete', 'failed']
            .map(s => `<dt>${s}</dt><dd>${fmtNum(rs[s] || 0)}</dd>`).join('')}
          <dt>open lobbies</dt><dd>${fmtNum(o.open_lobbies)}</dd>
        </dl>
      </div>
      <div class="adm-card">
        <div class="adm-card-head">System</div>
        <dl class="adm-dl">
          <dt>deck</dt><dd>${fmtNum(h.cards)} cards · ${fmtNum(h.franchise_seasons)} squads</dd>
          <dt>deck source</dt><dd>${esc(h.source || '–')}</dd>
          <dt>db connections</dt><dd>${fmtNum(db.opened)} opened · ${fmtNum(db.reused)} reused</dd>
          <dt>daily attempts</dt><dd>${fmtNum(o.daily_all_time)} all time</dd>
          <dt>UTC day</dt><dd>${esc(o.today)}</dd>
        </dl>
      </div>
    </div>`;
  const page = await api('/api/admin/accounts?offset=0');
  $('#adminNewest').innerHTML = accountsTable(page.accounts.slice(0, 8));
}

// --- accounts ------------------------------------------------------------------------

function accountsTable(rows){
  if (!rows.length) return '<p class="adm-empty">No accounts match.</p>';
  return `<div class="adm-scroll"><table class="adm-table">
    <thead><tr><th>#</th><th>User</th><th>Email</th><th>Joined</th><th>Games</th>
      <th>Titles</th><th>Dailies</th><th>Last active</th></tr></thead>
    <tbody>${rows.map(a => `
      <tr class="adm-click" onclick="openAccount(${a.account_id})">
        <td class="adm-dim">${a.account_id}</td>
        <td><b>${esc(a.username)}</b>${a.is_admin ? ' <span class="adm-tag">admin</span>' : ''}</td>
        <td class="adm-dim">${esc(a.email)}</td>
        <td>${when(a.created_at)}</td>
        <td>${fmtNum(a.games_saved)}</td>
        <td>${fmtNum(a.titles)}</td>
        <td>${fmtNum(a.daily_played)}</td>
        <td>${when(a.last_active)}</td>
      </tr>`).join('')}</tbody></table></div>`;
}

function adminSearch(){
  ADM.accounts = {q: $('#adminQuery').value.trim(), offset: 0};
  loadAccounts();
}

function accountsPage(delta){
  ADM.accounts.offset = Math.max(0, ADM.accounts.offset + delta);
  loadAccounts();
}

async function loadAccounts(){
  const {q, offset} = ADM.accounts;
  const el = $('#adminAccounts');
  el.innerHTML = '<p class="adm-empty">Loading…</p>';
  try {
    const p = await api(`/api/admin/accounts?q=${encodeURIComponent(q)}&offset=${offset}`);
    const last = Math.min(p.total, offset + p.accounts.length);
    el.innerHTML = `
      <p class="adm-count">${p.total ? `${offset + 1}–${last} of ${fmtNum(p.total)}` : '0'}
        account${p.total === 1 ? '' : 's'}${q ? ` matching “${esc(q)}”` : ''}</p>
      ${accountsTable(p.accounts)}
      <div class="adm-toolbar">
        <button class="act minor" ${offset ? '' : 'disabled'} onclick="accountsPage(-${p.page})">← Newer</button>
        <button class="act minor" ${last < p.total ? '' : 'disabled'} onclick="accountsPage(${p.page})">Older →</button>
      </div>`;
  } catch(e){ el.innerHTML = `<p class="adm-empty">${esc(e.message)}</p>`; }
}

async function openAccount(id){
  $('#adminAccountBody').innerHTML = '<p class="adm-empty">Loading…</p>';
  $('#adminAccountOverlay').classList.remove('hide');
  try { renderAccount(await api('/api/admin/accounts/' + id)); }
  catch(e){ $('#adminAccountBody').innerHTML = `<p class="adm-empty">${esc(e.message)}</p>`; }
}

function closeAccount(e){
  if (e && e.target !== e.currentTarget) return;
  $('#adminAccountOverlay').classList.add('hide');
}

function renderAccount(a){
  const self = ME && ME.account_id === a.account_id;
  const locked = a.is_admin && !self;   // another admin: view only, as the server enforces
  const games = a.recent_games.map(g => `<tr><td>${esc(g.source)}</td>
      <td>${g.champion ? '🏆 champion' : '–'}</td>
      <td>${g.matches_won == null ? '–' : `${g.matches_won}/${g.matches_played}`}</td>
      <td>${when(g.completed_at)}</td></tr>`).join('');
  const dailies = a.recent_dailies.map(d => `<tr><td>${esc(d.challenge_date)}</td>
      <td>${d.objective_met ? 'met' : 'missed'}</td><td>${d.margin}</td>
      <td>+${d.bonus_points}</td></tr>`).join('');
  const leaders = (rows, unit) => rows.length
    ? rows.map(r => `${esc(r.name)} <span class="adm-dim">${r.total} ${unit}</span>`).join('<br>')
    : '<span class="adm-dim">–</span>';
  $('#adminAccountBody').innerHTML = `
    <div class="adm-acct-head">
      <div class="adm-dim">Account #${a.account_id}${a.is_admin ? ' · <span class="adm-tag">admin</span>' : ''}</div>
      <div class="adm-acct-name">@${esc(a.username)}</div>
      <div class="adm-dim">${esc(a.email)} · joined ${esc(new Date(a.created_at).toLocaleString())}</div>
      ${a.kit ? `<div class="adm-dim">Kit: ${esc(a.kit.name)} (${esc(a.kit.monogram)}, ${esc(a.kit.colour)})</div>` : ''}
    </div>
    <div class="career-grid adm-stats">
      ${statTile(a.games_played, 'Games saved')}${statTile(a.titles_won, 'Titles')}
      ${statTile(a.total_runs, 'Runs')}${statTile(a.total_wickets, 'Wickets')}
    </div>
    <div class="adm-two">
      <div class="adm-card"><div class="adm-card-head">Top batters</div>${leaders(a.top_batters, 'runs')}</div>
      <div class="adm-card"><div class="adm-card-head">Top bowlers</div>${leaders(a.top_bowlers, 'wkts')}</div>
    </div>
    <div class="adm-card"><div class="adm-card-head">Recent games</div>
      ${games ? `<div class="adm-scroll"><table class="adm-table"><thead><tr><th>Mode</th><th>Result</th><th>Won</th><th>When</th></tr></thead><tbody>${games}</tbody></table></div>` : '<p class="adm-empty">None saved.</p>'}</div>
    <div class="adm-card"><div class="adm-card-head">Recent dailies</div>
      ${dailies ? `<div class="adm-scroll"><table class="adm-table"><thead><tr><th>Day</th><th>Objective</th><th>Margin</th><th>Bonus</th></tr></thead><tbody>${dailies}</tbody></table></div>` : '<p class="adm-empty">None played.</p>'}</div>
    ${locked ? '<p class="room-note">This is another admin\'s account, so it can only be viewed here.</p>' : `
    <div class="adm-card adm-actions">
      <div class="adm-card-head">Manage</div>
      <label class="room-label">Username</label>
      <div class="adm-row"><input id="admRename" class="room-input" value="${esc(a.username)}" maxlength="24">
        <button class="act" onclick="adminRename(this, ${a.account_id})">Rename</button></div>
      <label class="room-label">Set a new password</label>
      <div class="adm-row"><input id="admPassword" class="room-input" type="text" placeholder="At least 8 characters" autocomplete="off">
        <button class="act" onclick="adminPassword(this, ${a.account_id})">Set password</button></div>
      <p class="room-note">There is no password-reset email, so this is how a locked-out player gets back in: set one and tell them. Their existing sign-ins stay valid until they expire.</p>
      <div class="adm-row adm-danger">
        ${a.kit ? `<button class="act minor" onclick="adminClearKit(this, ${a.account_id})">Remove team kit</button>` : ''}
        ${self ? '' : `<button class="act minor adm-del" onclick="adminDelete(this, ${a.account_id}, '${esc(a.username)}')">Delete account</button>`}
      </div>
    </div>`}`;
}

async function adminPost(ctrl, path, body, method){
  let ok = false;
  await busyClick(ctrl, null, async () => {
    try {
      await api(path, {method: method || 'POST', headers: JSON_HEADERS,
                       body: body ? JSON.stringify(body) : undefined});
      ok = true;
    } catch(e){ slip(e.message); }
  });
  return ok;
}

async function refreshAfterChange(id){
  ADM.loaded = {overview: true};
  loadAccounts();
  if (id != null) openAccount(id);
}

async function adminRename(ctrl, id){
  const username = $('#admRename').value.trim();
  if (await adminPost(ctrl, `/api/admin/accounts/${id}/username`, {username})){
    slip('Renamed to @' + username);
    if (ME && ME.account_id === id){ loadMe(); }
    refreshAfterChange(id);
  }
}

async function adminPassword(ctrl, id){
  const password = $('#admPassword').value;
  if (password.length < 8){ slip('A password needs at least 8 characters.'); return; }
  if (await adminPost(ctrl, `/api/admin/accounts/${id}/password`, {password})){
    $('#admPassword').value = '';
    slip('Password set.');
  }
}

async function adminClearKit(ctrl, id){
  if (!confirm('Remove this account\'s team kit (name, monogram and colour)?')) return;
  if (await adminPost(ctrl, `/api/admin/accounts/${id}/clear-kit`)){
    slip('Kit removed.');
    refreshAfterChange(id);
  }
}

async function adminDelete(ctrl, id, username){
  const typed = prompt(`This permanently deletes @${username}, every game they saved and every daily they played. It cannot be undone.\n\nType the username to confirm:`);
  if (typed == null) return;
  if (typed.trim() !== username){ slip('The username did not match, so nothing was deleted.'); return; }
  if (await adminPost(ctrl, `/api/admin/accounts/${id}`, null, 'DELETE')){
    slip('Deleted @' + username);
    closeAccount();
    refreshAfterChange(null);
  }
}

// --- the daily ---------------------------------------------------------------------------

async function loadDaily(){
  const input = $('#adminDailyDate');
  const el = $('#adminDaily');
  el.innerHTML = '<p class="adm-empty">Loading…</p>';
  try {
    const d = await api('/api/admin/daily' + (input.value ? '?date=' + input.value : ''));
    if (!input.value) input.value = d.challenge_date;
    const rows = d.board.map((r, i) => `<tr>
      <td class="adm-dim">${i + 1}</td>
      <td><a href="#" onclick="openAccount(${r.account_id});return false"><b>${esc(r.username)}</b></a></td>
      <td>${r.objective_met ? '<span class="adm-ok">met</span>' : '<span class="adm-dim">missed</span>'}</td>
      <td>${r.margin}</td><td>+${r.bonus_points}</td><td>${when(r.completed_at)}</td>
      <td><button class="act minor" onclick="adminRemoveDaily(this, '${esc(d.challenge_date)}', ${r.account_id}, '${esc(r.username)}')">Remove</button></td>
    </tr>`).join('');
    el.innerHTML = `
      <div class="adm-card">
        <div class="adm-card-head">${esc(d.challenge_date)}<em>${d.board.length} played</em></div>
        <p>${d.scenario ? esc(d.scenario) : '<span class="adm-dim">Nobody opened the daily on this day, so no challenge was generated.</span>'}</p>
      </div>
      ${rows ? `<div class="adm-scroll"><table class="adm-table"><thead><tr><th>#</th><th>Player</th><th>Objective</th><th>Margin</th><th>Bonus</th><th>Finished</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
             : '<p class="adm-empty">No results for this day.</p>'}
      <p class="room-note">Removing a result takes it off the board and lets that player play the day again.</p>`;
  } catch(e){ el.innerHTML = `<p class="adm-empty">${esc(e.message)}</p>`; }
}

async function adminRemoveDaily(ctrl, date, id, username){
  if (!confirm(`Remove @${username}'s result for ${date}? They will be able to play that day again.`)) return;
  if (await adminPost(ctrl, `/api/admin/daily/${date}/${id}`, null, 'DELETE')){
    slip('Result removed.');
    loadDaily();
  }
}

// --- rooms ---------------------------------------------------------------------------

async function loadRooms(){
  const el = $('#adminRooms');
  el.innerHTML = '<p class="adm-empty">Loading…</p>';
  try {
    const {rooms} = await api('/api/admin/rooms');
    if (!rooms.length){ el.innerHTML = '<p class="adm-empty">No rooms right now.</p>'; return; }
    el.innerHTML = `<div class="adm-scroll"><table class="adm-table">
      <thead><tr><th>Code</th><th>Game</th><th>Status</th><th>Players</th><th>Active</th><th></th></tr></thead>
      <tbody>${rooms.map(r => `<tr>
        <td><b class="adm-mono">${esc(r.code)}</b>${r.is_open ? ' <span class="adm-tag">open</span>' : ''}</td>
        <td>${esc(r.game)} · ${esc(r.format)}</td>
        <td>${esc(r.status)}</td>
        <td>${r.players.map(esc).join(', ') || '–'}${r.cpu_seats ? ` <span class="adm-dim">+${r.cpu_seats} CPU</span>` : ''}</td>
        <td>${when(r.updated_at)}</td>
        <td><button class="act minor" onclick="adminDeleteRoom(this, '${esc(r.code)}')">Close</button></td>
      </tr>`).join('')}</tbody></table></div>
      <p class="room-note">Closing a room deletes it for everybody in it; their pages return home.</p>`;
  } catch(e){ el.innerHTML = `<p class="adm-empty">${esc(e.message)}</p>`; }
}

async function adminDeleteRoom(ctrl, code){
  if (!confirm(`Close room ${code} for everybody in it?`)) return;
  if (await adminPost(ctrl, `/api/admin/rooms/${code}`, null, 'DELETE')){
    slip('Room ' + code + ' closed.');
    loadRooms();
  }
}

// --- boot ------------------------------------------------------------------------------

async function boot(){
  loadMeta().then(renderDeckStats).catch(() => {});
  const me = loadMe().catch(() => null);
  try {
    await loadOverview();
  } catch(e){
    await me;
    $('#adminDenied').classList.remove('hide');
    if (e.status !== 404) slip(e.message);
    return;
  }
  await me;
  ADM.loaded.overview = true;
  $('#adminBody').classList.remove('hide');
  const tab = location.hash.slice(1);
  if (['accounts', 'daily', 'rooms'].includes(tab)) adminTab(tab);
}

boot();
