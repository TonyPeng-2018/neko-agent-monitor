// Procedural animation + per-agent state machine (DESIGN.md "Mapping monitoring → character").
//
// Every state has a pose function; the rendered pose is the weighted sum of all states with
// non-zero weight, and weights ease toward the target with 1-exp(-k·dt) → smooth crossfades.
import * as THREE from 'three';
import { buildCharacter, starGeo, toon } from './character.js';
import { TEX } from './fx.js';
import { normPersona, clamp, lerp, damp, STATES } from './util.js';

const TAU = Math.PI * 2;
const _v = new THREE.Vector3();
const _v2 = new THREE.Vector3();

function easeOutBack(x) {
  const c1 = 2.0, c3 = c1 + 1;
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
}
function angleDiff(a, b) {
  let d = (b - a) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return d;
}

export function sizeFromContext(p) {
  p = clamp(p || 0, 0, 1);
  return 0.7 + 0.9 * (Math.log10(1 + 9 * p));
}

const POSE_KEYS = ['by', 'sq', 'lean', 'tilt', 'hTilt', 'hNod', 'hYaw', 'aLf', 'aLz', 'aRf', 'aRz', 'lL', 'lR', 'sit', 'tail', 'tailUp', 'ear', 'yawAdd'];
function zeroPose(p) {
  for (const k of POSE_KEYS) p[k] = 0;
  return p;
}

let ringBg = null;
function ringGeo(frac) {
  const f = Math.round(clamp(frac, 0, 1) * 48) / 48;
  const key = 'ring' + f;
  if (!ringGeo.cache) ringGeo.cache = new Map();
  let g = ringGeo.cache.get(key);
  if (!g) {
    g = new THREE.RingGeometry(0.4, 0.46, 48, 1, Math.PI / 2, -Math.max(0.0001, f) * TAU);
    ringGeo.cache.set(key, g);
  }
  return g;
}

