// [A321] Shareable stat cards: a player's IPL career, or two players compared, drawn as a
// 1080x1350 image (the size a phone shares as a post) from the same compare.json the compare
// page reads, so a card can never show a figure the Almanack's pages do not. Shared through
// the phone's share sheet where the browser can share files, downloaded otherwise.

const SC = {doc: null, src: null, blob: null, name: 'stat-card.png', text: ''};
const SC_DISPLAY = '"Anton","Archivo Black",Impact,sans-serif';
const SC_BODY = '"Archivo",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif';
const SC_FIGS = '"JetBrains Mono",ui-monospace,"SF Mono",Menlo,monospace';
const SC_ORIGIN = 'fineleg.app';

// ------------------------------------------------------------------ what a card says

// The six figures on a player's card, the discipline the player is known for first: a
// bowler's card leads with wickets, everyone else's with runs (the Almanack's own rule, a
// wicket worth twenty runs, decides an all-rounder). Pure, so it is tested under node.
function scTiles(p){
  const n = v => Number(v).toLocaleString('en-US');   // counts: never missing
  const f = (v, d) => v === null || v === undefined ? '–' : Number(v).toFixed(d);
  const bat = [['Runs', n(p.runs)], ['Strike rate', f(p.sr, 1)], ['Average', f(p.avg, 2)], ['Highest', p.hs_t || '–']];
  const bowl = [['Wickets', n(p.wkts)], ['Economy', f(p.econ, 2)], ['Best', p.best_t || '–'], ['Average', f(p.bavg, 2)]];
  const bowler = p.role === 'bowler' || 20 * p.wkts > p.runs;
  const both = p.runs >= 500 && p.wkts >= 25;
  let main;
  if (both) main = bowler ? [bowl[0], bat[0], bowl[1], bat[1]] : [bat[0], bowl[0], bat[1], bowl[1]];
  else main = bowler ? bowl : bat;
  return [['Matches', n(p.m)], ...main, ['Player of the Match', n(p.pom)]];
}

// Each season's bar: runs or wickets, whichever the tiles lead with.
function scSeries(p){
  const wk = scTiles(p)[1][0] === 'Wickets';
  return {label: wk ? 'Wickets each season' : 'Runs each season', bars: p.s.map(s => [s[0], wk ? s[2] : s[1], s[4]])};
}

// ------------------------------------------------------------------ drawing

function scColours(key){
  const el = document.createElement('div');
  if (key) el.className = 'crest-' + key;
  document.body.appendChild(el);
  const css = getComputedStyle(el), v = n => css.getPropertyValue(n).trim();
  const out = {team: v('--team') || '#f5b83d', deep: v('--team-deep') || '#0a1730', ink: v('--team-ink') || '#ffd873'};
  el.remove();
  return out;
}

function scImage(url){
  return new Promise(res => {
    if (!url) return res(null);
    const im = new Image();
    im.onload = () => res(im); im.onerror = () => res(null);
    im.src = url;
  });
}

function scFit(ctx, text, maxW, weight, size, family, min = 16){
  let s = size;
  ctx.font = `${weight} ${s}px ${family}`;
  while (ctx.measureText(text).width > maxW && s > min){ s -= 2; ctx.font = `${weight} ${s}px ${family}`; }
  return s;
}

