// neko web front end: scene, data feed (SSE → polling → demo), world steering, input.
import * as THREE from 'three';
import { OutlineEffect } from 'three/addons/effects/OutlineEffect.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Critter } from './anim.js';
import { FX } from './fx.js';
import { Hud, buildRoom } from './hud.js';
import { startDemo, gallerySnapshot } from './demo.js';
import { clamp } from './util.js';

const qs = new URLSearchParams(location.search);
const OVERLAY = qs.get('overlay') === '1';
const DEMO = qs.get('demo') === '1';
const MODE = OVERLAY ? 'overlay' : 'room';
const GALLERY = qs.has('gallery');
let galleryN = 0;
document.body.classList.add(MODE);

// ------------------------------------------------------------------ renderer / scene
const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
renderer.setClearColor(0x000000, 0);
const effect = new OutlineEffect(renderer, { defaultThickness: 0.0036, defaultColor: [0.35, 0.22, 0.3] });

const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight('#ffffff', '#f6d9e6', 1.25));
const sun = new THREE.DirectionalLight('#fff6ec', 2.1);
sun.position.set(3, 7, 6);
scene.add(sun);

const ROOM_R = 4.6;
let camera, controls;
const overlayView = { halfW: 8, halfH: 4, pxPerUnit: 100 };

if (OVERLAY) {
  camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 100);
} else {
  camera = new THREE.PerspectiveCamera(30, 1, 0.1, 200);
  camera.position.set(0, 5.4, 12.2);
  controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0.4, 0.3);
  controls.enablePan = false;
  controls.enableDamping = true;
  controls.minDistance = 3;
  controls.maxDistance = 24;
  controls.minPolarAngle = 0.35;
  controls.maxPolarAngle = 1.38;
  const cq = qs.get('cam');
  if (cq) {
    const n = cq.split(',').map(Number);
    camera.position.set(n[0], n[1], n[2]);
    controls.target.set(n[3] ?? 0, n[4] ?? 0.6, n[5] ?? 0);
    window.__camLock = true;
  }
  controls.update();
  buildRoom(scene, ROOM_R);
}

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  if (OVERLAY) {
    const ppu = clamp(h / 3.2, 70, 110); // character ≈ 1 unit tall → ~70–110 px
    overlayView.pxPerUnit = ppu;
    overlayView.halfW = w / ppu / 2;
    overlayView.halfH = h / ppu / 2;
    camera.left = -overlayView.halfW;
    camera.right = overlayView.halfW;
    camera.top = overlayView.halfH;
    camera.bottom = -overlayView.halfH;
    const a = 0.2; // slight downward tilt so feet & shadows read
    const margin = 0.18;
    const hgt = (10 * Math.sin(a) + overlayView.halfH - margin) / Math.cos(a);
    camera.position.set(0, hgt, 10);
    camera.rotation.set(-a, 0, 0);
    camera.updateProjectionMatrix();
  } else {
    camera.aspect = w / h;
    // keep the island in view on narrow screens
    camera.fov = w / h < 1 ? 42 : 30;
    // shift the image right so the island is centred in the space beside the side panel
    if (w > 720) camera.setViewOffset(w, h, -170, 0, w, h);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
  }
}
addEventListener('resize', resize);
resize();

// ------------------------------------------------------------------ world
const critters = new Map();
const fx = new FX(scene);
const departed = new Set();
const _v = new THREE.Vector3();

const world = {
  scene, fx, mode: MODE, critters, camera, hoverId: null, hoverListId: null, mouseWorld: null,
  spawnPoint(c) {
    if (GALLERY) {
      const i = galleryN++;
      return new THREE.Vector3(i * 1.3 - 4.55, 0, 0);
    }
    const par = c.agent.parent_id && critters.get(c.agent.parent_id);
    if (par) return par.pos.clone().add(new THREE.Vector3((Math.random() - 0.5) * 0.6, 0, -0.3));
    if (OVERLAY) return new THREE.Vector3((Math.random() * 2 - 1) * (overlayView.halfW - 0.8), 0, (Math.random() - 0.5) * 0.5);
    const a = Math.random() * Math.PI * 2, r = Math.sqrt(Math.random()) * (ROOM_R - 1);
    return new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
  },
  wanderPoint(c) {
    if (OVERLAY) {
      const span = overlayView.halfW - 0.7;
      let x = c.pos.x + (Math.random() * 2 - 1) * 3;
      if (Math.abs(x) > span) x = (Math.random() * 2 - 1) * span;
      return new THREE.Vector3(x, 0, (Math.random() - 0.5) * 0.6);
    }
    for (let i = 0; i < 6; i++) {
      const a = Math.random() * Math.PI * 2, r = Math.sqrt(Math.random()) * (ROOM_R - 0.6);
      const p = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
      if (p.distanceTo(c.pos) > 1.2) return p;
    }
    return new THREE.Vector3(0, 0, 0);
  },
  exitPoint(c) {
    if (OVERLAY) return new THREE.Vector3(Math.sign(c.pos.x || 1) * (overlayView.halfW + 2), 0, c.pos.z);
    const d = c.pos.clone().setY(0);
    if (d.length() < 0.2) d.set(Math.random() - 0.5, 0, Math.random() - 0.5);
    return d.normalize().multiplyScalar(ROOM_R + 2);
  },
  isOutside(p) {
    return OVERLAY ? Math.abs(p.x) > overlayView.halfW + 0.7 : Math.hypot(p.x, p.z) > ROOM_R + 0.35;
  },
  clampInside(p, c, soft) {
    if (c && c.leaving) return;
    if (OVERLAY) {
      const span = overlayView.halfW - 0.5;
      p.x = clamp(p.x, -span, span);
      p.z = clamp(p.z, -0.45, 0.45);
      return;
    }
    const d = Math.hypot(p.x, p.z);
    const R = ROOM_R - 0.2;
    if (d > R) {
      p.x *= R / d;
      p.z *= R / d;
    }
  },
  cameraYawFrom(p) {
    if (OVERLAY) return 0;
    return Math.atan2(camera.position.x - p.x, camera.position.z - p.z);
  },
  focus(c) {
    if (controls) focusTarget = c;
  },
};
let focusTarget = null;