export class Critter {
  constructor(agent, world) {
    this.world = world;
    this.id = agent.id;
    this.agent = agent;
    this.P = normPersona(agent.persona, agent.id);
    this.isKitten = agent.kind === 'subagent';
    this.rig = buildCharacter(this.P, { source: agent.source });
    this.root = this.rig.root;
    world.scene.add(this.root);
    this.rig.proxy.userData.critter = this;

    this.pos = world.spawnPoint(this);
    this.yaw = world.mode === 'overlay' ? 0 : (Math.random() - 0.5) * 1.2;
    this.phase = Math.random() * 10;
    this.t = Math.random() * 100;
    this.age = 0;
    this.w = Object.fromEntries(STATES.map((s) => [s, 0]));
    this.state = STATES.includes(agent.state) ? agent.state : 'idle';
    this.w[this.state] = 1;
    this.pose = zeroPose({});
    this._tmp = zeroPose({});
    this.moveBlend = 0;
    this.useBlend = 0;
    this.target = null;
    this.useTimer = 1 + Math.random() * 2;
    this.blinkIn = 1 + Math.random() * 3;
    this.blinkT = 0;
    this.earT = 0;
    this.earIn = 2 + Math.random() * 5;
    this.doneT = 0;
    this.leaving = false;
    this.leaveT = 0;
    this.gone = false;
    this.dead = false;
    this.hover = 0;
    this.poke = 0;
    this.zT = 0;
    this.puffT = 0;
    this.sizeT = sizeFromContext(agent.context_pct);
    this.size = this.sizeT;
    this.lastCost = agent.cost_usd || 0;
    this.lastCost10 = agent.cost_10m || 0;

    // attachments
    const r = this.rig;
    this.bubble = new THREE.Sprite(new THREE.SpriteMaterial({ map: TEX.bang, transparent: true, depthWrite: false, depthTest: false }));
    this.bubble.renderOrder = 10;
    this.bubble.visible = false;
    world.scene.add(this.bubble);

    this.sweat = new THREE.Sprite(new THREE.SpriteMaterial({ map: TEX.drop, transparent: true, depthWrite: false }));
    this.sweat.scale.setScalar(0.09);
    this.sweat.visible = false;
    r.sweatAnchor.add(this.sweat);

    this.stars = new THREE.Group();
    const starM = toon('#ffe066');
    for (let i = 0; i < 3; i++) {
      const s = new THREE.Mesh(starGeo(), starM);
      s.scale.setScalar(0.045);
      this.stars.add(s);
    }
    this.stars.position.set(0, 0.34, 0);
    this.stars.visible = false;
    r.head.add(this.stars);

    const ringMatBg = new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.45, depthWrite: false });
    ringMatBg.userData.outlineParameters = { visible: false };
    if (!ringBg) ringBg = new THREE.RingGeometry(0.4, 0.46, 48);
    this.ringBg = new THREE.Mesh(ringBg, ringMatBg);
    this.ringBg.rotation.x = -Math.PI / 2;
    this.ringBg.position.y = 0.006;
    const ringMat = new THREE.MeshBasicMaterial({ color: '#7cc8a0', transparent: true, opacity: 0.95, depthWrite: false });
    ringMat.userData.outlineParameters = { visible: false };
    this.ring = new THREE.Mesh(ringGeo(0), ringMat);
    this.ring.rotation.x = -Math.PI / 2;
    this.ring.position.y = 0.008;
    r.scaler.add(this.ringBg, this.ring);

    const glowMat = new THREE.MeshBasicMaterial({ color: '#ff6fa0', transparent: true, opacity: 0, depthWrite: false, map: TEX.soft });
    glowMat.userData.outlineParameters = { visible: false };
    this.glow = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 1.4), glowMat);
    this.glow.rotation.x = -Math.PI / 2;
    this.glow.position.y = 0.005;
    r.scaler.add(this.glow);

    this.setAgent(agent, true);
    world.fx.sparkles(this.pos, this.baseScale() * 1.1);
  }

  baseScale() {
    return (this.isKitten ? 0.55 : 1) * this.size;
  }

  setAgent(a, first = false) {
    this.agent = a;
    this.parentId = a.parent_id || null;
    const st = STATES.includes(a.state) ? a.state : 'idle';
    if (st !== this.state) {
      if (st === 'done') this.doneT = 0;
      if (st === 'waiting' && !first) this.world.fx.sparkles(this.pos, this.baseScale() * 0.6, 6);
      this.state = st;
      this.useTimer = 0.5;
    }
    this.sizeT = sizeFromContext(a.context_pct ?? (a.context_limit ? a.context_tokens / a.context_limit : 0));
    // coins when spending
    const c = a.cost_usd || 0;
    const c10 = a.cost_10m || 0;
    if (!first) {
      const delta = Math.max(c - this.lastCost, c10 - this.lastCost10, 0);
      if (delta > 0.0005) {
        const n = clamp(Math.ceil(delta / 0.08), 1, 4);
        this.coinQueue = (this.coinQueue || 0) + n;
      }
    }
    this.lastCost = c;
    this.lastCost10 = c10;
    // progress ring
    const tot = a.progress_total || 0;
    const frac = tot > 0 ? clamp((a.progress_done || 0) / tot, 0, 1) : -1;
    this.ring.visible = this.ringBg.visible = frac >= 0;
    if (frac >= 0) {
      this.ring.geometry = ringGeo(frac);
      this.ring.material.color.set(frac >= 1 ? '#f2c94c' : '#7cc8a0');
    }
  }

  /** Agent vanished from the feed (not via "done"): wave and walk off. */
  depart() {
    if (!this.leaving) {
      this.leaving = true;
      this.leaveT = 0;
      this.exit = this.world.exitPoint(this);
    }
  }

  headWorld(out) {
    return this.rig.headTop.getWorldPosition(out);
  }

  // ------------------------------------------------------------ poses
  poseWorking(p, t) {
    const P = this.P;
    const ph = this.phase;
    const mb = this.moveBlend;
    const bounce = 0.03 + P.bounce * 0.05;
    // walk cycle
    const s = Math.sin(ph), c = Math.cos(ph);
    const walk = {
      by: Math.abs(s) * bounce, sq: -Math.cos(2 * ph) * 0.035, lean: 0.08, tilt: s * 0.05,
      hTilt: -s * 0.06, hNod: 0.02, lL: s * 0.7, lR: -s * 0.7, aLf: -s * 0.6, aRf: s * 0.6,
      aLz: 0.05, aRz: 0.05, tail: Math.sin(ph * 0.5) * 0.45, tailUp: 0.1,
    };
    // "use the prop" (stand still, busy)
    const use = { by: 0, sq: Math.sin(t * 2.4) * 0.012, hNod: 0.14, aLf: 0.2, aRf: 0.9, tail: Math.sin(t * 1.3) * 0.3 };
    switch (P.prop) {
      case 'laptop': {
        const tap = Math.sin(t * 18);
        Object.assign(use, { aLf: 1.25 + tap * 0.12, aRf: 1.25 - tap * 0.12, aLz: -0.35, aRz: -0.35, hNod: 0.22, by: Math.abs(tap) * 0.006 });
        break;
      }
      case 'magnifier': {
        const sw = Math.sin(t * 1.8);
        Object.assign(use, { aRf: 1.3, aRz: -0.2 + sw * 0.35, hYaw: sw * 0.3, lean: 0.12, hNod: 0.12 });
        break;
      }
      case 'brush':
        Object.assign(use, { aRf: 1.3 + Math.sin(t * 5) * 0.4, aRz: Math.cos(t * 5) * 0.2, hTilt: Math.sin(t * 2.5) * 0.1 });
        break;
      case 'bugnet': {
        const sw = Math.sin(t * 3.4);
        Object.assign(use, { aRf: 1.2 + sw * 0.9, aRz: 0.3, tilt: sw * 0.08, hYaw: sw * 0.2, by: Math.max(0, Math.sin(t * 6.8)) * 0.03 });
        break;
      }
      case 'quill':
        Object.assign(use, { aRf: 1.05 + Math.sin(t * 11) * 0.07, aRz: -0.25 + Math.cos(t * 11) * 0.07, aLf: 0.7, aLz: -0.3, hNod: 0.25 });
        break;
      case 'chart':
        Object.assign(use, { aRf: 1.6 + Math.sin(t * 2) * 0.05, aRz: 0.2, aLf: 1.1 + Math.sin(t * 4) * 0.2, aLz: -0.2, hTilt: 0.12, hNod: -0.05 });
        break;
      case 'wrench':
        Object.assign(use, { aRf: 1.1, aRz: -0.2 + Math.sin(t * 7) * 0.35, lean: 0.1, hNod: 0.18 });
        break;
      case 'clipboard':
        Object.assign(use, { aRf: 1.1, aRz: -0.3, aLf: 0.9 + Math.max(0, Math.sin(t * 7)) * 0.25, aLz: -0.4, hNod: 0.2, hTilt: Math.sin(t * 1.5) * 0.08 });
        break;
      default: {
        const tap = Math.sin(t * 12);
        Object.assign(use, { aLf: 0.9 + tap * 0.1, aRf: 0.9 - tap * 0.1, aLz: -0.25, aRz: -0.25, hTilt: Math.sin(t * 1.4) * 0.1 });
      }
    }
    for (const k of POSE_KEYS) p[k] = lerp(use[k] || 0, walk[k] || 0, mb);
    return p;
  }

  poseWaiting(p, t) {
    const hop = Math.abs(Math.sin(t * 4.6));
    p.by = hop * 0.13;
    p.sq = (hop < 0.2 ? -0.08 * (1 - hop / 0.2) : 0) + (hop > 0.8 ? 0.04 : 0);
    p.aRz = 1.25 + Math.sin(t * 11) * 0.38;
    p.aRf = 0.55;
    p.aLz = 0.15;
    p.hTilt = Math.sin(t * 2.3) * 0.15;
    p.hNod = -0.1;
    p.tail = Math.sin(t * 6) * 0.5;
    p.tailUp = 0.25;
    p.lL = -hop * 0.2;
    p.lR = -hop * 0.2;
    return p;
  }

  poseIdle(p, t) {
    p.sit = 1;
    p.sq = Math.sin(t * 1.9) * 0.015;
    p.hTilt = Math.sin(t * 0.6) * 0.14;
    p.aLf = 0.35;
    p.aRf = 0.35;
    p.aLz = -0.35;
    p.aRz = -0.35;
    p.tail = Math.sin(t * 0.9) * 0.35;
    const m = this.world.mouseWorld;
    if (m && this.world.mode !== 'overlay') {
      const want = Math.atan2(m.x - this.pos.x, m.z - this.pos.z);
      p.hYaw = clamp(angleDiff(this.yaw, want), -0.7, 0.7) * 0.8;
      const d = Math.hypot(m.x - this.pos.x, m.z - this.pos.z);
      p.hNod = clamp(0.25 - d * 0.08, -0.12, 0.2);
    } else if (m) {
      p.hYaw = clamp((m.x - this.pos.x) * 0.25, -0.6, 0.6);
      p.hNod = clamp((0.6 - m.y) * 0.15, -0.2, 0.15);
    }
    return p;
  }

  poseSleeping(p, t) {
    const br = Math.sin(t * 1.5);
    p.sit = 1.25;
    p.sq = br * 0.035;
    p.lean = 0.06;
    p.hNod = 0.12 + br * 0.02;
    p.hTilt = 0.3;
    p.aLf = 0.55;
    p.aRf = 0.55;
    p.aLz = -0.45;
    p.aRz = -0.45;
    p.tail = 0.9;
    p.tailUp = -0.5;
    p.ear = 0.35;
    return p;
  }

  poseError(p, t) {
    p.tilt = Math.sin(t * 4.2) * 0.14;
    p.hTilt = Math.sin(t * 4.2 + 1.2) * 0.28;
    p.hNod = 0.06 + Math.sin(t * 3) * 0.05;
    p.by = Math.abs(Math.sin(t * 4.2)) * 0.015;
    p.aLz = -0.3 + Math.sin(t * 4.2) * 0.2;
    p.aRz = -0.3 - Math.sin(t * 4.2) * 0.2;
    p.lL = Math.sin(t * 4.2) * 0.15;
    p.lR = -Math.sin(t * 4.2) * 0.15;
    p.tail = Math.sin(t * 2) * 0.2;
    p.tailUp = -0.3;
    p.ear = 0.3;
    return p;
  }

  poseDone(p, t) {
    const T = this.doneT;
    if (!this.leaving) {
      const j = Math.max(0, Math.sin(T * 6));
      p.by = j * 0.28;
      p.sq = j < 0.15 ? -0.08 : 0.03;
      p.aLz = 1.35 + Math.sin(T * 12) * 0.25;
      p.aRz = 1.35 - Math.sin(T * 12) * 0.25;
      p.aLf = 0.45;
      p.aRf = 0.45;
      p.yawAdd = T < 1.2 ? easeOutBack(Math.min(1, T / 1.2)) * TAU : TAU;
      p.hNod = -0.15;
      p.tail = Math.sin(T * 10) * 0.6;
      p.tailUp = 0.3;
      p.lL = -j * 0.35;
      p.lR = -j * 0.35;
      return p;
    }
    return this.poseLeaving(p, t);
  }

  poseLeaving(p, t) {
    const ph = this.phase;
    const s = Math.sin(ph);
    p.by = Math.abs(s) * 0.06;
    p.sq = -Math.cos(2 * ph) * 0.035;
    p.lL = s * 0.75;
    p.lR = -s * 0.75;
    p.aLf = -s * 0.5;
    p.aRz = 1.25 + Math.sin(t * 10) * 0.38;
    p.aRf = 0.5;
    p.tail = Math.sin(ph * 0.5) * 0.5;
    p.tailUp = 0.2;
    p.hTilt = 0.1;
    return p;
  }

  expression() {
    const P = this.P;
    let st = this.leaving ? 'done' : this.state;
    let e;
    switch (st) {
      case 'waiting': e = { eyes: 'wide', mouth: 'o' }; break;
      case 'sleeping': e = { eyes: 'closed', mouth: 'tiny' }; break;
      case 'error': e = { eyes: 'spiral', mouth: 'wavy' }; break;
      case 'done': e = { eyes: 'happy', mouth: 'open' }; break;
      default: e = { eyes: P.eyes, mouth: P.mouth };
    }
    if (this.poke > 0 && st !== 'sleeping' && st !== 'error') e = { eyes: 'happy', mouth: 'open' };
    if (this.blinkT > 0 && !['closed', 'happy', 'spiral'].includes(e.eyes)) e.eyes = 'closed';
    e.blush = P.blush || st === 'waiting' || this.hover > 0.5;
    return e;
  }

  // ------------------------------------------------------------ update
  update(dt, world) {
    this.t += dt;
    this.age += dt;
    const t = this.t;
    const P = this.P;
    const r = this.rig;

    // state weights
    const target = this.leaving ? 'done' : this.state;
    for (const s of STATES) this.w[s] += ((s === target ? 1 : 0) - this.w[s]) * damp(6, dt);
    if (this.state === 'done' || this.leaving) this.doneT += dt;
    if (this.state === 'done' && !this.leaving && this.doneT > 2.8) this.depart();

    // locomotion
    let moving = false;
    const speed = 0.55 * P.speed * Math.sqrt(this.baseScale()) * (this.leaving ? 1.5 : 1);
    let desiredYaw = null;
    const parent = this.parentId ? world.critters.get(this.parentId) : null;
    if (this.leaving) {
      this.leaveT += dt;
      if (this.state !== 'done' || this.doneT > 2.8) {
        _v.subVectors(this.exit, this.pos);
        _v.y = 0;
        if (_v.length() > 0.1) {
          moving = true;
          this.target = this.exit;
        }
        if (world.isOutside(this.pos) || this.leaveT > 14) this.gone = true;
      }
    } else if (this.state === 'working') {
      if (parent && !parent.leaving) {
        const idx = this.followIndex || 0;
        const behind = -0.75 * parent.baseScale() - 0.25 * Math.floor(idx / 2);
        const side = (idx % 2 === 0 ? 1 : -1) * (0.35 + 0.15 * Math.floor(idx / 2));
        const cy = Math.cos(parent.yaw), sy = Math.sin(parent.yaw);
        this.followPt = this.followPt || new THREE.Vector3();
        this.followPt.set(parent.pos.x + sy * behind + cy * side, 0, parent.pos.z + cy * behind - sy * side);
        world.clampInside(this.followPt, this);
        const d = this.followPt.distanceTo(this.pos);
        this.target = this.followPt;
        moving = d > (this.moveBlend > 0.5 ? 0.12 : 0.35);
      } else {
        if (this.useTimer > 0) {
          this.useTimer -= dt;
          if (this.useTimer <= 0) this.target = world.wanderPoint(this);
        } else if (this.target) {
          _v.subVectors(this.target, this.pos);
          _v.y = 0;
          if (_v.length() < 0.15) {
            this.target = null;
            this.useTimer = P.prop === 'none' ? 1.5 + Math.random() * 2.5 : 2.5 + Math.random() * 4;
          } else moving = true;
        } else {
          this.target = world.wanderPoint(this);
        }
      }
    }
    this.moveBlend += ((moving ? 1 : 0) - this.moveBlend) * damp(7, dt);
    if (moving && this.target) {
      _v.subVectors(this.target, this.pos);
      _v.y = 0;
      const d = _v.length();
      if (d > 1e-4) {
        _v.multiplyScalar(1 / d);
        desiredYaw = Math.atan2(_v.x, _v.z);
        const facing = Math.cos(angleDiff(this.yaw, desiredYaw));
        const sp = speed * this.moveBlend * clamp(0.3 + facing, 0.15, 1) * (parent && d > 1 ? 1.6 : 1);
        this.pos.addScaledVector(_v, Math.min(d, sp * dt));
      }
    }
    this.phase += dt * (6.5 + P.bounce * 2) * P.speed * Math.max(this.moveBlend, 0.001) / Math.sqrt(this.baseScale() + 0.3) * 1.3;

    // face camera when calling for attention, sitting, celebrating
    if (desiredYaw === null) {
      if (this.state === 'waiting' || this.state === 'idle' || this.state === 'done' || this.state === 'error') {
        desiredYaw = world.cameraYawFrom(this.pos);
      } else if (this.state === 'working' && this.useBlend > 0.4) {
        desiredYaw = lerp(this.yaw, world.cameraYawFrom(this.pos), 0.02); // drift slowly toward camera
      }
    }
    if (desiredYaw !== null) this.yaw += angleDiff(this.yaw, desiredYaw) * damp(this.moveBlend > 0.5 ? 6 : 4, dt);
    this.useBlend = this.w.working * (1 - this.moveBlend);

    // pose = Σ w_s · pose_s
    const pose = zeroPose(this.pose);
    const tmp = this._tmp;
    const fns = {
      working: this.poseWorking, waiting: this.poseWaiting, idle: this.poseIdle,
      sleeping: this.poseSleeping, error: this.poseError, done: this.poseDone,
    };
    for (const s of STATES) {
      const w = this.w[s];
      if (w < 0.003) continue;
      zeroPose(tmp);
      fns[s].call(this, tmp, t);
      for (const k of POSE_KEYS) pose[k] += tmp[k] * w;
    }

    // blink / ear twitch timers
    this.blinkIn -= dt;
    if (this.blinkT > 0) this.blinkT -= dt;
    if (this.blinkIn <= 0) {
      this.blinkT = this.state === 'idle' ? 0.28 : 0.12;
      this.blinkIn = 1.8 + Math.random() * 3.5;
      if (Math.random() < 0.2) this.blinkIn = 0.25; // double blink
    }
    this.earIn -= dt;
    if (this.earIn <= 0) {
      this.earT = 0.3;
      this.earSide = Math.random() < 0.5 ? 0 : 1;
      this.earIn = 2.5 + Math.random() * 6;
    }
    if (this.earT > 0) this.earT -= dt;
    if (this.poke > 0) this.poke -= dt;
    this.hover += ((world.hoverId === this.id || world.hoverListId === this.id ? 1 : 0) - this.hover) * damp(10, dt);

    // ---------------------------------------------------------- apply
    this.size += (this.sizeT - this.size) * damp(2, dt);
    let sc = this.baseScale();
    if (this.age < 0.55) sc *= Math.max(0.01, easeOutBack(this.age / 0.55));
    if (this.gone) {
      this.goneT = (this.goneT || 0) + dt;
      sc *= Math.max(0, 1 - this.goneT / 0.35);
      if (this.goneT >= 0.35) this.dead = true;
    }
    sc *= 1 + this.hover * 0.06;
    r.scaler.scale.setScalar(sc);

    world.clampInside(this.pos, this, true);
    this.root.position.copy(this.pos);
    this.root.rotation.y = this.yaw;
    r.facing.rotation.y = pose.yawAdd;

    const sit = clamp(pose.sit, 0, 1.3);
    const pokeHop = this.poke > 0 ? Math.sin((1 - this.poke / 0.6) * Math.PI) * 0.18 : 0;
    r.body.position.y = pose.by + pokeHop - 0.07 * sit;
    const sq = pose.sq;
    r.body.scale.set(1 - sq * 0.6, 1 + sq, 1 - sq * 0.6);
    r.torso.rotation.set(pose.lean, 0, pose.tilt);
    r.headPivot.rotation.set(pose.hNod, pose.hYaw, pose.hTilt);
    r.legs[0].rotation.x = pose.lL - 1.25 * Math.min(1, sit);
    r.legs[1].rotation.x = pose.lR - 1.25 * Math.min(1, sit);
    r.legs[0].rotation.z = -0.15 * Math.min(1, sit);
    r.legs[1].rotation.z = 0.15 * Math.min(1, sit);
    const [aL, aR] = r.arms;
    aL.rotation.set(-pose.aLf, 0, -(0.55 + pose.aLz));
    aR.rotation.set(-pose.aRf, 0, 0.55 + pose.aRz);
    const anchor = aR.userData.anchor;
    anchor.rotation.order = 'ZYX';
    anchor.rotation.set(aR.rotation.x * -0.92, 0, -aR.rotation.z * 0.9);
    if (P.prop === 'laptop') {
      const show = clamp(this.useBlend * 1.6 - 0.3, 0, 1);
      r.prop.scale.setScalar(show < 0.01 ? 0.0001 : easeOutBack(show));
    }
    r.tail.rotation.set(0.1 + pose.tailUp, pose.tail, pose.tail * 0.3);
    if (r.cape) r.cape.rotation.x = -0.05 - this.moveBlend * 0.3 - Math.abs(Math.sin(this.phase)) * 0.08 * this.moveBlend;
    const twitch = this.earT > 0 ? Math.sin((this.earT / 0.3) * Math.PI * 3) * 0.3 : 0;
    r.ears.forEach((e, i) => {
      if (!e.userData.baseRot) e.userData.baseRot = e.rotation.clone();
      const b = e.userData.baseRot;
      e.rotation.x = b.x - pose.ear * 0.5 + (i === this.earSide ? twitch * 0.5 : 0);
      e.rotation.z = b.z + (i === 0 ? 1 : -1) * pose.ear * 0.6 + (i === this.earSide ? twitch * 0.3 : 0);
    });

    // face
    r.face.set(this.expression());

    // shadow shrinks while airborne
    const air = Math.max(0, r.body.position.y);
    r.shadow.scale.setScalar(0.75 * (1 - clamp(air * 2, 0, 0.45)) * (1 + 0.15 * sit));

    // ---------------------------------------------------------- effects
    const fx = world.fx;
    const headPos = this.headWorld(_v2);
    // "!" bubble when waiting
    const wWait = this.w.waiting;
    this.bubble.visible = wWait > 0.05;
    if (this.bubble.visible) {
      const bs = 0.36 * Math.sqrt(sc) * easeOutBack(Math.min(1, wWait)) * (1 + Math.sin(t * 6) * 0.05);
      this.bubble.scale.set(bs, bs, bs);
      this.bubble.position.set(headPos.x + 0.12 * sc, headPos.y + 0.12 * sc + Math.sin(t * 3) * 0.03, headPos.z);
    }
    this.glow.material.opacity = wWait * (0.3 + Math.sin(t * 4) * 0.12);
    // dizzy stars orbit
    const wErr = this.w.error;
    this.stars.visible = wErr > 0.05;
    if (this.stars.visible) {
      this.stars.children.forEach((s, i) => {
        const a = t * 3.2 + (i / 3) * TAU;
        s.position.set(Math.cos(a) * 0.3, 0.02 + Math.sin(a * 2) * 0.02, Math.sin(a) * 0.26);
        s.rotation.set(0, a * 2, 0.3);
        s.scale.setScalar(0.045 * wErr);
      });
    }
    // sweat drop near full context
    const cp = this.agent.context_pct ?? 0;
    this.sweat.visible = cp > 0.8 && !this.gone;
    if (this.sweat.visible) {
      const k = (t * 0.8) % 1;
      this.sweat.position.set(0, -k * 0.05, 0);
      this.sweat.material.opacity = k < 0.8 ? 1 : 1 - (k - 0.8) / 0.2;
    }
    // Zzz
    if (this.state === 'sleeping' && this.w.sleeping > 0.6) {
      this.zT -= dt;
      if (this.zT <= 0) {
        fx.z(_v.set(headPos.x + 0.15 * sc, headPos.y - 0.05 * sc, headPos.z), Math.sqrt(sc));
        this.zT = 1.1;
      }
    }
    // coins for spend, steam for high burn
    if (this.coinQueue > 0) {
      this.coinT = (this.coinT || 0) - dt;
      if (this.coinT <= 0) {
        fx.coin(headPos, Math.sqrt(sc));
        this.coinQueue--;
        this.coinT = 0.18;
      }
    }
    const burn = this.agent.cost_10m || 0;
    if (burn > 1.5 && this.state === 'working') {
      this.puffT -= dt;
      if (this.puffT <= 0) {
        fx.puff(_v.set(headPos.x - 0.1 * sc, headPos.y - 0.05 * sc, headPos.z), Math.sqrt(sc));
        this.puffT = clamp(1.2 - burn * 0.1, 0.25, 1.2);
      }
    }
    // done: confetti once
    if (this.state === 'done' && !this.confettied && this.doneT > 0.25) {
      this.confettied = true;
      fx.confetti(this.pos, Math.max(0.6, sc));
    }
  }

  pokeIt() {
    this.poke = 0.6;
    const hp = this.headWorld(new THREE.Vector3());
    this.world.fx.heart(hp, Math.sqrt(this.baseScale()));
  }

  dispose() {
    this.world.scene.remove(this.root);
    this.world.scene.remove(this.bubble);
    this.bubble.material.dispose();
    this.sweat.material.dispose();
    this.ring.material.dispose();
    this.ringBg.material.dispose();
    this.glow.material.dispose();
    this.glow.geometry.dispose();
    this.rig.dispose();
  }
}