function scRound(ctx, x, y, w, h, r){
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

function scBackground(ctx, W, H, c, crest, flip = false){
  const g = ctx.createLinearGradient(0, 0, W, H);
  g.addColorStop(0, c.deep); g.addColorStop(1, '#050b1a');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  const r = ctx.createRadialGradient(flip ? 0 : W, 0, 10, flip ? 0 : W, 0, W * 0.9);
  r.addColorStop(0, c.team + '88'); r.addColorStop(1, 'transparent');
  ctx.fillStyle = r; ctx.fillRect(0, 0, W, H);
  if (crest){
    ctx.save(); ctx.globalAlpha = 0.16;
    ctx.translate(flip ? 160 : W - 160, 330); ctx.rotate(flip ? 0.14 : -0.14);
    const s = 560, k = Math.min(s / crest.width, s / crest.height);
    ctx.drawImage(crest, -crest.width * k / 2, -crest.height * k / 2, crest.width * k, crest.height * k);
    ctx.restore();
  }
}

function scFooter(ctx, W, H, path){
  ctx.fillStyle = 'rgba(255,255,255,.08)'; ctx.fillRect(0, H - 92, W, 92);
  ctx.fillStyle = '#f5b83d'; ctx.font = `700 24px ${SC_FIGS}`; ctx.textAlign = 'left';
  ctx.fillText('FINE LEG XI · THE ALMANACK', 64, H - 38);
  const left = ctx.measureText('FINE LEG XI · THE ALMANACK').width;
  const size = scFit(ctx, SC_ORIGIN + path, W - 128 - left - 40, 600, 24, SC_BODY, 12);
  ctx.fillStyle = '#aebedb'; ctx.textAlign = 'right'; ctx.font = `600 ${size}px ${SC_BODY}`;
  ctx.fillText(SC_ORIGIN + path, W - 64, H - 38);
  ctx.textAlign = 'left';
}

const SC_ROLE = {batter: 'BATTER', bowler: 'BOWLER', allrounder: 'ALL-ROUNDER', keeper: 'WICKETKEEPER-BATTER'};

async function scDrawPlayer(canvas, p){
  const ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
  const c = scColours(p.key), crest = await scImage(p.crest);
  scBackground(ctx, W, H, c, crest);
  ctx.fillStyle = c.ink; ctx.font = `700 26px ${SC_FIGS}`;
  const span = p.years[0] === p.years[1] ? `${p.years[0]}` : `${p.years[0]}–${p.years[1]}`;
  ctx.fillText([SC_ROLE[p.role] || 'PLAYER', (p.country || '').toUpperCase(), span].filter(Boolean).join('  ·  '), 64, 110);
  const size = scFit(ctx, p.name.toUpperCase(), W - 128, 400, 150, SC_DISPLAY, 70);
  ctx.fillStyle = '#ffffff'; ctx.font = `400 ${size}px ${SC_DISPLAY}`;
  ctx.fillText(p.name.toUpperCase(), 64, 110 + size + 10);
  ctx.fillStyle = '#d6dff0'; ctx.font = `600 32px ${SC_BODY}`;
  ctx.fillText(`${p.team} · ${p.seasons} season${p.seasons === 1 ? '' : 's'} in the IPL`, 64, 110 + size + 66);
  // the best card, as a badge
  if (p.card){
    const bx = W - 170, by = 110 + size + 30;
    ctx.beginPath(); ctx.arc(bx, by, 74, 0, Math.PI * 2); ctx.fillStyle = c.team; ctx.fill();
    ctx.fillStyle = '#0b1020'; ctx.textAlign = 'center'; ctx.font = `400 74px ${SC_DISPLAY}`;
    ctx.fillText(String(p.card), bx, by + 26);
    ctx.font = `700 15px ${SC_FIGS}`; ctx.fillStyle = '#ffffff';
    ctx.fillText('BEST CARD', bx, by + 104); ctx.textAlign = 'left';
  }
  // six tiles
  const tiles = scTiles(p), top = 110 + size + 130, tw = (W - 128 - 40) / 3, th = 170;
  tiles.forEach(([label, value], i) => {
    const x = 64 + (i % 3) * (tw + 20), y = top + Math.floor(i / 3) * (th + 20);
    scRound(ctx, x, y, tw, th, 22); ctx.fillStyle = 'rgba(5,11,26,.62)'; ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.12)'; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = i === 1 ? c.ink : '#ffffff';
    const vs = scFit(ctx, value, tw - 44, 400, 84, SC_DISPLAY, 40);
    ctx.font = `400 ${vs}px ${SC_DISPLAY}`; ctx.fillText(value, x + 22, y + 26 + vs);
    ctx.fillStyle = '#8aa0c6'; ctx.font = `700 20px ${SC_FIGS}`;
    ctx.fillText(label.toUpperCase(), x + 22, y + th - 22);
  });
  // the career, season by season
  const series = scSeries(p), cy = top + 2 * (th + 20) + 30, ch = H - 92 - cy - 70;
  ctx.fillStyle = '#8aa0c6'; ctx.font = `700 20px ${SC_FIGS}`;
  ctx.fillText(series.label.toUpperCase(), 64, cy + 4);
  const bars = series.bars, max = Math.max(1, ...bars.map(b => b[1])), gap = 8;
  const bw = Math.min(70, (W - 128 - gap * (bars.length - 1)) / Math.max(1, bars.length));
  bars.forEach(([yr, v, key], i) => {
    const x = 64 + i * (bw + gap), h = Math.max(3, (ch - 60) * v / max), y = cy + 24 + (ch - 60) - h;
    ctx.fillStyle = scColours(key).team; scRound(ctx, x, y, bw, h, Math.min(8, bw / 3)); ctx.fill();
    ctx.fillStyle = '#aebedb'; ctx.textAlign = 'center'; ctx.font = `600 ${bw > 40 ? 18 : 14}px ${SC_FIGS}`;
    ctx.fillText(`’${String(yr % 100).padStart(2, '0')}`, x + bw / 2, cy + ch - 8);
    if (v && bw > 34){ ctx.fillStyle = '#ffffff'; ctx.fillText(Number(v).toLocaleString('en-US'), x + bw / 2, y - 10); }
    ctx.textAlign = 'left';
  });
  scFooter(ctx, W, H, p.path);
}

