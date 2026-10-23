// The auction's sound and the auctioneer's voice [A143, A144], shared by the auction floor
// (auction.js, on the auction page and in a room) and Build a Career's mega-auction years
// [A218]. Nothing here reads a floor's state: announcing a particular sale is the page's job,
// with `speak`, `play` and `priceSpoken`. Load before the page's own script.

// --- sound [A143] -------------------------------------------------------------------------
//
// Synthesised with the Web Audio API rather than recorded: nothing to license, nothing to
// download, and a knock can be pitched per bidder. Browsers refuse to play sound before the
// page has been interacted with, so the context is created on the first click and every
// cue before that is simply silent -- never an error. The auctioneer's voice is the
// browser's own speech synthesis, in one of two styles [A144]: a classic British auction
// room (the default) or the original Indian English announcer. Sound, voice and style are
// all switchable on the floor and remembered.

const SOUND_KEY = 'iplegends_auction_sound', VOICE_KEY = 'iplegends_auction_voice';
const VOICE_STYLE_KEY = 'iplegends_auction_voice_style';
let SOUND_ON = true, VOICE_ON = true, AUDIO = null, VOICE_STYLE = 'british';
try {
  SOUND_ON = localStorage.getItem(SOUND_KEY) !== 'off';
  VOICE_ON = localStorage.getItem(VOICE_KEY) !== 'off';
  VOICE_STYLE = localStorage.getItem(VOICE_STYLE_KEY) === 'indian' ? 'indian' : 'british';
} catch(e) {}

function audio(){
  if (!SOUND_ON) return null;
  if (!AUDIO){
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    AUDIO = new Ctx();
  }
  if (AUDIO.state === 'suspended') AUDIO.resume();
  return AUDIO.state === 'running' ? AUDIO : null;
}
document.addEventListener('pointerdown', () => { if (SOUND_ON) audio(); }, {once: true});

function tone(freq, dur, {type = 'sine', gain = 0.18, at = 0, slide = null} = {}){
  const ctx = audio();
  if (!ctx) return;
  const t = ctx.currentTime + at;
  const osc = ctx.createOscillator(), amp = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t);
  if (slide) osc.frequency.exponentialRampToValueAtTime(slide, t + dur);
  amp.gain.setValueAtTime(gain, t);
  amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  osc.connect(amp).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + dur + 0.02);
}

function knock(at = 0, pitch = 1){
  const ctx = audio();
  if (!ctx) return;
  // A wooden knock: a short noise burst through a band-pass, over a falling low tone.
  const t = ctx.currentTime + at;
  const len = Math.floor(ctx.sampleRate * 0.06);
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len) ** 3;
  const src = ctx.createBufferSource(), band = ctx.createBiquadFilter(), amp = ctx.createGain();
  src.buffer = buf;
  band.type = 'bandpass'; band.frequency.value = 900 * pitch; band.Q.value = 1.4;
  amp.gain.value = 0.9;
  src.connect(band).connect(amp).connect(ctx.destination);
  src.start(t);
  tone(220 * pitch, 0.12, {type: 'triangle', gain: 0.25, at, slide: 110 * pitch});
}

const SOUNDS = {
  bid: mine => tone(mine ? 880 : 520, 0.07, {type: 'triangle', gain: mine ? 0.16 : 0.09}),
  tick: () => tone(1250, 0.05, {gain: 0.07}),
  sold: mine => {
    knock(0); knock(0.17);
    tone(70, 0.35, {gain: 0.3, at: 0.17});
    if (mine){ tone(880, 0.5, {gain: 0.12, at: 0.35}); tone(1320, 0.6, {gain: 0.1, at: 0.45}); }
  },
  unsold: () => knock(0, 0.7),
  attention: () => { tone(660, 0.18, {gain: 0.14}); tone(990, 0.3, {gain: 0.12, at: 0.16}); },
};

function play(name, arg){ try { SOUNDS[name](arg); } catch(e) {} }

function priceWords(lakh){
  if (lakh < 100) return `${lakh} lakh`;
  const crore = (lakh / 100).toFixed(2).replace(/\.?0+$/, '');
  return `${crore} crore`;
}

// --- the auctioneer's voice [A144] -------------------------------------------------------
//
// Only the browser's own voices: nothing recorded, nothing cloned, no speech service. The
// British style asks for a British male voice where the device has one (Daniel on Apple,
// Google UK English Male in Chrome, Ryan or George on Windows) and speaks lower and more
// deliberately, in short phrases with a beat between them -- the rhythm is what makes it an
// auction room, since the device decides the timbre. A device with no British voice gets
// any British one, then any English one, so the patter still runs in whatever voice exists.

const BRITISH_MALE = /daniel|arthur|oliver|george|ryan|thomas|uk english male|male/i;
let VOICES = [];
function refreshVoices(){ try { VOICES = speechSynthesis.getVoices(); } catch(e) {} }
if ('speechSynthesis' in window){
  refreshVoices();
  // Chrome fills the list asynchronously; the first getVoices() is usually empty.
  speechSynthesis.addEventListener && speechSynthesis.addEventListener('voiceschanged', refreshVoices);
}

function pickVoice(){
  if (!VOICES.length) refreshVoices();
  const lang = v => (v.lang || '').replace('_', '-');
  if (VOICE_STYLE === 'british'){
    const gb = VOICES.filter(v => lang(v) === 'en-GB');
    return gb.find(v => BRITISH_MALE.test(v.name) && !/female/i.test(v.name))
        || gb[0] || VOICES.find(v => lang(v).startsWith('en')) || null;
  }
  return VOICES.find(v => lang(v) === 'en-IN') || VOICES.find(v => lang(v).startsWith('en')) || null;
}

// A line is a list of phrases, queued as separate utterances so each gets its own beat.
function speak(text){
  if (!VOICE_ON || !('speechSynthesis' in window)) return;
  try {
    speechSynthesis.cancel();              // never queue behind a stale announcement
    const voice = pickVoice();
    const british = VOICE_STYLE === 'british';
    for (const phrase of [].concat(text)){
      const u = new SpeechSynthesisUtterance(phrase);
      u.voice = voice;
      if (voice) u.lang = voice.lang;
      u.rate = british ? 0.9 : 1.05;
      u.pitch = british ? 0.85 : 1;
      speechSynthesis.speak(u);
    }
  } catch(e) {}
}

// An auctioneer says "two crore, forty lakh", not "two point four crore".
function priceSpoken(lakh){
  const crore = Math.floor(lakh / 100), rest = lakh % 100;
  if (!crore) return `${rest} lakh`;
  return rest ? `${crore} crore, ${rest} lakh` : `${crore} crore`;
}

function setSound(on){
  SOUND_ON = on;
  try { localStorage.setItem(SOUND_KEY, on ? 'on' : 'off'); } catch(e) {}
  if (on) audio();
  if (typeof renderSoundControls === 'function') renderSoundControls();
}
function setVoice(on){
  VOICE_ON = on;
  try { localStorage.setItem(VOICE_KEY, on ? 'on' : 'off'); } catch(e) {}
  if (!on && 'speechSynthesis' in window) speechSynthesis.cancel();
  if (typeof renderSoundControls === 'function') renderSoundControls();
}
