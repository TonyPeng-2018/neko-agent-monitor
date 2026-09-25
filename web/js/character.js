// Procedural kawaii chibi animals, built from a persona (see DESIGN.md "Persona schema").
//
// Proportions (at scale 1, feet at y=0): head centre y≈0.62, radius ≈0.34 (≈2/3 of the height),
// a tiny round body, nub legs and stubby arms. Toon shading (3-step gradient) + soft
// inverted-hull outlines via three/addons OutlineEffect (per-material outlineParameters).
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { Face, FACE_PHI_LEN, FACE_THETA_START, FACE_THETA_LEN } from './face.js';
import { shade, mixHex, lum, SOURCE_COLOR } from './util.js';

// ---------------------------------------------------------------- shared resources
function makeGradient() {
  const data = new Uint8Array([150, 205, 255]);
  const t = new THREE.DataTexture(data, 3, 1, THREE.RedFormat);
  t.minFilter = t.magFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}
export const GRADIENT = makeGradient();

const geoCache = new Map();
function geo(key, make) {
  let g = geoCache.get(key);
  if (!g) geoCache.set(key, (g = make()));
  return g;
}
const SPHERE = () => geo('sphere', () => new THREE.SphereGeometry(1, 40, 28));
const SPHERE_LO = () => geo('sphereLo', () => new THREE.SphereGeometry(1, 18, 12));
const HEMI = () => geo('hemi', () => new THREE.SphereGeometry(1, 36, 14, 0, Math.PI * 2, 0, Math.PI / 2));
const LOWER_HEMI = () => geo('lhemi', () => new THREE.SphereGeometry(1, 36, 14, 0, Math.PI * 2, Math.PI * 0.48, Math.PI * 0.52));
const CONE = () => geo('cone', () => new THREE.ConeGeometry(1, 1, 24, 1));
const CYL = () => geo('cyl', () => new THREE.CylinderGeometry(1, 1, 1, 28));
const capsule = (r, len) => geo(`cap${r}|${len}`, () => new THREE.CapsuleGeometry(r, len, 8, 16));
const torus = (r, t, arc = Math.PI * 2) => geo(`tor${r}|${t}|${arc}`, () => new THREE.TorusGeometry(r, t, 12, 40, arc));
const rbox = (w, h, d, r = 0.2) =>
  geo(`rb${w}|${h}|${d}|${r}`, () => new RoundedBoxGeometry(w, h, d, 3, Math.min(w, h, d) * r));
const FACE_GEO = () =>
  geo('face', () => new THREE.SphereGeometry(1.012, 56, 30, Math.PI / 2 - FACE_PHI_LEN / 2, FACE_PHI_LEN, FACE_THETA_START, FACE_THETA_LEN));
const BUMP_GEO = () => geo('bump', () => new THREE.SphereGeometry(1.02, 28, 20, Math.PI / 2 - 0.85, 1.7, 0.72, 1.6));

const matCache = new Map();
const _oc = new THREE.Color();
function outlineOf(hex) {
  // soft outline: a deep, slightly saturated version of the fill colour (never pure black)
  const l = lum(hex);
  const o = shade(hex, -Math.min(0.5, l * 0.55), 0.12);
  return _oc.set(o).toArray();
}
/** Toon material, cached by colour. opts: {outline=true, thick, side, transparent, opacity, emissive} */
export function toon(hex, opts = {}) {
  const key = hex + JSON.stringify(opts);
  let m = matCache.get(key);
  if (m) return m;
  m = new THREE.MeshToonMaterial({
    color: hex,
    gradientMap: GRADIENT,
    side: opts.side ?? THREE.FrontSide,
    transparent: !!opts.transparent,
    opacity: opts.opacity ?? 1,
  });
  if (opts.emissive) {
    m.emissive.set(opts.emissive);
    m.emissiveIntensity = opts.emissiveIntensity ?? 0.5;
  }
  m.userData.outlineParameters = {
    thickness: opts.thick ?? 0.0036,
    color: outlineOf(hex),
    alpha: 1,
    visible: opts.outline !== false,
  };
  matCache.set(key, m);
  return m;
}
function basic(hex, opts = {}) {
  const key = 'b' + hex + JSON.stringify(opts);
  let m = matCache.get(key);
  if (m) return m;
  m = new THREE.MeshBasicMaterial({ color: hex, transparent: !!opts.transparent, opacity: opts.opacity ?? 1, side: opts.side ?? THREE.FrontSide });
  m.userData.outlineParameters = { visible: opts.outline === true, thickness: 0.003, color: outlineOf(hex) };
  matCache.set(key, m);
  return m;
}

function mesh(g, m, pos, scale, rot) {
  const o = new THREE.Mesh(g, m);
  if (pos) o.position.set(pos[0], pos[1], pos[2]);
  if (scale !== undefined) {
    if (typeof scale === 'number') o.scale.setScalar(scale);
    else o.scale.set(scale[0], scale[1], scale[2]);
  }
  if (rot) o.rotation.set(rot[0], rot[1], rot[2]);
  return o;
}
function group(pos, rot) {
  const g = new THREE.Group();
  if (pos) g.position.set(pos[0], pos[1], pos[2]);
  if (rot) g.rotation.set(rot[0], rot[1], rot[2]);
  return g;
}

