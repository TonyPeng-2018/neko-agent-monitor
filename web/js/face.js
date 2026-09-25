// Kawaii faces painted on a CanvasTexture that wraps the front of the head.
//
// The face decal is a partial sphere (see FACE_GEO in character.js) so the canvas maps
// onto the head with very little distortion: ~3.4 px per degree in both directions.
// Expressions are redrawn only when they change (blink, state change), never per frame.
import * as THREE from 'three';
import { shade } from './util.js';

export const FACE_W = 512, FACE_H = 288;
// Angular window of the decal (radians); must match FACE_W/FACE_H aspect (~1:1 px/deg).
export const FACE_PHI_LEN = 2.6;
export const FACE_THETA_START = 0.95;
export const FACE_THETA_LEN = 1.42;

const CX = FACE_W / 2;
const EYE_Y = 150, EYE_DX = 72;
const MOUTH_Y = 190;
const INK = '#4a2e32';

function ellipse(ctx, x, y, rx, ry, rot = 0) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, rot, 0, Math.PI * 2);
}

function drawRoundEye(ctx, x, y, s, color, sparkle, flip) {
  const rx = 23.5 * s, ry = 30 * s;
  // soft dark rim then gradient iris
  ellipse(ctx, x, y, rx, ry);
  const g = ctx.createLinearGradient(0, y - ry, 0, y + ry);
  g.addColorStop(0, shade(color, -0.08));
  g.addColorStop(0.55, color);
  g.addColorStop(1, shade(color, sparkle ? 0.35 : 0.22, sparkle ? 0.15 : 0.05));
  ctx.fillStyle = g;
  ctx.fill();
  // lower glow crescent
  ctx.save();
  ellipse(ctx, x, y, rx, ry);
  ctx.clip();
  ellipse(ctx, x, y + ry * 0.75, rx * 0.8, ry * 0.45);
  ctx.fillStyle = sparkle ? 'rgba(255,190,230,0.55)' : 'rgba(255,255,255,0.18)';
  ctx.fill();
  ctx.restore();
  // highlights (mirrored a bit per eye for liveliness)
  const hx = flip ? 0.28 : -0.32;
  ctx.fillStyle = '#ffffff';
  ellipse(ctx, x + rx * hx, y - ry * 0.36, rx * 0.44, ry * 0.36, -0.3);
  ctx.fill();
  ellipse(ctx, x - rx * hx * 1.05, y + ry * 0.38, rx * 0.17, ry * 0.14);
  ctx.fill();
  if (sparkle) {
    star(ctx, x - rx * hx * 0.9, y - ry * 0.05, 7 * s, '#ffffff');
  }
}

function star(ctx, x, y, r, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 - Math.PI / 2;
    const rr = i % 2 === 0 ? r : r * 0.32;
    ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
  }
  ctx.closePath();
  ctx.fill();
}

function strokeStyle(ctx, w, color = INK) {
  ctx.lineWidth = w;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = color;
}

/** Draw one eye of the given style at (x, y). */
export function drawEye(ctx, x, y, style, color, flip, s = 1) {
  switch (style) {
    case 'closed': // blink / sleeping: gentle downward curve  ‿
      strokeStyle(ctx, 7 * s);
      ctx.beginPath();
      ctx.arc(x, y - 10 * s, 18 * s, 0.18 * Math.PI, 0.82 * Math.PI);
      ctx.stroke();
      break;
    case 'happy': // ^ ^
      strokeStyle(ctx, 8 * s);
      ctx.beginPath();
      ctx.arc(x, y + 10 * s, 17 * s, 1.15 * Math.PI, 1.85 * Math.PI);
      ctx.stroke();
      break;
    case 'spiral': {
      strokeStyle(ctx, 5 * s, '#5b3b5b');
      ctx.beginPath();
      for (let i = 0; i < 60; i++) {
        const a = i * 0.32 * (flip ? -1 : 1);
        const r = 1 + i * 0.36 * s;
        ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
      }
      ctx.stroke();
      break;
    }
    case 'dot': {
      ellipse(ctx, x, y + 4 * s, 10 * s, 13 * s);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.fillStyle = '#fff';
      ellipse(ctx, x - 3 * s, y - 2 * s, 3.8 * s, 4.2 * s);
      ctx.fill();
      break;
    }
    case 'sleepy': { // content, half-lidded dome eyes
      ctx.save();
      ellipse(ctx, x, y + 17 * s, 30 * s, 28 * s);
      ctx.clip();
      drawRoundEye(ctx, x, y + 3 * s, s * 0.95, color, false, flip);
      ctx.restore();
      strokeStyle(ctx, 6 * s);
      ctx.beginPath();
      ctx.ellipse(x, y + 17 * s, 23 * s, 28 * s, 0, 1.12 * Math.PI, 1.88 * Math.PI);
      ctx.stroke();
      break;
    }
    case 'wide':
      drawRoundEye(ctx, x, y, s * 1.1, color, true, flip);
      break;
    case 'sparkle':
      drawRoundEye(ctx, x, y, s * 1.04, color, true, flip);
      break;
    default:
      drawRoundEye(ctx, x, y, s, color, false, flip);
  }
}

