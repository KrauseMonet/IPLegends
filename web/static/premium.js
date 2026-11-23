// /premium [A305]: the passes, ordering one, paying by UPI and submitting the UTR.
//
// Prices, references and the UPI link all come from /api/premium -- nothing here decides
// what a pass costs or whether you hold one. The pass itself is checked on the server by
// every premium feature; this page only shows where your order stands.

const PM = {data: null};
const PM_JSON = {'Content-Type': 'application/json'};

function rupees(n){ return '₹' + Number(n).toLocaleString('en-IN'); }

function fmtDay(iso){
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-IN', {day: 'numeric', month: 'short', year: 'numeric'});
}

const PM_STATUS = {
  pending: o => o.utr ? 'Payment submitted · waiting for a check' : 'Waiting for your payment',
  approved: o => o.ends_on ? `Active until ${fmtDay(o.ends_on)}` : 'Active · never ends',
  rejected: () => 'No matching payment was found',
  revoked: () => 'Ended and refunded',
};

function passCard(p){
  const held = PM.data.active;
  const left = p.limit != null ? `<div class="pm-left">${p.left} of ${p.limit} left</div>` : '';
  const ends = p.ends_on ? `Valid until ${fmtDay(p.ends_on)}` : 'Never ends';
  let button;
  if (p.unavailable) button = `<button class="act" disabled>${esc(p.unavailable)}</button>`;
  else if (held && (!held.ends_on || (p.ends_on && held.ends_on >= p.ends_on)))
    button = `<button class="act" disabled>You have this covered</button>`;
  else button = `<button class="act lead" onclick="pmBuy(this, '${esc(p.kind)}')">Get the ${esc(p.name)}</button>`;
  return `<div class="pm-pass pm-${esc(p.kind)}">
    <div class="pm-name">${esc(p.name)}</div>
    <div class="pm-price">${rupees(p.rupees)}<span>one-time</span></div>
    <div class="pm-ends">${ends}</div>
    <p>${esc(p.blurb)}</p>
    ${left}
    ${button}
  </div>`;
}

function renderActive(){
  const a = PM.data.active, el = $('#pmActive');
  if (!a){ el.classList.add('hide'); return; }
  el.classList.remove('hide');
  el.innerHTML = `<b>★ ${esc(a.name)}</b> ${a.ends_on ? `active until ${fmtDay(a.ends_on)}` : 'active for good'}. Thank you.`;
}

// The order being paid for: the UPI button (phones), the QR with the amount and note to type
// (laptops), and the UTR box.
function renderOrder(o){
  const el = $('#pmOrder');
  if (!o){ el.classList.add('hide'); el.innerHTML = ''; return; }
  el.classList.remove('hide');
  const paid = !!o.utr;
  el.innerHTML = `
    <h2>Pay for your ${esc(o.name)}</h2>
    <div class="pm-pay">
      <div class="pm-pay-main">
        <div class="pm-ref">Reference <code>${esc(o.reference)}</code></div>
        <p>Pay <b>${rupees(o.rupees)}</b> to <code>${esc(PM.data.upi_id)}</code>
          (${esc(PM.data.payee)}) with <b>${esc(o.note)}</b> in the payment note.</p>
        <a class="act lead pm-upi" href="${esc(o.upi_link)}">Pay ${rupees(o.rupees)} in your UPI app</a>
        <p class="pm-dim">On a phone the button fills in the amount and the note for you.</p>
      </div>
      <div class="pm-pay-qr">
        <img src="/static/upi-qr.png" alt="UPI QR code" width="160" height="160">
        <p class="pm-dim">On a laptop, scan this, enter ${rupees(o.rupees)} and type
          <b>${esc(o.note)}</b> in the note.</p>
      </div>
    </div>
    <form class="pm-utr" onsubmit="event.preventDefault(); pmClaim(this, ${o.pass_id})">
      <label class="room-label" for="pmUtr">UTR (UPI transaction ID)</label>
      <input id="pmUtr" class="room-input" inputmode="numeric" autocomplete="off"
        placeholder="12 digits, from your UPI app's payment details" value="${esc(o.utr || '')}">
      <button class="act lead" type="submit">${paid ? 'Update UTR' : "I've paid"}</button>
    </form>
    ${paid ? '<p class="pm-ok">Got it. Your pass starts once the payment has been checked, usually within a day.</p>' : ''}`;
}

function renderOrders(){
  const list = PM.data.orders || [], el = $('#pmOrders');
  if (!list.length){ el.innerHTML = ''; return; }
  el.innerHTML = `<h2>Your orders</h2>
    <div class="adm-scroll"><table class="adm-table">
      <thead><tr><th>Pass</th><th>Reference</th><th>Amount</th><th>Status</th><th></th></tr></thead>
      <tbody>${list.map(o => `<tr>
        <td>${esc(o.name)}</td>
        <td><code>${esc(o.reference)}</code></td>
        <td>${rupees(o.rupees)}</td>
        <td>${esc(PM_STATUS[o.status] ? PM_STATUS[o.status](o) : o.status)}${o.note_from_admin ? `<div class="pm-dim">${esc(o.note_from_admin)}</div>` : ''}</td>
        <td>${o.status === 'pending' ? `<button class="act minor" onclick="pmShow(${o.pass_id})">${o.utr ? 'View' : 'Pay'}</button>` : ''}</td>
      </tr>`).join('')}</tbody></table></div>`;
}

function render(){
  renderActive();
  $('#pmPasses').innerHTML = PM.data.passes.map(passCard).join('');
  renderOrders();
}

function pmShow(passId){
  const o = (PM.data.orders || []).find(x => x.pass_id === passId);
  renderOrder(o);
  $('#pmOrder').scrollIntoView({behavior: 'smooth', block: 'start'});
}

async function pmBuy(ctrl, kind){
  if (!ME || !ME.account_id){ openAuthModal('register'); return; }
  await busyClick(ctrl, 'One moment…', async () => {
    try {
      const o = await api('/api/premium/order',
        {method: 'POST', headers: PM_JSON, body: JSON.stringify({kind}), retry: true});
      await load();
      pmShow(o.pass_id);
    } catch(e){ slip(e.message); }
  });
}

async function pmClaim(form, passId){
  const utr = form.querySelector('input').value;
  await busyClick(form.querySelector('button'), 'Saving…', async () => {
    try {
      await api('/api/premium/claim',
        {method: 'POST', headers: PM_JSON, body: JSON.stringify({pass_id: passId, utr}), retry: true});
      await load();
      pmShow(passId);
    } catch(e){ slip(e.message); }
  });
}

async function load(){
  PM.data = await api('/api/premium');
  render();
}

async function boot(){
  loadMeta().then(renderDeckStats).catch(() => {});
  await Promise.all([loadMe().catch(() => null), load().catch(e => slip(e.message))]);
  // A pending order that has not been paid opens straight away, so a returning buyer
  // lands on the step they left.
  const waiting = (PM.data && PM.data.orders || []).find(o => o.status === 'pending' && !o.utr);
  if (waiting) renderOrder(waiting);
}

document.addEventListener('signedin', () => load().catch(e => slip(e.message)));

boot();