let _shadowTex = null;
export function shadowTexture() {
  if (_shadowTex) return _shadowTex;
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(64, 64, 4, 64, 64, 62);
  g.addColorStop(0, 'rgba(120,80,110,0.42)');
  g.addColorStop(0.55, 'rgba(120,80,110,0.2)');
  g.addColorStop(1, 'rgba(120,80,110,0)');
  x.fillStyle = g;
  x.fillRect(0, 0, 128, 128);
  _shadowTex = new THREE.CanvasTexture(c);
  _shadowTex.colorSpace = THREE.SRGBColorSpace;
  return _shadowTex;
}

// ---------------------------------------------------------------- palette
function palette(P) {
  const C = {
    fur: P.fur,
    fur2: P.fur2,
    accent: P.accent,
    inner: '#ffb6c8',
    stripe: shade(P.fur, -0.2, 0.08),
    dark: shade(P.fur, -0.52, -0.25),
    patchA: '#f4a863',
    patchB: '#b39488',
    paw: P.fur,
  };
  if (P.species === 'panda') {
    C.fur = mixHex(P.fur, '#ffffff', 0.72);
    C.fur2 = '#ffffff';
    C.dark = shade(P.fur, -0.6, -0.3);
    C.paw = C.dark;
  } else if (P.species === 'penguin') {
    C.fur = shade(P.fur, -0.12, 0.05);
    C.fur2 = '#fffaf4';
    C.paw = C.fur;
  } else if (P.species === 'fox') {
    C.fur2 = mixHex(P.fur2, '#ffffff', 0.6);
  } else if (P.species === 'frog') {
    C.inner = shade(P.fur, 0.08);
  }
  if (lum(C.fur2) < lum(C.fur) + 0.04) C.fur2 = mixHex(C.fur, '#ffffff', 0.65);
  if (P.pattern === 'socks' || P.pattern === 'tuxedo') C.paw = C.fur2;
  if (P.pattern === 'calico') C.patchA = mixHex('#f4a863', P.fur, 0.2);
  return C;
}