function drawMouth(ctx, style, y) {
  const x = CX;
  switch (style) {
    case 'cat': // ω
      strokeStyle(ctx, 5);
      ctx.beginPath();
      ctx.arc(x - 8, y - 3, 8, 0.05 * Math.PI, 0.95 * Math.PI);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x + 8, y - 3, 8, 0.05 * Math.PI, 0.95 * Math.PI);
      ctx.stroke();
      break;
    case 'smile':
      strokeStyle(ctx, 5);
      ctx.beginPath();
      ctx.arc(x, y - 9, 12, 0.2 * Math.PI, 0.8 * Math.PI);
      ctx.stroke();
      break;
    case 'o':
      ellipse(ctx, x, y + 1, 8, 9.5);
      ctx.fillStyle = '#8a3a4a';
      ctx.fill();
      ellipse(ctx, x, y + 5, 5, 3.5);
      ctx.fillStyle = '#ff9fb0';
      ctx.fill();
      break;
    case 'tiny':
      ellipse(ctx, x, y, 4, 4.5);
      ctx.fillStyle = '#8a3a4a';
      ctx.fill();
      break;
    case 'tongue':
      strokeStyle(ctx, 5);
      ctx.beginPath();
      ctx.arc(x - 8, y - 3, 8, 0.05 * Math.PI, 0.95 * Math.PI);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x + 8, y - 3, 8, 0.05 * Math.PI, 0.95 * Math.PI);
      ctx.stroke();
      ctx.beginPath();
      ctx.ellipse(x + 3, y + 7, 6.5, 8, 0, 0, Math.PI);
      ctx.fillStyle = '#ff8fa6';
      ctx.fill();
      strokeStyle(ctx, 2.5, '#c95a74');
      ctx.stroke();
      break;
    case 'open': { // D-shaped happy open mouth
      ctx.beginPath();
      ctx.moveTo(x - 15, y - 5);
      ctx.quadraticCurveTo(x, y - 7, x + 15, y - 5);
      ctx.quadraticCurveTo(x + 13, y + 16, x, y + 16);
      ctx.quadraticCurveTo(x - 13, y + 16, x - 15, y - 5);
      ctx.fillStyle = '#8a3a4a';
      ctx.fill();
      ctx.save();
      ctx.clip();
      ellipse(ctx, x, y + 15, 10, 7);
      ctx.fillStyle = '#ff9fb0';
      ctx.fill();
      ctx.restore();
      break;
    }
    case 'wavy':
      strokeStyle(ctx, 4.5);
      ctx.beginPath();
      for (let i = 0; i <= 20; i++) {
        const xx = x - 16 + i * 1.6;
        ctx.lineTo(xx, y + Math.sin(i * 0.95) * 3.5);
      }
      ctx.stroke();
      break;
    default:
      drawMouth(ctx, 'smile', y);
  }
}

function drawBlush(ctx, x, y) {
  const g = ctx.createRadialGradient(x, y, 2, x, y, 30);
  g.addColorStop(0, 'rgba(255,120,150,0.62)');
  g.addColorStop(0.6, 'rgba(255,140,165,0.32)');
  g.addColorStop(1, 'rgba(255,150,170,0)');
  ctx.fillStyle = g;
  ellipse(ctx, x, y, 30, 17);
  ctx.fill();
  // three tiny shy lines
  strokeStyle(ctx, 2.2, 'rgba(240,95,125,0.55)');
  for (let i = -1; i <= 1; i++) {
    ctx.beginPath();
    ctx.moveTo(x + i * 8 - 3, y + 4);
    ctx.lineTo(x + i * 8 + 3, y - 4);
    ctx.stroke();
  }
}

