// Small shared helpers: hashing, seeded RNG, colour maths, persona normalisation.
import * as THREE from 'three';

export const SPECIES = ['cat', 'fox', 'bunny', 'bear', 'panda', 'frog', 'penguin', 'hamster'];
export const HATS = ['none', 'detective', 'hardhat', 'beret', 'goggles', 'crown', 'headset', 'visor', 'beanie', 'flower', 'bow', 'wizard', 'chef'];
export const PROPS = ['none', 'magnifier', 'laptop', 'brush', 'bugnet', 'quill', 'chart', 'wrench', 'clipboard'];
export const OUTFITS = ['none', 'scarf', 'hoodie', 'overalls', 'cape', 'bowtie', 'apron', 'sweater'];
export const PATTERNS = ['solid', 'tabby', 'calico', 'tuxedo', 'socks', 'spots'];
export const EYES = ['round', 'sparkle', 'sleepy', 'happy', 'dot'];
export const MOUTHS = ['cat', 'smile', 'o', 'tongue'];
export const ROLES = ['research', 'coding', 'design', 'testing', 'writing', 'data', 'devops', 'planning', 'chat'];
export const STATES = ['working', 'waiting', 'idle', 'sleeping', 'error', 'done'];

export const SPECIES_EMOJI = {
  cat: '🐱', fox: '🦊', bunny: '🐰', bear: '🐻', panda: '🐼', frog: '🐸', penguin: '🐧', hamster: '🐹',
};
export const STATE_COLOR = {
  working: '#7cc8a0', waiting: '#ff7aa8', idle: '#9db8e8', sleeping: '#b7a8d8', error: '#ff8a6a', done: '#f2c94c',
};
export const STATE_LABEL = {
  working: 'working', waiting: 'needs you', idle: 'idle', sleeping: 'sleeping', error: 'oops', done: 'done',
};
export const SOURCE_COLOR = { claude: '#f0a36b', codex: '#7fdcc0', 'openai-sdk': '#bba4f0' };

export function hashStr(s) {
  let h = 2166136261 >>> 0;
  s = String(s ?? '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

export function rng(seed) {
  let a = (typeof seed === 'number' ? seed : hashStr(seed)) >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const pick = (r, arr) => arr[Math.floor(r() * arr.length) % arr.length];
export const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const damp = (k, dt) => 1 - Math.exp(-k * dt);

const _c = new THREE.Color();
const _hsl = { h: 0, s: 0, l: 0 };

export function hslHex(h, s, l) {
  return '#' + _c.setHSL(((h % 1) + 1) % 1, clamp(s, 0, 1), clamp(l, 0, 1)).getHexString();
}

/** Shift lightness / saturation of a hex colour (in sRGB HSL space). */
export function shade(hex, dl = 0, ds = 0, dh = 0) {
  _c.set(hex);
  _c.getHSL(_hsl);
  return hslHex(_hsl.h + dh, _hsl.s + ds, _hsl.l + dl);
}

export function mixHex(a, b, t) {
  const ca = new THREE.Color(a), cb = new THREE.Color(b);
  return '#' + ca.lerp(cb, t).getHexString();
}

export function lum(hex) {
  _c.set(hex);
  _c.getHSL(_hsl);
  return _hsl.l;
}

function validHex(x) {
  return typeof x === 'string' && /^#?[0-9a-fA-F]{6}$/.test(x) ? (x[0] === '#' ? x : '#' + x) : null;
}

const oneOf = (v, list, dflt) => (list.includes(v) ? v : dflt);
const num = (v, lo, hi, dflt) => (typeof v === 'number' && isFinite(v) ? clamp(v, lo, hi) : dflt);

const NAMES = ['Mochi', 'Tofu', 'Pudding', 'Boba', 'Kiki', 'Nori', 'Dango', 'Miso', 'Yuzu', 'Momo',
  'Pocky', 'Sushi', 'Latte', 'Biscuit', 'Peach', 'Muffin', 'Sesame', 'Taro', 'Cocoa', 'Maple'];

/**
 * Make any persona (possibly partial / from an older schema) safe to render.
 * Unknown enums fall back to "none" / "cat"; missing colours are derived from the agent id.
 */
export function normPersona(p, seed = 'neko') {
  p = p && typeof p === 'object' ? p : {};
  const r = rng(seed + '|persona');
  const hue = r();
  const fur = validHex(p.fur) || hslHex(hue, 0.55 + r() * 0.25, 0.8 + r() * 0.08);
  return {
    name: typeof p.name === 'string' && p.name.trim() ? p.name.trim().slice(0, 24) : pick(r, NAMES),
    species: oneOf(p.species, SPECIES, 'cat'),
    role: oneOf(p.role, ROLES, 'chat'),
    hat: oneOf(p.hat, HATS, 'none'),
    prop: oneOf(p.prop, PROPS, 'none'),
    outfit: oneOf(p.outfit, OUTFITS, 'none'),
    fur,
    fur2: validHex(p.fur2) || shade(fur, 0.12, -0.2),
    pattern: oneOf(p.pattern, PATTERNS, 'solid'),
    accent: validHex(p.accent) || hslHex(hue + 0.45, 0.7, 0.72),
    eyes: oneOf(p.eyes, EYES, 'round'),
    eye_color: validHex(p.eye_color) || '#3b2a2a',
    mouth: oneOf(p.mouth, MOUTHS, 'cat'),
    blush: p.blush !== false,
    ear_size: num(p.ear_size, 0.6, 1.5, 1.0),
    chubby: num(p.chubby, 0.8, 1.3, 1.0),
    tail: num(p.tail, 0.5, 1.5, 1.0),
    bounce: num(p.bounce, 0, 1, 0.5),
    speed: num(p.speed, 0.6, 1.4, 1.0),
  };
}

export function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + 'k';
  return String(n);
}
export function fmtCost(c) {
  c = c || 0;
  if (c >= 100) return '$' + c.toFixed(0);
  if (c >= 10) return '$' + c.toFixed(1);
  return '$' + c.toFixed(2);
}
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
export function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return Math.round(s) + 's ago';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  return (s / 3600).toFixed(1) + 'h ago';
}