function bodyTexture(P, C) {
  const W = 256, H = 128;
  const c = document.createElement('canvas');
  c.width = W;
  c.height = H;
  const x = c.getContext('2d');
  x.fillStyle = C.fur;
  x.fillRect(0, 0, W, H);
  // belly (front is u=0.25)
  const bellyW = P.species === 'penguin' ? 58 : P.species === 'hamster' ? 50 : 38;
  const bellyH = P.species === 'penguin' ? 54 : 40;
  x.fillStyle = C.fur2;
  x.beginPath();
  x.ellipse(W * 0.25, H * 0.66, bellyW, bellyH, 0, 0, Math.PI * 2);
  x.fill();
  if (P.pattern === 'tabby') {
    x.strokeStyle = C.stripe;
    x.lineCap = 'round';
    x.lineWidth = 7;
    for (let i = 0; i < 6; i++) {
      const u = W * (0.5 + i * 0.09);
      x.beginPath();
      x.moveTo(u, 4);
      x.quadraticCurveTo(u + 8, H * 0.3, u - 2, H * 0.55);
      x.stroke();
    }
  } else if (P.pattern === 'calico') {
    x.fillStyle = C.patchA;
    x.beginPath(); x.ellipse(W * 0.62, H * 0.3, 38, 28, 0.3, 0, Math.PI * 2); x.fill();
    x.beginPath(); x.ellipse(W * 0.02, H * 0.4, 30, 26, 0, 0, Math.PI * 2); x.fill();
    x.beginPath(); x.ellipse(W * 1.0, H * 0.4, 30, 26, 0, 0, Math.PI * 2); x.fill();
    x.fillStyle = C.patchB;
    x.beginPath(); x.ellipse(W * 0.82, H * 0.45, 26, 20, -0.4, 0, Math.PI * 2); x.fill();
  } else if (P.pattern === 'spots') {
    x.fillStyle = C.stripe;
    const pts = [[0.55, 0.3, 9], [0.68, 0.55, 7], [0.8, 0.25, 10], [0.92, 0.6, 6], [0.05, 0.35, 7], [0.42, 0.5, 6]];
    for (const [u, v, r] of pts) {
      x.beginPath(); x.ellipse(W * u, H * v, r * 1.4, r, 0, 0, Math.PI * 2); x.fill();
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// ---------------------------------------------------------------- species parts
const HEAD_R = 0.34, HEAD_SX = 1.1, HEAD_SY = 0.93;
const HEAD_W = HEAD_R * HEAD_SX, HEAD_H = HEAD_R * HEAD_SY;
/** Point on the head surface at angle `a` from vertical (side ±1), pushed in by `inset`. */
function headPoint(a, side, inset = 0.9, z = 0) {
  return [side * HEAD_W * Math.sin(a) * inset, HEAD_H * Math.cos(a) * inset, z];
}

function buildEars(P, C, head) {
  const s = P.ear_size;
  const ears = [];
  const furM = toon(C.fur);
  const innerM = toon(C.inner, { outline: false });
  const sp = P.species;
  for (const side of [-1, 1]) {
    let piv;
    if (sp === 'cat' || sp === 'fox') {
      const fox = sp === 'fox';
      const a = fox ? 0.62 : 0.66;
      piv = group(headPoint(a, side, 0.86), [0, 0, -side * (fox ? 0.42 : 0.5)]);
      const h = (fox ? 0.25 : 0.19) * s, r = (fox ? 0.12 : 0.11) * s;
      const earM = P.pattern === 'calico' && side < 0 ? toon(C.patchA) : furM;
      piv.add(mesh(CONE(), earM, [0, h * 0.42, 0], [r, h, r * 0.62]));
      piv.add(mesh(CONE(), fox ? toon(C.fur2, { outline: false }) : innerM, [0, h * 0.36, r * 0.3], [r * 0.62, h * 0.7, r * 0.3]));
      if (fox) piv.add(mesh(CONE(), toon(C.dark), [0, h * 0.78, 0], [r * 0.34, h * 0.25, r * 0.22]));
    } else if (sp === 'bunny') {
      const a = 0.32;
      piv = group(headPoint(a, side, 0.86), [side > 0 ? 0.25 : -0.05, 0, -side * (side > 0 ? 0.45 : 0.14)]);
      const len = 0.26 * s;
      piv.add(mesh(capsule(0.075, len), furM, [0, len * 0.6, 0], [1, 1, 0.62]));
      piv.add(mesh(capsule(0.045, len * 0.85), innerM, [0, len * 0.6, 0.03], [1, 1, 0.4]));
    } else if (sp === 'bear' || sp === 'panda' || sp === 'hamster') {
      const ham = sp === 'hamster';
      const a = ham ? 0.78 : 0.72;
      piv = group(headPoint(a, side, 0.9), [0, 0, -side * 0.5]);
      const r = (ham ? 0.08 : 0.1) * s;
      const earM = sp === 'panda' ? toon(C.dark) : furM;
      piv.add(mesh(SPHERE(), earM, [0, r * 0.35, 0], [r, r, r * 0.6]));
      piv.add(mesh(SPHERE(), sp === 'panda' ? toon(shade(C.dark, 0.12), { outline: false }) : innerM, [0, r * 0.35, r * 0.3], [r * 0.6, r * 0.6, r * 0.35]));
    } else {
      continue; // frog & penguin have no ears
    }
    head.add(piv);
    ears.push(piv);
  }
  return ears;
}

function buildFrogBumps(P, C, head, face) {
  const out = [];
  [-1, 1].forEach((side, i) => {
    const g = group([side * 0.155, 0.22, 0.13]);
    const r = 0.12;
    g.add(mesh(SPHERE(), toon(C.fur), [0, 0, 0], r));
    const dm = new THREE.MeshToonMaterial({ map: face.bumps[i].tex, gradientMap: GRADIENT, transparent: true, depthWrite: false });
    dm.userData.outlineParameters = { visible: false };
    dm.polygonOffset = true;
    dm.polygonOffsetFactor = -2;
    g.add(mesh(BUMP_GEO(), dm, [0, 0, 0], r));
    head.add(g);
    out.push(g);
  });
  return out;
}

function buildTail(P, C) {
  const t = group([0, 0.13, -0.16]);
  const s = P.tail;
  const furM = toon(C.fur);
  const sp = P.species;
  if (sp === 'cat') {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0.02, -0.1), new THREE.Vector3(0, 0.1, -0.17),
      new THREE.Vector3(0, 0.22, -0.17), new THREE.Vector3(0.03, 0.3, -0.1),
    ]);
    const tube = geo('catTail', () => new THREE.TubeGeometry(curve, 24, 0.036, 10, false));
    const tm = P.pattern === 'tabby' || P.pattern === 'calico' ? toon(P.pattern === 'tabby' ? C.stripe : C.patchA) : furM;
    const inner = group(null);
    inner.scale.setScalar(s);
    inner.add(mesh(tube, furM));
    inner.add(mesh(SPHERE_LO(), tm, [0.03, 0.3, -0.1], 0.038));
    t.add(inner);
  } else if (sp === 'fox') {
    const dir = new THREE.Vector3(0, 0.72, -0.7).normalize();
    const inner = group(null, [0, 0, 0]);
    inner.scale.setScalar(s);
    inner.add(mesh(SPHERE(), furM, dir.clone().multiplyScalar(0.16).toArray(), [0.1, 0.19, 0.1], [-0.8, 0, 0]));
    inner.add(mesh(SPHERE(), toon(C.fur2), dir.clone().multiplyScalar(0.33).toArray(), [0.075, 0.085, 0.075], [-0.8, 0, 0]));
    t.add(inner);
  } else if (sp === 'bunny') {
    t.add(mesh(SPHERE(), toon(C.fur2), [0, -0.02, 0.0], 0.075 * s));
  } else if (sp === 'bear' || sp === 'panda') {
    t.add(mesh(SPHERE(), toon(sp === 'panda' ? C.dark : C.fur), [0, -0.03, 0.0], 0.05 * s));
  } else if (sp === 'hamster') {
    t.add(mesh(SPHERE(), furM, [0, -0.06, 0.02], 0.035 * s));
  } else if (sp === 'penguin') {
    t.add(mesh(CONE(), furM, [0, -0.08, -0.02], [0.06, 0.08, 0.03], [-2.2, 0, 0]));
  }
  return t;
}

