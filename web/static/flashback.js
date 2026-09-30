// Flashback: a ten-question quiz on real IPL seasons [A147].
//
// The whole quiz arrives in one request, answers included -- there is nothing to protect
// (no leaderboard, nothing saved) and a quiz that needs no server call per question
// cannot stall halfway through one. The seed is the quiz: `?q=SEED` replays the same ten
// questions, which is what "Challenge a friend" sends, with `&s=SCORE` as the score to beat.
//
// Scoring is here rather than on the server for the same reason: nothing depends on it.

const QZ_BEST_KEY = 'iplegends_flashback_best';
// A shared link has to work for whoever reads it, so it names the real site rather than
// whatever host this page happens to be on (A129's reasoning for the daily's share line).
const QZ_SHARE_ORIGIN = 'https://iplegends.vercel.app';
const QZ_POINTS = 100, QZ_SPEED = 50, QZ_STREAK = 20, QZ_STREAK_CAP = 100;

let QZ = null;           // the quiz payload
let QZ_NEXT = null;      // a quiz fetched ahead, so "Start" and "New quiz" are instant
let QZ_I = 0, QZ_SCORE = 0, QZ_RUN = 0, QZ_BEST_RUN = 0;
let QZ_RESULTS = [];     // [{right, points, chosen}]
let QZ_TIMER = null, QZ_DEADLINE = 0, QZ_ANSWERED = false;
let QZ_TARGET = null;    // the score a shared link challenges this player to beat

function qzEsc(s){
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;');
}

function qzFmt(n){ return Number(n).toLocaleString('en-IN'); }