const hud = new Hud(world, { overlay: OVERLAY });

function separate(dt) {
  const list = [...critters.values()];
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    for (let j = i + 1; j < list.length; j++) {
      const b = list[j];
      const dx = b.pos.x - a.pos.x, dz = b.pos.z - a.pos.z;
      let min = 0.36 * (a.baseScale() + b.baseScale()) + 0.05;
      if (a.id === b.parentId || b.id === a.parentId) min *= 0.8;
      const d2 = dx * dx + dz * dz;
      if (d2 >= min * min) continue;
      const d = Math.sqrt(d2) || 0.001;
      const push = (min - d) * Math.min(1, dt * 6) * 0.5;
      const nx = d2 > 1e-8 ? dx / d : Math.random() - 0.5, nz = d2 > 1e-8 ? dz / d : Math.random() - 0.5;
      const wa = a.leaving ? 0.2 : 1, wb = b.leaving ? 0.2 : 1;
      a.pos.x -= nx * push * wa;
      a.pos.z -= nz * push * wa;
      b.pos.x += nx * push * wb;
      b.pos.z += nz * push * wb;
    }
  }
}

// ------------------------------------------------------------------ data
let firstSnap = true;
function applySnapshot(snap) {
  if (Array.isArray(snap)) snap = { agents: snap };
  const agents = (snap && snap.agents) || [];
  const seen = new Set();
  for (const a of agents) {
    if (!a || !a.id) continue;
    if (departed.has(a.id)) {
      if (a.state === 'done') continue;
      departed.delete(a.id);
    }
    if (firstSnap && a.state === 'done') continue;
    seen.add(a.id);
    const c = critters.get(a.id);
    if (!c) critters.set(a.id, new Critter(a, world));
    else if (!c.leaving || a.state !== c.state) c.setAgent(a);
  }
  firstSnap = false;
  for (const [id, c] of critters) if (!seen.has(id)) c.depart();
  // kitten follow slots
  const counts = new Map();
  for (const c of critters.values()) {
    if (!c.parentId) continue;
    const n = counts.get(c.parentId) || 0;
    c.followIndex = n;
    counts.set(c.parentId, n + 1);
  }
  hud.setSnapshot({ ...snap, agents: agents.filter((a) => a && critters.has(a.id)) });
}