// ---------------------------------------------------------------- hats
function buildHat(P, C, kind) {
  const h = group(null);
  const acc = toon(P.accent);
  switch (kind) {
    case 'detective': {
      const m = toon('#c9a77c');
      h.add(mesh(HEMI(), m, [0, 0.2, 0], [0.28, 0.2, 0.26]));
      h.add(mesh(SPHERE(), m, [0, 0.215, 0.22], [0.1, 0.02, 0.08], [0.35, 0, 0]));
      h.add(mesh(SPHERE(), m, [0, 0.215, -0.22], [0.1, 0.02, 0.08], [-0.35, 0, 0]));
      h.add(mesh(torus(0.34, 0.02), toon('#8a6a4a'), [0, 0.215, 0], [0.8, 0.74, 1], [Math.PI / 2, 0, 0]));
      h.add(mesh(SPHERE_LO(), toon('#8a6a4a'), [0, 0.38, 0], 0.03));
      break;
    }
    case 'hardhat': {
      const m = toon('#ffd45e');
      h.add(mesh(HEMI(), m, [0, 0.15, 0], [0.32, 0.24, 0.3]));
      h.add(mesh(CYL(), m, [0, 0.16, 0.04], [0.36, 0.02, 0.36]));
      h.add(mesh(rbox(0.05, 0.045, 0.4, 0.4), toon('#ffe48f'), [0, 0.38, 0], [1, 1, 1], [0, 0, 0]));
      break;
    }
    case 'beret': {
      const g = group([0.04, 0.29, -0.01], [0, 0, -0.28]);
      g.add(mesh(SPHERE(), acc, [0, 0, 0], [0.33, 0.1, 0.31]));
      g.add(mesh(CYL(), acc, [0, 0.1, 0], [0.018, 0.06, 0.018]));
      h.add(g);
      break;
    }
    case 'goggles': {
      h.add(mesh(torus(1, 0.035), toon(shade(P.accent, -0.25)), [0, 0.1, 0], [HEAD_W * 0.99, HEAD_R * 0.98, 1], [Math.PI / 2, 0, 0]));
      for (const side of [-1, 1]) {
        const g = group([side * 0.11, 0.2, 0.25], [-0.55, side * 0.25, 0]);
        g.add(mesh(torus(0.075, 0.024), toon('#b8bcc8'), [0, 0, 0], 1));
        g.add(mesh(CYL(), toon('#a8e4ff', { outline: false, emissive: '#6ac8ff', emissiveIntensity: 0.25 }), [0, 0, 0], [0.072, 0.02, 0.072], [Math.PI / 2, 0, 0]));
        h.add(g);
      }
      break;
    }
    case 'crown': {
      const g = group([0, 0.3, 0], [0, 0, 0.12]);
      g.scale.setScalar(0.8);
      const gold = toon('#ffd35c');
      g.add(mesh(geo('crownBand', () => new THREE.CylinderGeometry(0.15, 0.14, 0.08, 28, 1, true)), toon('#ffd35c', { side: THREE.DoubleSide }), [0, 0.02, 0]));
      for (let i = 0; i < 5; i++) {
        const a = (i / 5) * Math.PI * 2;
        g.add(mesh(CONE(), gold, [Math.sin(a) * 0.14, 0.1, Math.cos(a) * 0.14], [0.035, 0.08, 0.035]));
        g.add(mesh(SPHERE_LO(), gold, [Math.sin(a) * 0.14, 0.145, Math.cos(a) * 0.14], 0.016));
      }
      g.add(mesh(SPHERE_LO(), toon('#ff7fa8'), [0, 0.02, 0.15], 0.025));
      g.add(mesh(SPHERE_LO(), toon('#7fc8ff'), [0.1, 0.02, 0.11], 0.018));
      g.add(mesh(SPHERE_LO(), toon('#7fc8ff'), [-0.1, 0.02, 0.11], 0.018));
      h.add(g);
      break;
    }
    case 'headset': {
      const dark = toon('#5d5a6e');
      h.add(mesh(torus(0.39, 0.024, Math.PI), toon(P.accent), [0, 0.0, 0], [1, 0.92, 1]));
      for (const side of [-1, 1]) {
        h.add(mesh(CYL(), dark, [side * 0.37, 0.0, 0], [0.085, 0.07, 0.085], [0, 0, Math.PI / 2]));
        h.add(mesh(CYL(), toon(P.accent), [side * 0.41, 0.0, 0], [0.06, 0.02, 0.06], [0, 0, Math.PI / 2]));
      }
      const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(-0.37, -0.02, 0.03), new THREE.Vector3(-0.33, -0.2, 0.25), new THREE.Vector3(-0.12, -0.2, 0.33));
      h.add(mesh(geo('mic', () => new THREE.TubeGeometry(curve, 12, 0.012, 6, false)), dark));
      h.add(mesh(SPHERE_LO(), dark, [-0.12, -0.2, 0.33], 0.03));
      break;
    }
    case 'visor': {
      h.add(mesh(torus(1, 0.03), acc, [0, 0.13, 0], [HEAD_W * 0.97, HEAD_R * 0.96, 1], [Math.PI / 2, 0, 0]));
      const brim = geo('visorBrim', () => new THREE.CylinderGeometry(0.22, 0.22, 0.02, 32, 1, false, -Math.PI / 2, Math.PI));
      h.add(mesh(brim, acc, [0, 0.15, 0.27], [1.1, 1, 0.9], [0.2, 0, 0]));
      break;
    }
    case 'beanie': {
      h.add(mesh(HEMI(), acc, [0, 0.12, 0], [0.345, 0.26, 0.315]));
      h.add(mesh(torus(1, 0.12), toon(shade(P.accent, 0.08)), [0, 0.13, 0], [0.345, 0.315, 0.3], [Math.PI / 2, 0, 0]));
      h.add(mesh(SPHERE(), toon(mixHex(P.accent, '#ffffff', 0.55)), [0, 0.4, 0], 0.07));
      break;
    }
    case 'flower': {
      const g = group([0.2, 0.22, 0.14], [0.3, 0.5, -0.4]);
      const pet = toon(mixHex(P.accent, '#ff9ec4', 0.5));
      for (let i = 0; i < 5; i++) {
        const a = (i / 5) * Math.PI * 2;
        g.add(mesh(SPHERE(), pet, [Math.cos(a) * 0.055, Math.sin(a) * 0.055, 0], [0.045, 0.045, 0.02]));
      }
      g.add(mesh(SPHERE_LO(), toon('#ffe066'), [0, 0, 0.012], [0.032, 0.032, 0.02]));
      h.add(g);
      break;
    }
    case 'bow': {
      const g = group([0.18, 0.25, 0.05], [0, 0, -0.5]);
      const m = toon(mixHex(P.accent, '#ff8fb8', 0.4));
      g.add(mesh(CONE(), m, [-0.06, 0, 0], [0.06, 0.12, 0.035], [0, 0, Math.PI / 2]));
      g.add(mesh(CONE(), m, [0.06, 0, 0], [0.06, 0.12, 0.035], [0, 0, -Math.PI / 2]));
      g.add(mesh(SPHERE_LO(), m, [0, 0, 0], [0.032, 0.032, 0.03]));
      h.add(g);
      break;
    }
    case 'wizard': {
      const m = toon(mixHex(P.accent, '#7b6bd6', 0.6));
      h.add(mesh(CYL(), m, [0, 0.24, 0], [0.34, 0.02, 0.34]));
      const g = group([0, 0.24, 0], [0, 0, 0.18]);
      g.add(mesh(CONE(), m, [0, 0.22, 0], [0.24, 0.46, 0.24]));
      g.add(mesh(CONE(), m, [-0.03, 0.47, 0], [0.06, 0.1, 0.06], [0, 0, 0.6]));
      g.add(mesh(starGeo(), toon('#ffe066'), [0.06, 0.18, 0.2], 0.05, [0, 0.4, 0]));
      h.add(g);
      break;
    }
    case 'chef': {
      const w = toon('#ffffff');
      h.add(mesh(CYL(), w, [0, 0.32, 0], [0.2, 0.14, 0.2]));
      for (const [x, z] of [[-0.09, 0], [0.09, 0], [0, 0.08], [0, -0.08], [0, 0]]) {
        h.add(mesh(SPHERE(), w, [x, 0.43, z], 0.12));
      }
      break;
    }
    default:
      break;
  }
  return h;
}

