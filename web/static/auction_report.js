// The auction told as a story [A232]: the morning-after paper, every squad ranked, the
// board's brief and the season's verdict on the buys. Shared by the auction page, an auction
// room and the season page. Every word is the server's (game/auction_report.py); this file
// only lays it out, escaping all of it.

function aucMoney(lakh){
  if (lakh == null) return '';
  if (lakh >= 100) return '₹' + (lakh / 100).toFixed(2).replace(/\.?0+$/, '') + ' cr';
  return '₹' + lakh + 'L';
}

const BRIEF_MARK = {open: '○', met: '✓', missed: '✗'};
const BRIEF_KIND = {squad: 'Squad', money: 'Money', season: 'Season'};

// The board's three targets. `compact` is the floor's version, beside the squad.
function aucBriefHtml(brief, compact){
  if (!brief || !brief.length) return '';
  return `<div class="auc-brief${compact ? ' compact' : ''}">
    <div class="auc-brief-head"><b>The board's brief</b><em>for the story · it changes nothing</em></div>
    ${brief.map(t => `<div class="auc-brief-row ${t.state}">
      <span class="auc-brief-mark">${BRIEF_MARK[t.state] || '○'}</span>
      <span class="auc-brief-text"><i>${BRIEF_KIND[t.kind] || ''}</i>${esc(t.text)}</span>
      <span class="auc-brief-prog">${esc(t.progress || '')}</span>
    </div>`).join('')}
  </div>`;
}

// One clipping, in the career's newspaper styling (.np, A228). No year in the masthead: an
// auction is not a season.
function aucClip(c, size, paper){
  return `<article class="np np-${size}${c.tone ? ' np-' + c.tone : ''}">
    <header class="np-mast"><span>${esc(paper)}</span><span>Auction special · ${esc(c.section)}</span></header>
    ${c.crest && size === 'lead' ? `<div class="np-photo">${crestImg(c.crest, 'np-crest')}</div>` : ''}
    <h3 class="np-head">${esc(c.headline)}</h3>
    ${c.dek ? `<p class="np-dek">${esc(c.dek)}</p>` : ''}
    <div class="np-body"><p><b>From our correspondent.</b> ${c.body.map(esc).join(' ')}</p></div>
  </article>`;
}

