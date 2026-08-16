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
    // Signed in: an identity chip that says where it GOES. "Hi, name" was a greeting, not
    // a destination -- nothing about it suggested a career page sat behind it.
    const initial = (ME.username[0] || '?').toUpperCase();
    el.innerHTML = `
      <a href="/profile" class="auth-me" title="Your player card">
        <span class="auth-avatar">${initial}</span>
        <span class="auth-me-text">
          <b>${ME.username}</b>
          <em>Player card →</em>
        </span>
      </a>
      <button class="auth-out" onclick="submitLogout(this)">Sign out</button>`;
  } else {
    // Signed out: a real button carrying its own reason. "Sign in" alone states a
    // mechanism and no benefit, which is a poor trade to ask of someone who can already
    // play the entire game without an account.
    el.innerHTML = `
      <button class="auth-cta" onclick="openAuthModal()">
        <span class="auth-cta-main">Sign in</span>
        <span class="auth-cta-sub">Save your career</span>
      </button>`;
  }
}

// The modal's chrome is built here rather than added to ten copies of the markup -- the
// same "duplicate small markup, share the logic" split the page split already uses, except
// here the markup is big enough that duplicating it would guarantee drift.
const AUTH_COPY = {
  login: ['Welcome back', 'Your card, your caps and every game you have saved.'],
  register: ['Save your career', 'Runs, wickets, titles and both caps -- kept across every game you play.'],
};

function ensureAuthChrome(){
  const frame = document.querySelector('#authOverlay .scorecard-frame');
  if (!frame || frame.querySelector('.auth-hero')) return;
  const hero = document.createElement('div');
  hero.className = 'auth-hero';
  hero.innerHTML = `<div class="auth-hero-eyebrow">The Legends Almanack</div>
    <div class="auth-hero-title" id="authHeroTitle"></div>
    <div class="auth-hero-sub" id="authHeroSub"></div>`;
  frame.insertBefore(hero, frame.firstChild);
  // Everything that was already in the frame becomes the body, so the hero can sit flush
  // against the top edge while the form keeps its own padding.
  const body = document.createElement('div');
  body.className = 'auth-body';
  while (hero.nextSibling) body.appendChild(hero.nextSibling);
  frame.appendChild(body);
  const perks = document.createElement('ul');
  perks.className = 'auth-perks';
  perks.innerHTML = ['Every completed game saved to your card',
                     'Your Orange Cap and Purple Cap, all-time',
                     'Your name filled in automatically in rooms']
                     .map(t => `<li>${t}</li>`).join('');
  body.appendChild(perks);
}

function setAuthCopy(tab){
  const [title, sub] = AUTH_COPY[tab] || AUTH_COPY.register;
  const t = $('#authHeroTitle'), u = $('#authHeroSub');
  if (t) t.textContent = title;
  if (u) u.textContent = sub;
}

function openAuthModal(tab){
  ensureAuthChrome();
  pickAuthTab(tab || 'register');   // registering is the point of the prompt, so lead with it
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
  setAuthCopy(tab);
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