export function starGeo() {
  return geo('star', () => {
    const s = new THREE.Shape();
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2 + Math.PI / 2;
      const r = i % 2 === 0 ? 1 : 0.5;
      const x = Math.cos(a) * r, y = Math.sin(a) * r;
      i === 0 ? s.moveTo(x, y) : s.lineTo(x, y);
    }
    const g = new THREE.ExtrudeGeometry(s, { depth: 0.35, bevelEnabled: true, bevelSize: 0.15, bevelThickness: 0.15, bevelSegments: 2 });
    g.center();
    return g;
  });
}

// ---------------------------------------------------------------- props (in paw space, built upright)
function buildProp(P, kind) {
  const p = group(null);
  const wood = toon('#c79a6b');
  const metal = toon('#b9bfcc');
  switch (kind) {
    case 'magnifier':
      p.add(mesh(CYL(), wood, [0, 0.03, 0], [0.02, 0.12, 0.02]));
      p.add(mesh(torus(0.075, 0.018), toon('#ffcf5c'), [0, 0.16, 0]));
      p.add(mesh(CYL(), toon('#c8f0ff', { transparent: true, opacity: 0.55, outline: false }), [0, 0.16, 0], [0.072, 0.006, 0.072], [Math.PI / 2, 0, 0]));
      break;
    case 'laptop': {
      const g = group([-0.14, 0.02, 0.1]);
      const shell = toon('#dfe3ee');
      g.add(mesh(rbox(0.26, 0.02, 0.17, 0.3), shell, [0, 0, 0]));
      const lid = group([0, 0.01, -0.08], [-0.28, 0, 0]);
      lid.add(mesh(rbox(0.26, 0.17, 0.018, 0.3), shell, [0, 0.085, 0]));
      lid.add(mesh(geo('plane', () => new THREE.PlaneGeometry(1, 1)), basic('#bfe9ff'), [0, 0.085, 0.011], [0.22, 0.13, 1]));
      lid.add(mesh(SPHERE_LO(), basic('#ff9ec4'), [0, 0.09, 0.012], [0.02, 0.02, 0.002]));
      g.add(lid);
      p.add(g);
      p.userData.twoHanded = true;
      break;
    }
    case 'brush':
      p.add(mesh(CYL(), wood, [0, 0.04, 0], [0.016, 0.18, 0.016]));
      p.add(mesh(CYL(), metal, [0, 0.14, 0], [0.02, 0.03, 0.02]));
      p.add(mesh(CONE(), toon(P.accent), [0, 0.19, 0], [0.024, 0.08, 0.024]));
      break;
    case 'bugnet': {
      p.add(mesh(CYL(), wood, [0, 0.14, 0], [0.012, 0.4, 0.012]));
      const g = group([0, 0.38, 0], [0.4, 0, 0]);
      g.add(mesh(torus(0.09, 0.012), toon('#8ed6a0'), [0, 0, 0], 1, [Math.PI / 2, 0, 0]));
      g.add(mesh(geo('net', () => new THREE.ConeGeometry(0.09, 0.14, 16, 1, true)), toon('#ffffff', { transparent: true, opacity: 0.65, side: THREE.DoubleSide, outline: false }), [0, -0.07, 0], 1, [Math.PI, 0, 0]));
      p.add(g);
      break;
    }
    case 'quill':
      p.add(mesh(CYL(), toon('#8a6a4a'), [0, 0.04, 0], [0.006, 0.12, 0.006]));
      p.add(mesh(SPHERE(), toon(mixHex(P.accent, '#ffffff', 0.35)), [0.01, 0.15, 0], [0.035, 0.12, 0.012], [0, 0, -0.15]));
      break;
    case 'chart': {
      p.add(mesh(CYL(), wood, [0, 0.05, 0], [0.012, 0.14, 0.012]));
      const g = group([0, 0.18, 0]);
      g.add(mesh(rbox(0.2, 0.15, 0.014, 0.3), toon('#ffffff'), [0, 0, 0]));
      const cols = ['#ff9ec4', '#8fd0ff', '#a7e3a0'];
      [0.05, 0.08, 0.11].forEach((hh, i) => {
        g.add(mesh(rbox(0.035, hh, 0.012, 0.3), toon(cols[i], { outline: false }), [-0.055 + i * 0.055, -0.06 + hh / 2, 0.01]));
      });
      p.add(g);
      break;
    }
    case 'wrench':
      p.add(mesh(rbox(0.035, 0.17, 0.018, 0.4), metal, [0, 0.06, 0]));
      p.add(mesh(torus(0.04, 0.017, Math.PI * 1.5), metal, [0, 0.17, 0], 1, [0, 0, Math.PI * 1.25]));
      break;
    case 'clipboard': {
      const g = group([0, 0.12, 0]);
      g.add(mesh(rbox(0.16, 0.21, 0.014, 0.3), wood, [0, 0, 0]));
      g.add(mesh(rbox(0.13, 0.16, 0.004, 0.3), toon('#ffffff', { outline: false }), [0, -0.01, 0.009]));
      g.add(mesh(rbox(0.06, 0.03, 0.02, 0.3), metal, [0, 0.1, 0.01]));
      for (let i = 0; i < 3; i++) g.add(mesh(rbox(0.08, 0.01, 0.004, 0.3), toon(P.accent, { outline: false }), [0, 0.03 - i * 0.04, 0.012]));
      p.add(g);
      break;
    }
    default:
      break;
  }
  return p;
}