async function scDrawCompare(canvas, x, y, rows){
  const ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
  const cx = scColours(x.key), cyc = scColours(y.key);
  const [ix, iy] = await Promise.all([scImage(x.crest), scImage(y.crest)]);
  const g = ctx.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, cx.deep); g.addColorStop(0.5, '#050b1a'); g.addColorStop(1, cyc.deep);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  [[ix, 200, cx], [iy, W - 200, cyc]].forEach(([im, px, col]) => {
    const r = ctx.createRadialGradient(px, 160, 10, px, 160, 520);
    r.addColorStop(0, col.team + '77'); r.addColorStop(1, 'transparent');
    ctx.fillStyle = r; ctx.fillRect(0, 0, W, H);
    if (im){ ctx.save(); ctx.globalAlpha = 0.22; const s = 260, k = Math.min(s / im.width, s / im.height);
      ctx.drawImage(im, px - im.width * k / 2, 60, im.width * k, im.height * k); ctx.restore(); }
  });
  ctx.textAlign = 'center';
  [[x, 270, cx], [y, W - 270, cyc]].forEach(([p, px, col]) => {
    const s = scFit(ctx, p.name.toUpperCase(), 440, 400, 76, SC_DISPLAY, 36);
    ctx.fillStyle = '#ffffff'; ctx.font = `400 ${s}px ${SC_DISPLAY}`; ctx.fillText(p.name.toUpperCase(), px, 380);
    ctx.fillStyle = col.ink; ctx.font = `700 22px ${SC_FIGS}`; ctx.fillText(`${p.years[0]}–${p.years[1]}`, px, 420);
  });
  ctx.fillStyle = '#6c7fa3'; ctx.font = `400 64px ${SC_DISPLAY}`; ctx.fillText('V', W / 2, 375);
  // The tally is the page's, over every figure it shows, not only the rows this card has room for.
  const wins = [0, 0];
  cmpRows(x, y).forEach(([key, , , better]) => { const l = cmpLeader(x, y, key, better); if (l < 0) wins[0]++; else if (l > 0) wins[1]++; });
  ctx.fillStyle = '#ffffff'; ctx.font = `400 46px ${SC_DISPLAY}`;
  const verdict = wins[0] === wins[1] ? `LEVEL, ${wins[0]} EACH` : `${(wins[0] > wins[1] ? x : y).name.toUpperCase()} LEADS ${Math.max(...wins)}–${Math.min(...wins)}`;
  ctx.fillText(verdict, W / 2, 500);
  const shown = rows.slice(0, 12), avail = H - 92 - 600, half = 230, G = 132;
  const rh = Math.min(86, avail / Math.max(1, shown.length)), start = 570 + (avail - rh * shown.length) / 2;
  shown.forEach((r, i) => {
    const yy = start + i * rh;
    ctx.fillStyle = '#d6dff0'; ctx.textAlign = 'center';
    ctx.font = `600 ${scFit(ctx, r.label, 2 * G - 20, 600, 26, SC_BODY, 16)}px ${SC_BODY}`;
    ctx.fillText(r.label, W / 2, yy + 30);
    ctx.fillStyle = 'rgba(255,255,255,.06)';
    scRound(ctx, W / 2 - G - half, yy + 8, half, 22, 11); ctx.fill();
    scRound(ctx, W / 2 + G, yy + 8, half, 22, 11); ctx.fill();
    const lw = half * r.lx / 100, rw = half * r.ly / 100;
    ctx.globalAlpha = r.lead < 0 ? 1 : 0.4; ctx.fillStyle = cx.team;
    scRound(ctx, W / 2 - G - lw, yy + 8, Math.max(lw, 1), 22, 11); ctx.fill();
    ctx.globalAlpha = r.lead > 0 ? 1 : 0.4; ctx.fillStyle = cyc.team;
    scRound(ctx, W / 2 + G, yy + 8, Math.max(rw, 1), 22, 11); ctx.fill();
    ctx.globalAlpha = 1;
    ctx.font = `${r.lead < 0 ? 800 : 500} 32px ${SC_FIGS}`; ctx.fillStyle = r.lead < 0 ? '#ffffff' : '#8aa0c6';
    ctx.textAlign = 'right'; ctx.fillText(r.vx, W / 2 - G - half - 16, yy + 34);
    ctx.font = `${r.lead > 0 ? 800 : 500} 32px ${SC_FIGS}`; ctx.fillStyle = r.lead > 0 ? '#ffffff' : '#8aa0c6';
    ctx.textAlign = 'left'; ctx.fillText(r.vy, W / 2 + G + half + 16, yy + 34);
  });
  ctx.textAlign = 'left';
  scFooter(ctx, W, H, location.pathname + location.search);
}

