// /hall: every saved career [A210], inducted first, the rest below. Signed out goes home.

function renderAllHall(list){
  const {inn, rest} = hofSplit(list);
  $('#hallRule').textContent = `Into the Hall of Fame takes ${HOF_RULE}.`;
  if (!list.length){
    $('#hofList').innerHTML = `<p class="pf-empty">Careers you finish in <a class="pf-link" href="/career">Build a Career</a> are kept here.</p>`;
    return;
  }
  $('#hofList').innerHTML =
    `<div class="hof-group">Inducted · ${inn.length}</div>`
    + (inn.length ? inn.map(hofRow).join('') : '<p class="pf-empty">None of your careers has made it yet.</p>')
    + (rest.length ? `<div class="hof-group">Other careers · ${rest.length}</div>${rest.map(hofRow).join('')}` : '');
}

async function boot(){
  const meta = loadMeta().then(m => { renderDeckStats(m); });
  const me = loadMe();
  const hall = api('/api/career/hall').catch(err => err);
  await Promise.all([meta, me]);
  if (!ME || !ME.account_id){ location.href = '/'; return; }
  const list = await hall;
  if (list instanceof Error){ slip(list.message); return; }
  renderAllHall(list);
}

boot();
