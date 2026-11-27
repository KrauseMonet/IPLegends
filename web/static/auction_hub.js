// The IPL 2027 auction hub [A324]. The page is plain HTML written by tools.build_auction_hub;
// this only adds the countdown and the register's filters, so a reader without script loses
// nothing but those.

// Days until the auction, counted in Indian time, since that is where it is held.
function ahDaysTo(iso, now){
  const ist = new Date((now || new Date()).toLocaleString('en-US', {timeZone: 'Asia/Kolkata'}));
  const today = Date.UTC(ist.getFullYear(), ist.getMonth(), ist.getDate());
  const [y, m, d] = iso.split('-').map(Number);
  return Math.round((Date.UTC(y, m - 1, d) - today) / 86400000);
}

function ahCountText(days){
  if (days > 1) return `${days} days to go`;
  if (days === 1) return 'tomorrow';
  if (days === 0) return 'today';
  return '';
}

// A row is shown when it passes every filter that has a value.
function ahMatches(row, f){
  if (f.q && !row.name.includes(f.q)) return false;
  for (const k of ['role', 'band', 'os', 'status'])
    if (f[k] && row[k] !== f[k]) return false;
  return true;
}

function ahFilter(){
  const val = id => document.getElementById(id).value;
  const f = {q: val('ahSearch').trim().toLowerCase(), role: val('ahRole'), band: val('ahBand'),
             os: val('ahOs'), status: val('ahStatus')};
  const rows = document.querySelectorAll('#ahRegister tbody tr');
  let shown = 0;
  rows.forEach(tr => {
    const ok = ahMatches({name: tr.dataset.name, role: tr.dataset.role, band: tr.dataset.band,
                          os: tr.dataset.os, status: tr.dataset.status}, f);
    tr.hidden = !ok;
    shown += ok;
  });
  document.getElementById('ahShown').textContent =
    shown === rows.length ? `${rows.length} players` : `${shown} of ${rows.length} players`;
}

function ahStart(){
  const count = document.querySelector('.ah-count[data-day]');
  if (count){
    const t = ahCountText(ahDaysTo(count.dataset.day));
    if (t) count.textContent = ' · ' + t;
  }
  const box = document.getElementById('ahFilters');
  if (box){
    box.hidden = false;
    box.querySelectorAll('input,select').forEach(el => el.addEventListener('input', ahFilter));
    ahFilter();
  }
}

if (typeof document !== 'undefined' && document.getElementById) ahStart();
if (typeof module !== 'undefined') module.exports = {ahDaysTo, ahCountText, ahMatches};