// ------------------------------------------------------------------ the overlay and sharing

function scOverlay(){
  let el = document.getElementById('scOverlay');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'scOverlay';
  el.className = 'card-overlay hide';
  el.innerHTML = `<div class="card-frame"><div class="card-scroll"><canvas id="scCanvas" width="1080" height="1350"></canvas></div>
    <div class="card-actions"><button class="act lead" type="button" id="scShare">Share</button>
      <a class="act" id="scDownload" download="stat-card.png">Download</a>
      <button class="act" type="button" id="scClose">Close</button></div></div>`;
  document.body.appendChild(el);
  el.addEventListener('click', e => { if (e.target === el) el.classList.add('hide'); });
  el.querySelector('#scClose').addEventListener('click', () => el.classList.add('hide'));
  el.querySelector('#scShare').addEventListener('click', scShare);
  return el;
}

async function scFonts(){
  if (!document.fonts || !document.fonts.load) return;
  await Promise.all([`400 80px ${SC_DISPLAY}`, `600 30px ${SC_BODY}`, `700 24px ${SC_FIGS}`]
    .map(f => document.fonts.load(f).catch(() => null)));
}

async function scShow(draw, name, text){
  const el = scOverlay();
  el.classList.remove('hide');
  const canvas = document.getElementById('scCanvas');
  await scFonts();
  await draw(canvas);
  SC.name = name; SC.text = text;
  SC.blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
  const a = document.getElementById('scDownload');
  if (a.href.startsWith('blob:')) URL.revokeObjectURL(a.href);
  a.href = URL.createObjectURL(SC.blob);
  a.download = name;
}

async function scShare(){
  const file = SC.blob && new File([SC.blob], SC.name, {type: 'image/png'});
  if (file && navigator.canShare && navigator.canShare({files: [file]})){
    try { await navigator.share({files: [file], text: SC.text}); return; }
    catch (e){ if (e && e.name === 'AbortError') return; }
  }
  document.getElementById('scDownload').click();
}

async function scDoc(src){
  if (typeof CMP !== 'undefined' && CMP.doc) return CMP.doc;
  if (!SC.doc){ SC.doc = await (await fetch(src)).json(); }
  return SC.doc;
}

// A player page: the button carries the file and the page's own slug.
async function scPlayerCard(btn){
  try {
    const doc = await scDoc(btn.dataset.src);
    const p = doc.players[btn.dataset.slug];
    if (!p) return;
    await scShow(c => scDrawPlayer(c, p), `${btn.dataset.slug}-ipl.png`,
      `${p.name} in the IPL: ${location.origin}${p.path}`);
  } catch (e){ if (typeof slip === 'function') slip('Could not draw the card.'); }
}

// The figures a comparison card has room for, in this order; a row the page would not show
// (two bowlers' batting, say) is left out here too.
const SC_HEADLINE = ['m', 'pom', 'card', 'runs', 'sr', 'avg', 'h100', 'sixes', 'wkts', 'econ', 'bavg', 'best'];

// The compare page: the rows as compare.js judged them, so the card and the page agree.
async function scCompareCard(){
  if (typeof CMP === 'undefined' || !CMP.doc || !CMP.a || !CMP.b) return;
  const x = CMP.doc.players[CMP.a], y = CMP.doc.players[CMP.b];
  const rows = cmpRows(x, y).filter(r => SC_HEADLINE.includes(r[0]))
    .sort((a, b) => SC_HEADLINE.indexOf(a[0]) - SC_HEADLINE.indexOf(b[0])).map(([key, label, , better, places]) => ({
    label, lead: cmpLeader(x, y, key, better), vx: cmpShown(x, key, places), vy: cmpShown(y, key, places),
    lx: cmpLen(x[key], y[key], better), ly: cmpLen(y[key], x[key], better)}));
  await scShow(c => scDrawCompare(c, x, y, rows), `${CMP.a}-v-${CMP.b}.png`,
    `${x.name} v ${y.name}: ${location.href}`);
}
