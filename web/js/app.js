// neko web front end: scene, data feed (SSE → polling → demo), world steering, input.
import * as THREE from 'three';
import { OutlineEffect } from 'three/addons/effects/OutlineEffect.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Critter, sizeFromContext } from './anim.js';
import { FX } from './fx.js';
import { Hud, buildRoom } from './hud.js';
import { startDemo, gallerySnapshot } from './demo.js';
import { clamp } from './util.js';
import { cacheStats } from './character.js';

const qs = new URLSearchParams(location.search);
const OVERLAY = qs.get('overlay') === '1';
const DEMO = qs.get('demo') === '1';
const MODE = OVERLAY ? 'overlay' : 'room';
const GALLERY = qs.has('gallery');
let galleryN = 0;
document.body.classList.add(MODE);
if (GALLERY) document.body.classList.add('gallery'); // clean line-up: no side panel

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
const OBSTACLES = []; // plants, cushions, yarn: {x, z, r}
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
  OBSTACLES.push(...buildRoom(scene, ROOM_R).userData.obstacles);
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
    if (w > 720 && !GALLERY) camera.setViewOffset(w, h, -170, 0, w, h);
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

// ---- layout: spread characters over the island, big ones toward the back
const footR = (c) => 0.42 * c.baseScale(); // footprint radius incl. head / hat brims
let activeR = 2.4; // radius of the populated part of the island (camera frames it)

const radiusFor = (s2) => clamp(0.8 + 1.05 * Math.sqrt(s2), 1.7, ROOM_R - 0.45);
function populationRadius() {
  let s2 = 0;
  for (const c of critters.values()) {
    if (c.leaving || c.gone) continue;
    const r = footR(c) * 2 + 0.25;
    s2 += r * r;
  }
  return Math.max(radiusFor(s2), expectR);
}
// radius the latest snapshot will need (known before its characters are spawned)
let expectR = 1.7;
function expectRadius(agents) {
  let s2 = 0;
  for (const a of agents) {
    if (!a || a.state === 'done') continue;
    const r = 0.84 * (a.kind === 'subagent' ? 0.55 : 1) * sizeFromContext(a.context_pct) + 0.25;
    s2 += r * r;
  }
  return radiusFor(s2);
}

/** How much room point p has: min gap to every other critter (and where it is heading). */
function clearance(p, self) {
  let best = 9;
  const rs = self ? footR(self) : 0.42;
  for (const o of OBSTACLES) best = Math.min(best, Math.hypot(p.x - o.x, p.z - o.z) - rs - o.r);
  for (const o of critters.values()) {
    if (o === self || o.gone) continue;
    const family = self && (o.parentId === self.id || self.parentId === o.id);
    const need = (rs + footR(o)) * (family ? 0.6 : 1);
    for (const q of [o.pos, o.target]) {
      if (!q) continue;
      best = Math.min(best, Math.hypot(p.x - q.x, p.z - q.z) - need);
    }
  }
  return best;
}

function randomSpot(r) {
  if (OVERLAY) {
    const span = overlayView.halfW - 0.8;
    return new THREE.Vector3((Math.random() * 2 - 1) * span, 0, (Math.random() - 0.5) * 0.5);
  }
  const a = Math.random() * Math.PI * 2, d = Math.sqrt(Math.random()) * r;
  return new THREE.Vector3(Math.cos(a) * d, 0, Math.sin(a) * d);
}

/** Best-candidate sampling: roomy spots win; big characters prefer the back so they
 *  don't hide the small ones; optionally not too far from where we are. */
function pickSpot(c, tries, from) {
  const r = Math.max(activeR, populationRadius()) - 0.15;
  let best = null, bestScore = -Infinity;
  let sum = 0, n = 0;
  for (const o of critters.values()) if (!o.gone && !o.leaving) (sum += o.baseScale()), n++;
  const big = c && n ? c.baseScale() - sum / n : 0; // relative to the crowd
  for (let i = 0; i < tries; i++) {
    const p = randomSpot(r);
    let score = Math.min(clearance(p, c), 2.2); // prefer genuinely open ground
    // occlusion only matters in a crowd
    if (!OVERLAY) score += (-p.z / ROOM_R) * 0.7 * clamp(big, -0.4, 0.6) * clamp((n - 3) / 5, 0, 1);
    if (from) {
      const d = Math.hypot(p.x - from.x, p.z - from.z);
      if (d < 1.0) score -= 1; // go somewhere, not in place
      score -= d * 0.05;
    }
    if (score > bestScore) (bestScore = score), (best = p);
  }
  return best;
}