function drawNose(ctx, species, y) {
  const x = CX;
  if (species === 'penguin') {
    // little rounded beak
    ctx.beginPath();
    ctx.moveTo(x - 13, y - 6);
    ctx.quadraticCurveTo(x, y - 12, x + 13, y - 6);
    ctx.quadraticCurveTo(x + 4, y + 11, x, y + 11);
    ctx.quadraticCurveTo(x - 4, y + 11, x - 13, y - 6);
    ctx.fillStyle = '#ffb347';
    ctx.fill();
    ellipse(ctx, x - 4, y - 5, 4, 2);
    ctx.fillStyle = 'rgba(255,255,255,0.6)';
    ctx.fill();
    return;
  }
  if (species === 'frog') return;
  const big = species === 'bear' || species === 'panda';
  const w = big ? 9 : 6.5, h = big ? 6.5 : 5;
  ctx.beginPath();
  ctx.moveTo(x - w, y - h * 0.6);
  ctx.quadraticCurveTo(x, y - h * 1.2, x + w, y - h * 0.6);
  ctx.quadraticCurveTo(x + w * 0.4, y + h, x, y + h);
  ctx.quadraticCurveTo(x - w * 0.4, y + h, x - w, y - h * 0.6);
  ctx.fillStyle = big ? '#3a2c30' : species === 'fox' ? '#3a2c30' : '#f58aa6';
  ctx.fill();
  ellipse(ctx, x - w * 0.3, y - h * 0.35, w * 0.3, h * 0.22);
  ctx.fillStyle = 'rgba(255,255,255,0.7)';
  ctx.fill();
}

