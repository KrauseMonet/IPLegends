// Shared sign-in/register UI, loaded by every page (home, draft, season, rooms, room,
// profile) -- a sibling to common.js/reveal.js in the same "duplicate small markup,
// share the logic in one file" pattern the multi-page split already established. Login
// state is genuinely global (the session cookie rides every request automatically), so
// unlike DRAFT_MODE there is nothing to carry between pages on navigation -- each page's
// own boot() just calls loadMe() once.
//
// Never a login wall: nothing here blocks or gates ordinary play. Being logged in is
// purely additive elsewhere (a room's own name field pre-fills, a completed game gets
// saved) -- this file's own job is only the sign-in/register control and the modal.

let ME = null;

async function loadMe(){
  ME = await api('/api/auth/me');
  renderAuthArea();
  return ME;
}

function renderAuthArea(){
  const el = $('#authArea');
  if (!el) return;
  if (ME && ME.account_id){
    el.innerHTML = `<a href="/profile" class="auth-link">Hi, ${ME.username}</a>
      <button class="auth-link" onclick="submitLogout(this)">Sign out</button>`;
  } else {
    el.innerHTML = `<button class="auth-link" onclick="openAuthModal()">Sign in</button>`;
  }
}

function openAuthModal(){
  $('#authError').textContent = '';
  $('#authOverlay').classList.remove('hide');
}

function closeAuthModal(e){
  if (e && e.target !== e.currentTarget) return;   // a click inside the frame stays open
  $('#authOverlay').classList.add('hide');
}

function pickAuthTab(tab){
  document.querySelectorAll('#authTabs .room-choice')
    .forEach(b => b.classList.toggle('sel', b.dataset.tab === tab));
  $('#authLoginPanel').classList.toggle('hide', tab !== 'login');
  $('#authRegisterPanel').classList.toggle('hide', tab !== 'register');
  $('#authError').textContent = '';
}

async function submitLogin(ctrl){
  const identifier = $('#authLoginIdentifier').value.trim();
  const password = $('#authLoginPassword').value;
  if (!identifier || !password){ $('#authError').textContent = 'Enter your details.'; return; }
  await busyClick(ctrl, 'Signing in…', async () => {
    try {
      ME = await api('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({identifier, password})});
      renderAuthArea();
      $('#authOverlay').classList.add('hide');
    } catch(e){ $('#authError').textContent = e.message; }
  });
}

async function submitRegister(ctrl){
  const username = $('#authRegisterUsername').value.trim();
  const email = $('#authRegisterEmail').value.trim();
  const password = $('#authRegisterPassword').value;
  if (!username || !email || !password){
    $('#authError').textContent = 'Fill in every field.'; return;
  }
  await busyClick(ctrl, 'Creating account…', async () => {
    try {
      ME = await api('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({username, email, password})});
      renderAuthArea();
      $('#authOverlay').classList.add('hide');
    } catch(e){ $('#authError').textContent = e.message; }
  });
}

async function submitLogout(ctrl){
  await busyClick(ctrl, null, async () => {
    try { await api('/api/auth/logout', {method:'POST'}); } catch(e){ /* clear locally regardless */ }
    ME = {account_id: null, username: null};
    renderAuthArea();
  });
}