const world = {
  scene, fx, mode: MODE, critters, camera, hoverId: null, hoverListId: null, mouseWorld: null, crowd: 1,
  spawnPoint(c) {
    if (GALLERY) {
      const i = galleryN++;
      return new THREE.Vector3(i * 1.3 - 4.55, 0, 0);
    }
    const par = c.agent.parent_id && critters.get(c.agent.parent_id);
    if (par) return par.pos.clone().add(new THREE.Vector3((Math.random() - 0.5) * 0.6, 0, -0.3));
    return pickSpot(c, 24, null);
  },
  wanderPoint(c) {
    if (OVERLAY) {
      const span = overlayView.halfW - 0.7;
      let best = null, bs = -Infinity;
      for (let i = 0; i < 8; i++) {
        let x = c.pos.x + (Math.random() * 2 - 1) * 3;
        if (Math.abs(x) > span) x = (Math.random() * 2 - 1) * span;
        const p = new THREE.Vector3(x, 0, (Math.random() - 0.5) * 0.6);
        const sc = Math.min(clearance(p, c), 1) - (Math.abs(x - c.pos.x) < 0.6 ? 1 : 0);
        if (sc > bs) (bs = sc), (best = p);
      }
      return best;
    }
    return pickSpot(c, 10, c.pos);
  },
  /** < 0 when this critter overlaps a neighbour. */
  roomAround(c) {
    return clearance(c.pos, c);
  },
  freeSpot(c) {
    return pickSpot(c, 16, c.pos);
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
  for (const c of list) {
    if (c.leaving) continue;
    for (const o of OBSTACLES) {
      const dx = c.pos.x - o.x, dz = c.pos.z - o.z;
      const min = o.r + footR(c) * 0.8, d = Math.hypot(dx, dz);
      if (d >= min || d < 1e-4) continue;
      const push = (min - d) * Math.min(1, dt * 8);
      c.pos.x += (dx / d) * push;
      c.pos.z += (dz / d) * push;
    }
  }
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    for (let j = i + 1; j < list.length; j++) {
      const b = list[j];
      const dx = b.pos.x - a.pos.x, dz = b.pos.z - a.pos.z;
      let min = footR(a) + footR(b) + 0.06;
      if (a.id === b.parentId || b.id === a.parentId) min *= 0.7;
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
  expectR = expectRadius(agents);
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
    if (!c) {
      critters.set(a.id, new Critter(a, world));
      window.__nekoSpawned = (window.__nekoSpawned || 0) + 1;
    }
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
    startDemo(applySnapshot, { count: Number(qs.get('n')) || undefined });
  };
  if (qs.get('gallery')) {
    hud.setSource('gallery');
    // ?gallery=idle>done switches states after the first second (for testing transitions)
    const [s0, s1] = (qs.get('gallery') === '1' ? 'idle' : qs.get('gallery')).split('>');
    const over = {};
    for (const k of ['hat', 'prop', 'outfit', 'pattern', 'eyes', 'mouth']) if (qs.get(k)) over[k] = qs.get(k);
    for (const k of ['ear_size', 'chubby']) if (qs.get(k)) over[k] = Number(qs.get(k));
    applySnapshot(gallerySnapshot(s0, over));
    setInterval(() => applySnapshot(gallerySnapshot(s1 || s0, over)), 1000);
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

// ------------------------------------------------------------------ drag & drop
// Press on a character and move: it is lifted (room: slides over the floor;
// overlay: follows the pointer anywhere, then drops back to the ground strip).
let dragging = null;        // the critter being carried
const grabOff = new THREE.Vector3();
const _dp = new THREE.Vector3();

function dragPoint(x, y, out) {
  ndc.set((x / innerWidth) * 2 - 1, -(y / innerHeight) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  return raycaster.ray.intersectPlane(OVERLAY ? facePlane : floorPlane, out);
}

function startDrag(c, e) {
  if (!dragPoint(e.clientX, e.clientY, _dp)) return;
  dragging = c;
  grabOff.copy(c.pos).sub(_dp);
  // overlay: hang below the pointer, held by the scruff; room: keep the grab offset on the floor
  grabOff.y = OVERLAY ? -0.9 * c.baseScale() : 0;
  c.grab(dragTarget(_dp));
  if (controls) controls.enabled = false;
  document.body.style.cursor = 'grabbing';
  canvas.setPointerCapture?.(e.pointerId);
  window.neko?.setHover?.((lastHover = true)); // keep the overlay window catching the mouse
  if (OVERLAY) hud.showHoverCard(null);
}

function dragTarget(pt) {
  pt.add(grabOff);
  world.clampInside(pt, dragging); // stay on the island / on screen
  return pt;
}

function endDrag() {
  if (!dragging) return;
  dragging.release();
  dragging = null;
  if (controls) controls.enabled = true;
  document.body.style.cursor = '';
}

let lastHover = false;
addEventListener('pointermove', (e) => {
  mouseXY = [e.clientX, e.clientY];
  if (downAt && !dragging && downCritter && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) {
    startDrag(downCritter, e);
  }
  if (dragging) {
    if (dragPoint(e.clientX, e.clientY, _dp)) dragging.moveDrag(dragTarget(_dp));
    return;
  }
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
let downCritter = null;
canvas.addEventListener('pointerdown', (e) => {
  downAt = [e.clientX, e.clientY];
  downCritter = e.button === 0 ? pick(e.clientX, e.clientY) : null;
  if (downCritter && controls) controls.enabled = false; // don't orbit when grabbing a cat
});
addEventListener('pointercancel', () => ((downAt = downCritter = null), endDrag()));
canvas.addEventListener('pointerup', (e) => {
  const wasDrag = !!dragging;
  endDrag();
  downCritter = null;
  if (controls) controls.enabled = true;
  if (wasDrag || !downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return (downAt = null);
  downAt = null;
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
let userCamT = 0; // seconds left before auto-framing resumes after the user orbits / zooms
if (controls) controls.addEventListener('start', () => ((focusTarget = null), (userCamT = 30)));

// ------------------------------------------------------------------ auto framing
// The camera dollies so the populated part of the island (activeR) fills the view
// beside the HUD panel: one agent → close-up, a crowd → the whole island.
const _cp = new THREE.Vector3(), _dir = new THREE.Vector3(), _save = new THREE.Vector3();
const FRAME_TARGET = new THREE.Vector3(0, 0.35, 0.2);
let fitDist = null, fitT = 0;
function fits(r, h) {
  camera.updateMatrixWorld();
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    for (const y of [0, h]) {
      _cp.set(FRAME_TARGET.x + Math.cos(a) * r, y, FRAME_TARGET.z + Math.sin(a) * r).project(camera);
      if (Math.abs(_cp.x) > 0.94 || _cp.y > 0.8 || _cp.y < -0.94 || _cp.z > 1) return false;
    }
  }
  return true;
}
function computeFit(r, h) {
  _save.copy(camera.position);
  _dir.subVectors(camera.position, controls.target).normalize();
  let lo = controls.minDistance, hi = controls.maxDistance;
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2;
    camera.position.copy(FRAME_TARGET).addScaledVector(_dir, mid);
    camera.lookAt(FRAME_TARGET);
    if (fits(r, h)) hi = mid;
    else lo = mid;
  }
  camera.position.copy(_save);
  camera.lookAt(controls.target);
  camera.updateMatrixWorld();
  return hi;
}
/** Shrink everyone a little (down to 0.6×) when the island can't hold the crowd. */
function updateCrowd(dt) {
  if (OVERLAY || GALLERY) return;
  let s2 = 0;
  for (const c of critters.values()) {
    if (c.leaving || c.gone) continue;
    const r = 0.84 * c.rawScale() + 0.25;
    s2 += r * r;
  }
  const need = 1.05 * Math.sqrt(s2), room = ROOM_R - 0.45 - 0.8;
  const want = clamp((room * 1.6) / need, 0.6, 1); // islands hold ~1.6× the naive disc packing
  world.crowd += (want - world.crowd) * (1 - Math.exp(-1.5 * dt));
}

function autoFrame(dt) {
  activeR += (populationRadius() - activeR) * (1 - Math.exp(-0.8 * dt));
  if (!controls || window.__camLock || focusTarget || GALLERY) return;
  if (userCamT > 0) {
    userCamT -= dt;
    return;
  }
  fitT -= dt;
  if (fitT <= 0 || fitDist === null) {
    let hmax = 0.9;
    for (const c of critters.values()) hmax = Math.max(hmax, 1.25 * c.baseScale());
    // crowded → the whole island (rim included); a few agents → close-up on them
    const full = clamp((activeR - 1.7) / (ROOM_R - 2.15), 0, 1);
    // few agents: look at them (their centroid); a crowd: the island's centre
    let cx = 0, cz = 0, n = 0;
    for (const c of critters.values()) if (!c.leaving) (cx += c.pos.x), (cz += c.pos.z), n++;
    const k = n ? (1 - full) * 0.8 : 0;
    FRAME_TARGET.set((cx / (n || 1)) * k, 0.35, (cz / (n || 1)) * k + 0.2 * (1 - k));
    const off = Math.hypot(FRAME_TARGET.x, FRAME_TARGET.z);
    fitDist = computeFit(Math.min(activeR + 0.55 + full * 0.4, ROOM_R + 0.5 - off * 0.5), hmax);
    fitT = 0.5;
  }
  const k = 1 - Math.exp(-1.2 * dt);
  controls.target.lerp(FRAME_TARGET, k);
  _dir.subVectors(camera.position, controls.target);
  const d = _dir.length();
  camera.position.copy(controls.target).addScaledVector(_dir.normalize(), d + (fitDist - d) * k);
}
window.nekoCamera = () => ({ activeR: +activeR.toFixed(2), fitDist: fitDist && +fitDist.toFixed(2), dist: +camera.position.distanceTo(controls?.target || _cp).toFixed(2) });

// ------------------------------------------------------------------ loop
const clock = new THREE.Timer();
renderer.info.autoReset = false; // OutlineEffect renders twice per frame; count both passes
let drawCalls = 0, drawTris = 0;
window.nekoStats = () => {
  let meshes = 0, outlined = 0, sprites = 0;
  scene.traverseVisible((o) => {
    if (o.isSprite) sprites++;
    if (!o.isMesh) return;
    meshes++;
    const op = o.material.userData.outlineParameters;
    if (!op || op.visible !== false) outlined++;
  });
  const mem = renderer.info.memory; // live GPU resources — should stay flat as agents come and go
  return { calls: drawCalls, triangles: drawTris, critters: critters.size, meshes, outlined, sprites,
           geometries: mem.geometries, textures: mem.textures, cache: cacheStats(), pixelRatio: pr };
};
let acc = 0;
let frames = 0;
// Adaptive resolution: a huge crowd (100+ agents) on a slow GPU drops the pixel ratio in
// steps until frames are back under ~25 ms, and climbs back up when there's headroom.
const PR_MAX = Math.min(devicePixelRatio || 1, 2);
let pr = PR_MAX, slowT = 0, fastT = 0, emaDt = 1 / 60;
function adaptQuality(dt) {
  if (document.hidden || dt > 0.25) return; // tab switches / stalls aren't load
  emaDt += (dt - emaDt) * 0.05;
  slowT = emaDt > 1 / 40 ? slowT + dt : 0;
  fastT = emaDt < 1 / 55 ? fastT + dt : 0;
  const next = slowT > 2 && pr > 1 ? pr - 0.25 : fastT > 8 && pr < PR_MAX ? pr + 0.25 : pr;
  if (next !== pr) {
    pr = Math.max(1, Math.min(PR_MAX, next));
    renderer.setPixelRatio(pr);
    slowT = fastT = 0;
  }
}

function frame() {
  requestAnimationFrame(frame);
  window.nekoFrames = ++frames;
  if (qs.has('debug')) setTimeout(() =>  document.body.dataset.debug = `${frames} ${JSON.stringify(window.nekoStats()).replace(/ /g, '')} ${JSON.stringify(window.nekoCamera()).replace(/ /g, '')} crowd=${world.crowd.toFixed(2)} pos=${[...critters.values()].map((c) => c.pos.x.toFixed(1) + ',' + c.pos.z.toFixed(1)).join(';')} fx=${fx.live.length} t=${performance.now() | 0} warmed=${!!window.__warmed} ` + [...critters.values()].map((c) => `${c.P.name}:${c.state}:${c.w.done.toFixed(2)}:${c.doneT.toFixed(1)}:${c.leaving}`).join(' '), 0);
  clock.update();
  let dt = clock.getDelta();
  if (document.hidden) {
    acc += dt;
    if (acc < 1 / 30) return;
    dt = acc;
    acc = 0;
  }
  adaptQuality(dt);
  dt = Math.min(dt, 0.05);
  // ?warm=N: fast-forward N simulated seconds once (for headless screenshots)
  if (qs.has('warm') && critters.size && !window.__warmed && (!qs.has('warmAfter') || performance.now() > Number(qs.get('warmAfter')))) {
    window.__warmed = true;
    const steps = Math.round(Number(qs.get('warm') || 3) * 30);
    for (let i = 0; i < steps; i++) {
      step(1 / 30);
      if (controls) autoFrame(1 / 30);
    }
  }
  step(dt);
  if (controls) {
    if (focusTarget && critters.has(focusTarget.id)) {
      _v.copy(focusTarget.pos).setY(0.6 * focusTarget.baseScale());
      controls.target.lerp(_v, 1 - Math.exp(-3 * dt));
    }
    autoFrame(dt);
    controls.update();
  }
  renderer.info.reset();
  effect.render(scene, camera);
  drawCalls = renderer.info.render.calls;
  drawTris = renderer.info.render.triangles;
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
  updateCrowd(dt);
  separate(dt);
  fx.update(dt);
}

startFeed();
frame();

// debug handle
window.nekoWorld = world;
