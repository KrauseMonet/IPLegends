// The IPL 2027 auction hub [A324, A326]. The page is plain HTML written by
// tools.build_auction_hub; this only adds the countdown, so a reader without script loses
// nothing but that.

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

function ahStart(){
  const count = document.querySelector('.ah-count[data-day]');
  if (count){
    const t = ahCountText(ahDaysTo(count.dataset.day));
    if (t) count.textContent = t;
  }
}

if (typeof document !== 'undefined' && document.querySelector) ahStart();
if (typeof module !== 'undefined') module.exports = {ahDaysTo, ahCountText};