// ---------------------------------------------------------------- outfits (torso space)
function buildOutfit(P, C, kind, torso, parts) {
  const acc = toon(P.accent);
  const accL = toon(mixHex(P.accent, '#ffffff', 0.45));
  switch (kind) {
    case 'scarf': {
      torso.add(mesh(torus(0.16, 0.05), acc, [0, 0.31, 0.01], [1, 1, 0.9], [Math.PI / 2 - 0.1, 0, 0]));
      torso.add(mesh(rbox(0.07, 0.15, 0.035, 0.4), acc, [0.08, 0.22, 0.17], 1, [0.15, 0, 0.18]));
      torso.add(mesh(rbox(0.072, 0.02, 0.037, 0.3), accL, [0.093, 0.17, 0.178], 1, [0.15, 0, 0.18]));
      break;
    }
    case 'hoodie':
    case 'sweater': {
      const b = parts.bodyMesh;
      torso.add(mesh(SPHERE(), acc, b.position.toArray(), b.scale.clone().multiplyScalar(1.045).toArray()));
      if (kind === 'hoodie') {
        torso.add(mesh(torus(0.15, 0.06), acc, [0, 0.35, -0.1], [1, 1, 1], [1.25, 0, 0]));
        torso.add(mesh(rbox(0.16, 0.07, 0.03, 0.4), toon(shade(P.accent, -0.06)), [0, 0.16, 0.19], 1, [-0.3, 0, 0]));
      } else {
        torso.add(mesh(torus(1, 0.05), accL, [0, 0.2, 0], [0.215 * P.chubby, 0.215 * P.chubby, 1], [Math.PI / 2, 0, 0]));
      }
      parts.sleeve = acc;
      break;
    }
    case 'overalls': {
      const b = parts.bodyMesh;
      torso.add(mesh(LOWER_HEMI(), acc, b.position.toArray(), b.scale.clone().multiplyScalar(1.05).toArray()));
      torso.add(mesh(rbox(0.15, 0.1, 0.03, 0.3), acc, [0, 0.25, 0.17], 1, [-0.35, 0, 0]));
      for (const side of [-1, 1]) {
        torso.add(mesh(rbox(0.03, 0.14, 0.02, 0.3), acc, [side * 0.07, 0.29, 0.13], 1, [-0.55, 0, side * -0.1]));
        torso.add(mesh(SPHERE_LO(), toon('#ffd35c'), [side * 0.055, 0.285, 0.19], 0.014));
      }
      break;
    }
    case 'cape': {
      const cg = geo('cape', () => new THREE.CylinderGeometry(0.17, 0.3, 0.34, 24, 1, true, Math.PI * 0.65, Math.PI * 0.7));
      const cape = group([0, 0.3, 0]);
      cape.add(mesh(cg, toon(P.accent, { side: THREE.DoubleSide }), [0, -0.15, -0.02]));
      torso.add(cape);
      torso.add(mesh(SPHERE_LO(), toon('#ffd35c'), [0, 0.29, 0.18], 0.025));
      parts.cape = cape;
      break;
    }
    case 'bowtie': {
      const g = group([0, 0.285, 0.185], [-0.2, 0, 0]);
      g.add(mesh(CONE(), acc, [-0.04, 0, 0], [0.04, 0.08, 0.025], [0, 0, -Math.PI / 2]));
      g.add(mesh(CONE(), acc, [0.04, 0, 0], [0.04, 0.08, 0.025], [0, 0, Math.PI / 2]));
      g.add(mesh(SPHERE_LO(), toon(shade(P.accent, -0.1)), [0, 0, 0.005], 0.022));
      torso.add(g);
      parts.noTag = true;
      break;
    }
    case 'apron': {
      const ag = geo('apron', () => new THREE.SphereGeometry(1, 24, 16, Math.PI / 2 - 0.8, 1.6, Math.PI * 0.35, Math.PI * 0.5));
      const b = parts.bodyMesh;
      torso.add(mesh(ag, toon(mixHex(P.accent, '#ffffff', 0.6)), b.position.toArray(), b.scale.clone().multiplyScalar(1.05).toArray()));
      torso.add(mesh(rbox(0.07, 0.04, 0.02, 0.3), acc, [0, 0.14, 0.205], 1, [0.1, 0, 0]));
      break;
    }
    default:
      break;
  }
}