function drawMarkings(ctx, P) {
  const dark = P.dark;
  const sp = P.species;
  // species markings
  if (sp === 'fox') {
    ctx.fillStyle = P.fur2;
    ctx.beginPath();
    ctx.moveTo(CX - 150, FACE_H);
    ctx.bezierCurveTo(CX - 170, 185, CX - 120, 160, CX - 60, 178);
    ctx.quadraticCurveTo(CX, 150, CX + 60, 178);
    ctx.bezierCurveTo(CX + 120, 160, CX + 170, 185, CX + 150, FACE_H);
    ctx.closePath();
    ctx.fill();
  } else if (sp === 'hamster' || sp === 'bear') {
    ctx.fillStyle = P.fur2;
    ellipse(ctx, CX, 196, sp === 'bear' ? 46 : 70, sp === 'bear' ? 32 : 44);
    ctx.fill();
  } else if (sp === 'panda') {
    ctx.fillStyle = dark;
    ellipse(ctx, CX - EYE_DX - 4, EYE_Y + 6, 34, 42, 0.55);
    ctx.fill();
    ellipse(ctx, CX + EYE_DX + 4, EYE_Y + 6, 34, 42, -0.55);
    ctx.fill();
  } else if (sp === 'penguin') {
    ctx.fillStyle = P.fur2;
    ctx.beginPath();
    // heart-shaped face mask
    ctx.moveTo(CX, 95);
    ctx.bezierCurveTo(CX - 40, 60, CX - 175, 70, CX - 150, 190);
    ctx.bezierCurveTo(CX - 130, 270, CX - 40, 290, CX, 290);
    ctx.bezierCurveTo(CX + 40, 290, CX + 130, 270, CX + 150, 190);
    ctx.bezierCurveTo(CX + 175, 70, CX + 40, 60, CX, 95);
    ctx.fill();
  }
  // coat pattern markings
  const stripe = P.stripe;
  if (P.pattern === 'tabby') {
    strokeStyle(ctx, 9, stripe);
    for (const [dx, len] of [[-26, 34], [0, 44], [26, 34]]) {
      ctx.beginPath();
      ctx.moveTo(CX + dx, 0);
      ctx.lineTo(CX + dx * 0.8, len);
      ctx.stroke();
    }
    strokeStyle(ctx, 7, stripe);
    for (const side of [-1, 1]) {
      for (let i = 0; i < 2; i++) {
        ctx.beginPath();
        ctx.moveTo(CX + side * 220, 150 + i * 22);
        ctx.lineTo(CX + side * 185, 156 + i * 20);
        ctx.stroke();
      }
    }
  } else if (P.pattern === 'calico') {
    ctx.fillStyle = P.patchA;
    ellipse(ctx, CX - 170, 28, 80, 62, 0.4);
    ctx.fill();
    ctx.fillStyle = P.patchB;
    ellipse(ctx, CX + 205, 18, 58, 44, -0.3);
    ctx.fill();
  } else if (P.pattern === 'tuxedo' && sp !== 'penguin' && sp !== 'fox') {
    ctx.fillStyle = P.fur2;
    ctx.beginPath();
    ctx.moveTo(CX, 140);
    ctx.bezierCurveTo(CX - 30, 150, CX - 110, 200, CX - 100, FACE_H);
    ctx.lineTo(CX + 100, FACE_H);
    ctx.bezierCurveTo(CX + 110, 200, CX + 30, 150, CX, 140);
    ctx.fill();
  } else if (P.pattern === 'spots') {
    ctx.fillStyle = stripe;
    ctx.globalAlpha = 0.55;
    for (const [x, y, r] of [[-50, 22, 11], [40, 12, 8], [8, 48, 6], [-175, 110, 12], [185, 95, 9]]) {
      ellipse(ctx, CX + x, y, r, r * 0.9);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

/**
 * Paint a full face.
 * expr: {eyes: style, mouth: style, blush: bool}
 */
export function paintFace(ctx, P, expr) {
  ctx.clearRect(0, 0, FACE_W, FACE_H);
  drawMarkings(ctx, P);
  if (P.species !== 'frog') {
    drawEye(ctx, CX - EYE_DX, EYE_Y, expr.eyes, P.eye_color, false);
    drawEye(ctx, CX + EYE_DX, EYE_Y, expr.eyes, P.eye_color, true);
  }
  const blushY = P.species === 'frog' ? 150 : 184;
  const blushDX = P.species === 'frog' ? 90 : 116;
  if (expr.blush) {
    drawBlush(ctx, CX - blushDX, blushY);
    drawBlush(ctx, CX + blushDX, blushY);
  }
  const noseY = 172;
  drawNose(ctx, P.species, noseY);
  if (P.species === 'penguin') {
    if (expr.mouth === 'open' || expr.mouth === 'o' || expr.mouth === 'wavy') {
      // small open beak hint
      ellipse(ctx, CX, noseY + 10, 5, 3);
      ctx.fillStyle = '#b5552e';
      ctx.fill();
    }
  } else if (P.species === 'frog') {
    // wide happy frog grin
    if (expr.mouth === 'open' || expr.mouth === 'o') {
      drawMouth(ctx, 'open', 150);
    } else if (expr.mouth === 'wavy' || expr.mouth === 'tiny') {
      drawMouth(ctx, expr.mouth, 150);
    } else {
      strokeStyle(ctx, 5.5);
      ctx.beginPath();
      ctx.arc(CX, 122, 38, 0.25 * Math.PI, 0.75 * Math.PI);
      ctx.stroke();
      if (expr.mouth === 'tongue') {
        ctx.beginPath();
        ctx.ellipse(CX + 6, 150, 7, 9, 0, 0, Math.PI);
        ctx.fillStyle = '#ff8fa6';
        ctx.fill();
      }
    }
    // nostrils
    ctx.fillStyle = shade(P.fur, -0.35);
    ellipse(ctx, CX - 9, 100, 3, 2.2);
    ctx.fill();
    ellipse(ctx, CX + 9, 100, 3, 2.2);
    ctx.fill();
  } else {
    drawMouth(ctx, expr.mouth, MOUTH_Y);
  }
}

export const BUMP_W = 128, BUMP_H = 128;
/** Frog eye on top of its eye bump. */
export function paintBump(ctx, P, expr, flip) {
  ctx.clearRect(0, 0, BUMP_W, BUMP_H);
  drawEye(ctx, BUMP_W / 2 + (flip ? -6 : 6), BUMP_H / 2 + 6, expr.eyes, P.eye_color, flip, 1.25);
}

/** A face that repaints its CanvasTexture only when the expression changes. */
export class Face {
  constructor(P) {
    this.P = P;
    this.canvas = document.createElement('canvas');
    this.canvas.width = FACE_W;
    this.canvas.height = FACE_H;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.tex.anisotropy = 4;
    if (P.species === 'frog') {
      this.bumps = [0, 1].map(() => {
        const c = document.createElement('canvas');
        c.width = BUMP_W;
        c.height = BUMP_H;
        const t = new THREE.CanvasTexture(c);
        t.colorSpace = THREE.SRGBColorSpace;
        return { c, ctx: c.getContext('2d'), tex: t };
      });
    }
    this.key = '';
    this.set({ eyes: P.eyes, mouth: P.mouth, blush: P.blush });
  }
  set(expr) {
    const key = expr.eyes + '|' + expr.mouth + '|' + expr.blush;
    if (key === this.key) return;
    this.key = key;
    paintFace(this.ctx, this.P, expr);
    this.tex.needsUpdate = true;
    if (this.bumps) {
      this.bumps.forEach((b, i) => {
        paintBump(b.ctx, this.P, expr, i === 1);
        b.tex.needsUpdate = true;
      });
    }
  }
  dispose() {
    this.tex.dispose();
    this.bumps?.forEach((b) => b.tex.dispose());
  }
}