function aucSquadTableHtml(teams){
  return `<table class="auc-rep-table">
    <thead><tr><th>#</th><th>Squad</th><th>Best twelve</th><th class="wide">Strongest · weakest</th>
      <th>Top buy</th><th>Left</th></tr></thead>
    <tbody>${teams.map(t => `<tr class="${t.you ? 'you' : ''}">
      <td class="rk">${t.rank}</td>
      <td class="tm">${t.crest ? crestImg(t.crest, 'auc-rep-crest') : ''}<b>${esc(t.short)}</b>
        ${t.owner ? `<em>${esc(t.owner)}</em>` : t.you ? '<em>you</em>' : ''}</td>
      <td class="rt">${t.rating != null ? t.rating.toFixed(1) : '—'}</td>
      <td class="wide"><span class="up">${esc(t.strength)}</span><span class="down">${esc(t.gap)}</span>
        ${(t.notes || []).map(n => `<span class="note-chip">${esc(n)}</span>`).join('')}</td>
      <td class="buy">${t.top_buy ? `${esc(t.top_buy.name)}<em>${aucMoney(t.top_price)}</em>` : '—'}</td>
      <td class="left">${aucMoney(t.purse)}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

function aucReportHtml(rep){
  const paper = rep.paper || [];
  return `<div class="auc-rep">
    <div class="auc-sets-head"><span>The morning after</span><h3>${esc(rep.paper_name)}</h3>
      <em>What the auction did, and who came out of it best</em></div>
    ${paper.length ? aucClip(paper[0], 'lead', rep.paper_name) : ''}
    ${paper.length > 1 ? `<div class="np-grid">${paper.slice(1).map(c => aucClip(c, 'small', rep.paper_name)).join('')}</div>` : ''}
    ${aucBriefHtml(rep.brief)}
    <div class="auc-rep-head"><b>Every squad, ranked</b>
      <em>by the face value of its best twelve -- ranked, not graded: the ten sit a few points apart</em></div>
    <div class="auc-rep-scroll">${aucSquadTableHtml(rep.teams || [])}</div>
  </div>`;
}

function aucReportOverlay(){
  let el = document.getElementById('aucReportOverlay');
  if (!el){
    el = document.createElement('div');
    el.id = 'aucReportOverlay';
    el.className = 'card-overlay hide';
    el.onclick = e => { if (e.target === el) closeAuctionReport(); };
    el.innerHTML = `<div class="scorecard-shell auc-rep-shell">
        <div class="scorecard-frame" id="aucReportBody"></div>
        <div class="scorecard-close"><button class="act lead" onclick="closeAuctionReport()">Pick up from here</button></div>
      </div>`;
    document.body.appendChild(el);
  }
  return el;
}

async function openAuctionReport(url){
  const el = aucReportOverlay();
  const body = document.getElementById('aucReportBody');
  body.innerHTML = '<p class="auc-sets-empty">Reading the morning papers…</p>';
  el.classList.remove('hide');
  try { body.innerHTML = aucReportHtml(await api(url)); }
  catch(e){ body.innerHTML = `<p class="auc-sets-empty">${esc(e.message)}</p>`; }
}

function closeAuctionReport(){
  const el = document.getElementById('aucReportOverlay');
  if (el) el.classList.add('hide');
}

// --- the season's verdict on the buys --------------------------------------------------------

function aucBuyLine(ln, label, extra){
  if (!ln) return '';
  const c = ln.card;
  const figs = [ln.runs ? `${ln.runs} runs` : '', ln.wickets ? `${ln.wickets} wkts` : '',
                ln.pom ? `${ln.pom} × Player of the Match` : ''].filter(Boolean).join(' · ');
  return `<div class="auc-verdict-card ${crestClass(c.crest)}">
    <span class="lbl">${esc(label)}</span>
    <div class="nm">${c.crest ? crestImg(c.crest, 'auc-rep-crest') : ''}<b>${esc(c.name)}</b>
      <em>${c.season_year || ''}</em></div>
    <div class="fg">${aucMoney(ln.price)}${ln.retained ? ' · kept' : ''}${extra ? ' · ' + esc(extra) : ''}</div>
    <div class="fg dim">${figs || 'Did little this season'}</div>
  </div>`;
}

function aucVerdictHtml(v){
  const rows = v.yours.map(ln => `<tr class="${ln.played ? '' : 'dnp'}">
      <td>${ICON[ln.card.kind] || ''}${esc(ln.card.name)}</td>
      <td>${ln.legend ? 'free' : aucMoney(ln.price)}${ln.retained ? ' <em>kept</em>' : ''}</td>
      <td>${ln.played ? ln.runs : '—'}</td><td>${ln.played ? ln.wickets : '—'}</td>
      <td>${ln.pom ? '★'.repeat(Math.min(ln.pom, 5)) + (ln.pom > 5 ? ln.pom : '') : ''}</td>
    </tr>`).join('');
  const same = v.bargain && v.best_buy && v.bargain.card.person_id === v.best_buy.card.person_id;
  return `<div class="auc-verdict">
    <div class="panel-head"><h3>The auction, judged</h3>
      <div class="stat">${v.finish ? `<b>${v.finish}${['th','st','nd','rd'][(v.finish % 100 - 20) % 10] || ['th','st','nd','rd'][v.finish] || 'th'}</b><span>in the league</span>` : ''}</div></div>
    <div class="auc-verdict-cards">
      ${aucBuyLine(v.bargain, same ? 'Bargain of the season · and your best buy' : 'Bargain of the season',
                   v.bargain_team ? 'bought by ' + v.bargain_team : '')}
      ${same ? '' : aucBuyLine(v.best_buy, 'Your best buy')}
      ${v.flop ? aucBuyLine(v.flop, 'Your most expensive flop') : `<div class="auc-verdict-card plain"><span class="lbl">Your big buys</span><div class="fg">None of your three dearest buys finished in the bottom half of your side.</div></div>`}
    </div>
    ${aucBriefHtml(v.brief)}
    <div class="auc-verdict-table"><table>
      <thead><tr><th>Your squad</th><th>Paid</th><th>Runs</th><th>Wkts</th><th>PoM</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="note">Bargain of the season: of the league's thirty biggest contributors (runs plus twenty a wicket), the cheapest buy. Player of the Match by the career's own rule.</p></div>
  </div>`;
}

const VERDICTS = {};

// Fetched once per page and cached by `key`: a room's completion screen redraws on every poll.
async function loadAuctionVerdict(url, host, key){
  if (!host) return;
  if (VERDICTS[key]){ host.innerHTML = VERDICTS[key]; return; }
  host.innerHTML = '<p class="note">Judging the buys…</p>';
  try {
    VERDICTS[key] = aucVerdictHtml(await api(url));
    const el = document.getElementById(host.id) || host;
    el.innerHTML = VERDICTS[key];
  } catch(e){ host.innerHTML = ''; }
}