async function startFeed() {
  const useDemo = (label) => {
    hud.setSource(label);
    document.body.classList.add('demo');
    startDemo(applySnapshot);
  };
  if (qs.get('gallery')) {
    hud.setSource('gallery');
    // ?gallery=idle>done switches states after the first second (for testing transitions)
    const [s0, s1] = (qs.get('gallery') === '1' ? 'idle' : qs.get('gallery')).split('>');
    applySnapshot(gallerySnapshot(s0));
    setInterval(() => applySnapshot(gallerySnapshot(s1 || s0)), 1000);
    return;
  }
  if (DEMO) return useDemo('demo mode · simulated agents');
  try {
    const r = await fetch('/api/agents', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    applySnapshot(await r.json());
  } catch (e) {
    return useDemo('daemon unreachable · showing demo');
  }
  hud.setSource('live · 127.0.0.1');
  let polling = null;
  const poll = async () => {
    try {
      const r = await fetch('/api/agents', { cache: 'no-store' });
      if (r.ok) {
        applySnapshot(await r.json());
        hud.setSource('live · polling');
      }
    } catch (e) {
      hud.setSource('daemon offline · retrying…');
    }
  };
  if (!('EventSource' in window)) {
    polling = setInterval(poll, 2000);
    return;
  }
  const es = new EventSource('/api/stream');
  let fails = 0;
  const onMsg = (ev) => {
    fails = 0;
    if (polling) {
      clearInterval(polling);
      polling = null;
    }
    try {
      applySnapshot(JSON.parse(ev.data));
      hud.setSource('live · streaming');
    } catch (e) {
      console.warn('neko: bad snapshot', e);
    }
  };
  es.onmessage = onMsg;
  es.addEventListener('snapshot', onMsg);
  es.addEventListener('agents', onMsg);
  es.onerror = () => {
    fails++;
    if (fails >= 2 && !polling) polling = setInterval(poll, 2000);
  };
}

// ------------------------------------------------------------------ input
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
const facePlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
let mouseXY = null;

function pick(x, y) {
  ndc.set((x / innerWidth) * 2 - 1, -(y / innerHeight) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const proxies = [];
  for (const c of critters.values()) if (!c.gone) proxies.push(c.rig.proxy);
  const hit = raycaster.intersectObjects(proxies, false)[0];
  return hit ? hit.object.userData.critter : null;
}

/** For the Electron overlay: is there a cat under this screen point? */
window.nekoHitTest = (x, y) => !!pick(x, y);

let lastHover = false;
addEventListener('pointermove', (e) => {
  mouseXY = [e.clientX, e.clientY];
  const c = e.target === canvas ? pick(e.clientX, e.clientY) : null;
  world.hoverId = c ? c.id : null;
  document.body.style.cursor = c ? 'pointer' : '';
  const over = !!c || (!!e.target.closest && !!e.target.closest('.card:not(.hidden)'));
  if (over !== lastHover) {
    lastHover = over;
    window.neko?.setHover?.(over);
  }
  if (OVERLAY) hud.showHoverCard(c ? c.id : null, c ? [e.clientX, e.clientY] : null);
  raycaster.ray.intersectPlane(OVERLAY ? facePlane : floorPlane, _v) ? (world.mouseWorld = _v.clone()) : (world.mouseWorld = null);
});
addEventListener('pointerleave', () => {
  world.hoverId = null;
  world.mouseWorld = null;
  if (OVERLAY) hud.showHoverCard(null);
  if (lastHover) window.neko?.setHover?.((lastHover = false));
});

let downAt = null;
canvas.addEventListener('pointerdown', (e) => (downAt = [e.clientX, e.clientY]));
canvas.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;
  const c = pick(e.clientX, e.clientY);
  if (OVERLAY) {
    if (c) {
      c.pokeIt();
      window.neko?.select?.(c.id);
    }
    return;
  }
  hud.select(c ? c.id : null);
});
addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    hud.select(null);
    focusTarget = null;
  }
});
if (controls) controls.addEventListener('start', () => (focusTarget = null));

// ------------------------------------------------------------------ loop
const clock = new THREE.Timer();
let acc = 0;
let frames = 0;
function frame() {
  requestAnimationFrame(frame);
  window.nekoFrames = ++frames;
  if (qs.has('debug')) setTimeout(() =>  document.body.dataset.debug = `${frames} fx=${fx.live.length} t=${performance.now() | 0} warmed=${!!window.__warmed} ` + [...critters.values()].map((c) => `${c.P.name}:${c.state}:${c.w.done.toFixed(2)}:${c.doneT.toFixed(1)}:${c.leaving}`).join(' '), 0);
  clock.update();
  let dt = clock.getDelta();
  if (document.hidden) {
    acc += dt;
    if (acc < 1 / 30) return;
    dt = acc;
    acc = 0;
  }
  dt = Math.min(dt, 0.05);
  // ?warm=N: fast-forward N simulated seconds once (for headless screenshots)
  if (qs.has('warm') && critters.size && !window.__warmed && (!qs.has('warmAfter') || performance.now() > Number(qs.get('warmAfter')))) {
    window.__warmed = true;
    const steps = Math.round(Number(qs.get('warm') || 3) * 30);
    for (let i = 0; i < steps; i++) step(1 / 30);
  }
  step(dt);
  if (controls) {
    if (focusTarget && critters.has(focusTarget.id)) {
      _v.copy(focusTarget.pos).setY(0.6 * focusTarget.baseScale());
      controls.target.lerp(_v, 1 - Math.exp(-3 * dt));
    } else if (!focusTarget && !window.__camLock) {
      controls.target.lerp(_v.set(0, 0.4, 0.3), 1 - Math.exp(-1.5 * dt));
    }
    controls.update();
  }
  effect.render(scene, camera);
  hud.frame(camera, critters);
}

function step(dt) {
  for (const [id, c] of critters) {
    c.update(dt, world);
    if (c.dead) {
      c.dispose();
      critters.delete(id);
      if (c.state === 'done') departed.add(id);
      fx.sparkles(c.pos, c.baseScale() || 0.6, 8);
    }
  }
  separate(dt);
  fx.update(dt);
}

startFeed();
frame();

// debug handle
window.nekoWorld = world;