function qzShow(id){
  ['qzStart', 'qzPlay', 'qzDone'].forEach(s => $('#' + s).classList.toggle('hide', s !== id));
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function qzLoadBest(){
  try { return JSON.parse(localStorage.getItem(QZ_BEST_KEY) || 'null'); }
  catch(e){ return null; }
}

function qzSaveBest(score, correct){
  const best = qzLoadBest();
  if (best && best.score >= score) return false;
  try { localStorage.setItem(QZ_BEST_KEY, JSON.stringify({score, correct})); } catch(e){}
  return true;
}

function qzPaintBest(){
  const best = qzLoadBest();
  $('#qzBest').innerHTML = best
    ? `Your best: <b>${qzFmt(best.score)}</b> points, ${best.correct} of 10 right.` : '';
}

async function qzFetch(seed){
  return api('/api/flashback' + (seed ? '?seed=' + encodeURIComponent(seed) : ''));
}

// --- start ---------------------------------------------------------------------------------

async function qzStart(ctrl){
  await busyClick(ctrl, 'Dealing…', async () => {
    try {
      const quiz = QZ_NEXT || await qzFetch(null);
      QZ_NEXT = null;
      qzBegin(quiz);
    } catch(e){ slip(e.message); }
  });
}

async function qzAgain(ctrl){
  QZ_TARGET = null;
  history.replaceState(null, '', '/flashback');
  $('#qzChallenge').classList.add('hide');
  await qzStart(ctrl);
}

function qzBegin(quiz){
  QZ = quiz;
  QZ_I = 0; QZ_SCORE = 0; QZ_RUN = 0; QZ_BEST_RUN = 0; QZ_RESULTS = [];
  $('#qzScore').textContent = '0';
  qzShow('qzPlay');
  qzAsk();
  // The next quiz, fetched while this one is played.
  if (!QZ_TARGET) qzFetch(null).then(q => { QZ_NEXT = q; }).catch(() => {});
}

// --- one question --------------------------------------------------------------------------

function qzDots(){
  $('#qzDots').innerHTML = QZ.questions.map((_, i) => {
    const r = QZ_RESULTS[i];
    const cls = r ? (r.right ? 'right' : 'wrong') : (i === QZ_I ? 'now' : '');
    return `<i class="${cls}"></i>`;
  }).join('');
}

function qzAsk(){
  const q = QZ.questions[QZ_I];
  QZ_ANSWERED = false;
  qzDots();
  $('#qzNum').textContent = `Question ${QZ_I + 1} of ${QZ.questions.length}`;
  $('#qzCrest').innerHTML = crestImg(q.crest, 'qz-crest');
  $('#qzPrompt').textContent = q.prompt;
  $('#qzClues').innerHTML = q.clues.map(c => `<li>${qzEsc(c)}</li>`).join('');
  const two = q.options.length === 2;
  $('#qzOptions').className = 'qz-options' + (two ? ' two' : '');
  $('#qzOptions').innerHTML = q.options.map((o, i) => `
    <button class="qz-opt" onclick="qzAnswer(${i})" data-i="${i}">
      <kbd>${i + 1}</kbd>${crestImg(o.crest, 'qz-opt-crest')}
      <span class="qz-opt-label">${qzEsc(o.label)}</span>
      <span class="qz-opt-detail"></span>
    </button>`).join('');
  $('#qzAfter').classList.add('hide');
  const card = $('#qzCard');
  card.classList.remove('enter'); void card.offsetWidth; card.classList.add('enter');
  qzStartTimer();
}

function qzStartTimer(){
  clearInterval(QZ_TIMER);
  const total = QZ.seconds_per_question * 1000;
  QZ_DEADLINE = performance.now() + total;
  const bar = $('#qzTimer');
  const tick = () => {
    const left = Math.max(0, QZ_DEADLINE - performance.now());
    bar.style.width = (100 * left / total) + '%';
    bar.classList.toggle('urgent', left < 5000);
    if (left <= 0) qzAnswer(-1);
  };
  tick();
  QZ_TIMER = setInterval(tick, 80);
}

function qzAnswer(chosen){
  if (QZ_ANSWERED) return;
  QZ_ANSWERED = true;
  clearInterval(QZ_TIMER);
  const q = QZ.questions[QZ_I];
  const right = chosen === q.answer;
  const total = QZ.seconds_per_question * 1000;
  const left = Math.max(0, QZ_DEADLINE - performance.now());
  let points = 0;
  if (right){
    QZ_RUN += 1;
    QZ_BEST_RUN = Math.max(QZ_BEST_RUN, QZ_RUN);
    points = QZ_POINTS + Math.round(QZ_SPEED * left / total)
           + Math.min(QZ_STREAK_CAP, QZ_STREAK * (QZ_RUN - 1));
  } else {
    QZ_RUN = 0;
  }
  QZ_SCORE += points;
  QZ_RESULTS[QZ_I] = {right, points, chosen};

  document.querySelectorAll('#qzOptions .qz-opt').forEach((b, i) => {
    b.disabled = true;
    const o = q.options[i];
    if (o.detail) b.querySelector('.qz-opt-detail').textContent = o.detail;
    if (i === q.answer) b.classList.add('correct');
    else if (i === chosen) b.classList.add('wrong');
    else b.classList.add('dim');
  });
  const verdict = $('#qzVerdict');
  verdict.className = 'qz-verdict ' + (right ? 'good' : 'bad');
  verdict.innerHTML = right
    ? `Correct <span class="qz-pop">+${points}</span>`
    : (chosen < 0 ? 'Out of time' : 'Not quite');
  $('#qzReveal').textContent = q.reveal;
  $('#qzNext').textContent = QZ_I === QZ.questions.length - 1 ? 'See your score' : 'Next question';
  $('#qzAfter').classList.remove('hide');
  $('#qzScore').textContent = qzFmt(QZ_SCORE);
  const score = $('#qzScore');
  score.classList.remove('bump'); void score.offsetWidth; score.classList.add('bump');
  $('#qzStreak').textContent = QZ_RUN >= 2 ? `🔥 ${QZ_RUN}` : '';
  qzDots();
  $('#qzNext').focus({preventScroll: true});
}

function qzNext(){
  if (!QZ_ANSWERED) return;
  if (QZ_I < QZ.questions.length - 1){ QZ_I += 1; qzAsk(); }
  else qzFinish();
}

// --- the end -------------------------------------------------------------------------------

function qzTitle(correct){
  if (correct === 10) return 'Hall of Fame';
  if (correct >= 8) return 'Orange Cap';
  if (correct >= 6) return 'Playing XI';
  if (correct >= 4) return 'Impact Player';
  if (correct >= 1) return 'Net bowler';
  return 'Water carrier';
}

function qzCorrect(){ return QZ_RESULTS.filter(r => r.right).length; }

function qzGrid(){ return QZ_RESULTS.map(r => r.right ? '🟩' : '🟥').join(''); }

function qzFinish(){
  const correct = qzCorrect();
  const newBest = qzSaveBest(QZ_SCORE, correct);
  $('#qzSeedLabel').textContent = '#' + QZ.seed;
  $('#qzTitle').textContent = qzTitle(correct);
  $('#qzFinal').textContent = qzFmt(QZ_SCORE);
  $('#qzGrid').textContent = qzGrid();
  $('#qzStats').innerHTML = `<span><b>${correct}</b> of ${QZ_RESULTS.length} right</span>
    <span>best streak <b>${QZ_BEST_RUN}</b></span>
    ${newBest ? '<span class="qz-newbest">New personal best</span>' : ''}`;
  const vs = $('#qzVs');
  if (QZ_TARGET != null){
    const diff = QZ_SCORE - QZ_TARGET;
    vs.className = 'qz-vs ' + (diff > 0 ? 'good' : diff < 0 ? 'bad' : '');
    vs.textContent = diff > 0 ? `You beat your friend's ${qzFmt(QZ_TARGET)} by ${qzFmt(diff)}.`
      : diff < 0 ? `Your friend's ${qzFmt(QZ_TARGET)} stands -- ${qzFmt(-diff)} short.`
      : `Dead level with your friend on ${qzFmt(QZ_TARGET)}. A super over, perhaps.`;
  } else {
    vs.className = 'qz-vs hide';
  }
  $('#qzReview').innerHTML = QZ.questions.map((q, i) => {
    const r = QZ_RESULTS[i];
    const yours = r.chosen < 0 ? 'no answer' : q.options[r.chosen].label;
    return `<li class="${r.right ? 'right' : 'wrong'}">
      <div class="qz-rv-q">${qzEsc(q.prompt)}</div>
      <div class="qz-rv-a"><b>${qzEsc(q.options[q.answer].label)}</b>${r.right ? ''
        : ` <span>(you: ${qzEsc(yours)})</span>`}</div>
      <div class="qz-rv-why">${qzEsc(q.reveal)}</div></li>`;
  }).join('');
  qzShow('qzDone');
  qzPaintBest();
}

// The line is spoiler-free -- a grid, never the answers -- so a friend can play the same
// ten questions without being told them. Share sheet, then clipboard, then the text
// itself: the daily's three-step ladder (A129).
async function qzShare(){
  const correct = qzCorrect();
  const url = `${QZ_SHARE_ORIGIN}/flashback?q=${QZ.seed}&s=${QZ_SCORE}`;
  const text = `Flashback IPL quiz: ${correct}/10, ${qzFmt(QZ_SCORE)} points\n${qzGrid()}\n`
             + `Same ten questions -- can you beat it? ${url}`;
  if (navigator.share){
    try { await navigator.share({text}); return; }
    catch(e){ if (e && e.name === 'AbortError') return; }
  }
  try {
    await navigator.clipboard.writeText(text);
    slip('Copied -- send it to a friend. It holds your score, never the answers.');
  } catch(e){ slip(text); }
}

// --- keys and boot -------------------------------------------------------------------------

document.addEventListener('keydown', e => {
  if ($('#qzPlay').classList.contains('hide') || e.target.tagName === 'INPUT') return;
  if (!QZ_ANSWERED && /^[1-4]$/.test(e.key)){
    const i = Number(e.key) - 1;
    if (QZ && i < QZ.questions[QZ_I].options.length){ e.preventDefault(); qzAnswer(i); }
  } else if (QZ_ANSWERED && e.key === 'Enter'){
    e.preventDefault();
    qzNext();
  }
});

async function boot(){
  loadMe();
  loadMeta().then(renderDeckStats).catch(() => {});
  qzPaintBest();
  const params = new URLSearchParams(location.search);
  const seed = params.get('q');
  if (seed && /^\d+$/.test(seed)){
    const target = params.get('s');
    QZ_TARGET = target && /^\d+$/.test(target) ? Number(target) : null;
    const box = $('#qzChallenge');
    box.innerHTML = QZ_TARGET != null
      ? `<b>You've been challenged.</b> A friend scored ${qzFmt(QZ_TARGET)} on these same ten questions. Beat it.`
      : `<b>A friend sent you this quiz.</b> Same ten questions they played.`;
    box.classList.remove('hide');
    $('#qzStartBtn').textContent = 'Take the challenge';
    try { QZ_NEXT = await qzFetch(seed); } catch(e){ slip(e.message); }
  } else {
    try { QZ_NEXT = await qzFetch(null); } catch(e){ /* fetched again on Start */ }
  }
}
boot();