// ---------------------------------------------------------------- main builder
/**
 * Build a chibi from a normalised persona.
 * Returns handles the animator drives; everything hangs off `root` (placed on the floor).
 */
export function buildCharacter(P, { source = 'claude' } = {}) {
  const C = palette(P);
  const root = group(null);
  const scaler = group(null); // size (context %) + spawn pop
  root.add(scaler);
  const body = group(null); // bounce + squash & stretch
  scaler.add(body);
  const facing = group(null);
  body.add(facing);

  // blob shadow on the floor (not squashed)
  const shadow = mesh(geo('plane', () => new THREE.PlaneGeometry(1, 1)), new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false }), [0, 0.004, 0], [0.75, 0.75, 1], [-Math.PI / 2, 0, 0]);
  shadow.material.userData.outlineParameters = { visible: false };
  shadow.renderOrder = -1;
  scaler.add(shadow);

  const torso = group(null);
  facing.add(torso);

  const bodyTex = bodyTexture(P, C);
  const bodyMat = new THREE.MeshToonMaterial({ color: '#ffffff', map: bodyTex, gradientMap: GRADIENT });
  bodyMat.userData.outlineParameters = { thickness: 0.0036, color: outlineOf(C.fur), visible: true };
  const ch = P.chubby * (P.species === 'hamster' ? 1.08 : 1);
  const bodyMesh = mesh(SPHERE(), bodyMat, [0, 0.215, 0], [0.2 * ch, 0.2 * (P.species === 'penguin' ? 1.08 : 1), 0.18 * ch], [0, 0, 0]);
  torso.add(bodyMesh);
  const parts = { bodyMesh };

  // legs (hip pivots → nub feet)
  const pawM = toon(C.paw);
  const legs = [-1, 1].map((side) => {
    const piv = group([side * 0.085 * ch, 0.1, 0.01]);
    const footM = P.species === 'penguin' ? toon('#ffb347') : P.species === 'panda' ? toon(C.dark) : pawM;
    piv.add(mesh(SPHERE(), footM, [0, -0.05, 0.02], P.species === 'penguin' ? [0.07, 0.035, 0.1] : [0.07, 0.062, 0.085]));
    facing.add(piv);
    return piv;
  });

  // outfit first so sleeves can recolour arms
  buildOutfit(P, C, P.outfit, torso, parts);

  // arms
  const armM = parts.sleeve || (P.species === 'panda' ? toon(C.dark) : toon(C.fur));
  const handM = P.species === 'panda' ? toon(C.dark) : pawM;
  const arms = [-1, 1].map((side) => {
    const piv = group([side * 0.155 * ch, 0.29, 0.03], [0, 0, side * 0.55]);
    if (P.species === 'penguin') {
      piv.add(mesh(SPHERE(), toon(C.fur), [0, -0.09, 0], [0.035, 0.12, 0.075]));
    } else {
      piv.add(mesh(capsule(0.05, 0.06), armM, [0, -0.06, 0]));
      piv.add(mesh(SPHERE(), handM, [0, -0.11, 0.005], 0.055));
    }
    const anchor = group([0, -0.13, 0.02]);
    piv.add(anchor);
    piv.userData.anchor = anchor;
    torso.add(piv);
    return piv;
  });

  // collar + source tag (Claude warm orange, Codex mint, SDK lilac)
  const tagColor = SOURCE_COLOR[source] || '#f0a36b';
  torso.add(mesh(torus(0.165 * ch, 0.017), toon(shade(P.accent, -0.1)), [0, 0.305, 0.01], [1, 1, 0.95], [Math.PI / 2 - 0.12, 0, 0]));
  if (!parts.noTag) {
    const tag = group([0, 0.27, 0.19 * ch], [0.25, 0, 0]);
    tag.add(mesh(CYL(), toon(tagColor), [0, 0, 0], [0.034, 0.014, 0.034], [Math.PI / 2, 0, 0]));
    tag.add(mesh(torus(0.012, 0.004), toon('#ffd35c', { outline: false }), [0, 0.036, 0]));
    torso.add(tag);
  }

  // tail
  const tail = buildTail(P, C);
  torso.add(tail);

  // head
  const headPivot = group([0, 0.33, 0.01]);
  torso.add(headPivot);
  const head = group([0, 0.29, 0]);
  headPivot.add(head);
  const headShape = group(null);
  headShape.scale.set(HEAD_W, HEAD_H, HEAD_R);
  head.add(headShape);
  headShape.add(mesh(SPHERE(), toon(C.fur)));

  const face = new Face({ ...P, fur: C.fur, fur2: C.fur2, dark: C.dark, stripe: C.stripe, patchA: C.patchA, patchB: C.patchB });
  const faceMat = new THREE.MeshToonMaterial({ map: face.tex, gradientMap: GRADIENT, transparent: true, depthWrite: false });
  faceMat.polygonOffset = true;
  faceMat.polygonOffsetFactor = -2;
  faceMat.userData.outlineParameters = { visible: false };
  const faceMesh = mesh(FACE_GEO(), faceMat);
  faceMesh.renderOrder = 1;
  headShape.add(faceMesh);

  const ears = buildEars(P, C, head);
  let bumps = [];
  if (P.species === 'frog') bumps = buildFrogBumps(P, C, head, face);
  if (P.species === 'hamster') {
    for (const side of [-1, 1]) head.add(mesh(SPHERE(), toon(C.fur), [side * 0.27, -0.13, 0.1], [0.13, 0.11, 0.12]));
  }
  if (P.species === 'penguin') {
    // tiny head tuft
    head.add(mesh(CONE(), toon(C.fur), [0.02, HEAD_H + 0.02, 0.02], [0.03, 0.08, 0.03], [0, 0, -0.35]));
  }

  const hat = buildHat(P, C, P.hat);
  if (P.species === 'frog') {
    hat.position.set(0, 0.1, -0.06);
    hat.scale.setScalar(0.85);
  }
  head.add(hat);

  // prop in right paw
  const prop = buildProp(P, P.prop);
  arms[1].userData.anchor.add(prop);

  // attachment points for effects
  const headTop = group([0, HEAD_H + (P.hat === 'wizard' ? 0.45 : P.hat === 'chef' ? 0.25 : 0.12), 0]);
  head.add(headTop);
  const sweatAnchor = group([HEAD_W * 0.75, HEAD_H * 0.45, 0.16]);
  head.add(sweatAnchor);

  // invisible hit proxy for raycasts (hover / click / overlay click-through)
  const proxy = mesh(SPHERE_LO(), new THREE.MeshBasicMaterial({ visible: false }), [0, 0.5, 0], [0.42, 0.55, 0.38]);
  scaler.add(proxy);

  return {
    root, scaler, body, facing, torso, headPivot, head, ears, bumps, tail, arms, legs, hat, prop,
    cape: parts.cape || null, face, shadow, proxy, headTop, sweatAnchor, palette: C,
    dispose() {
      face.dispose();
      bodyTex.dispose();
      bodyMat.dispose();
      faceMat.dispose();
      shadow.material.dispose();
      proxy.material.dispose();
      bumps.forEach((b) => b.children[1]?.material?.dispose());
    },
  };
}
