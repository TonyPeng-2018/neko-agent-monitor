// Cozy room (floor island, rug, plants, cushions) + DOM HUD: agent list, totals chip,
// detail card and floating name tags.
import * as THREE from 'three';
import { toon } from './character.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import {
  SPECIES_EMOJI, STATE_COLOR, STATE_LABEL, SOURCE_COLOR, normPersona, fmtTokens, fmtCost, esc, ago, clamp,
} from './util.js';

// ------------------------------------------------------------------ room
function rugTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const x = c.getContext('2d');
  const rings = ['#ffd9e4', '#fff1d6', '#dcefff', '#e6f7dc', '#f0e2ff', '#ffe8d9'];
  x.fillStyle = '#fff3ea';
  x.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 6; i++) {
    x.fillStyle = rings[i % rings.length];
    x.beginPath();
    x.arc(256, 256, 250 - i * 40, 0, Math.PI * 2);
    x.fill();
  }
  // scalloped dots
  x.fillStyle = 'rgba(255,255,255,0.8)';
  for (let r = 70; r <= 230; r += 40) {
    const n = Math.round(r / 9);
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      x.beginPath();
      x.arc(256 + Math.cos(a) * r, 256 + Math.sin(a) * r, 3.2, 0, Math.PI * 2);
      x.fill();
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function plant(pos, s, leaf) {
  const g = new THREE.Group();
  g.position.set(pos[0], 0, pos[2]);
  g.scale.setScalar(s);
  const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.22, 0.36, 24), toon('#f3b89a'));
  pot.position.y = 0.18;
  const rim = new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.045, 10, 32), toon('#f7c9b0'));
  rim.rotation.x = Math.PI / 2;
  rim.position.y = 0.36;
  g.add(pot, rim);
  const lm = toon(leaf);
  const sph = new THREE.SphereGeometry(1, 20, 14);
  for (const [x, y, z, r] of [[0, 0.62, 0, 0.26], [0.15, 0.5, 0.08, 0.17], [-0.16, 0.52, -0.02, 0.18], [0.03, 0.85, 0.02, 0.16]]) {
    const m = new THREE.Mesh(sph, lm);
    m.position.set(x, y, z);
    m.scale.setScalar(r);
    g.add(m);
  }
  return g;
}

function cushion(pos, color, rot = 0) {
  const g = new THREE.Group();
  g.position.set(pos[0], 0, pos[2]);
  g.rotation.y = rot;
  const m = new THREE.Mesh(new RoundedBoxGeometry(0.95, 0.3, 0.95, 5, 0.14), toon(color));
  m.position.y = 0.15;
  m.scale.set(1, 1, 1);
  const b = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 8), toon('#fff4fa'));
  b.scale.setScalar(0.05);
  b.position.y = 0.3;
  g.add(m, b);
  return g;
}

function yarn(pos, color) {
  const g = new THREE.Group();
  g.position.set(pos[0], 0.16, pos[2]);
  const m = new THREE.Mesh(new THREE.SphereGeometry(0.16, 24, 16), toon(color));
  g.add(m);
  const lineM = toon(new THREE.Color(color).offsetHSL(0, 0, -0.08).getStyle(), { outline: false });
  for (let i = 0; i < 3; i++) {
    const t = new THREE.Mesh(new THREE.TorusGeometry(0.162, 0.008, 6, 40), lineM);
    t.rotation.set(i * 0.9, i * 0.6, 0);
    g.add(t);
  }
  const tail = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([
    new THREE.Vector3(0.1, -0.1, 0.08), new THREE.Vector3(0.35, -0.15, 0.2), new THREE.Vector3(0.6, -0.155, 0.05),
  ]), 20, 0.012, 6), toon(color, { outline: false }));
  g.add(tail);
  return g;
}

export function buildRoom(scene, R) {
  const room = new THREE.Group();
  const base = new THREE.Mesh(new THREE.CylinderGeometry(R + 1, R + 0.85, 0.5, 72), toon('#f6d3c9'));
  base.position.y = -0.3;
  base.material.userData.outlineParameters.thickness = 0.002;
  const top = new THREE.Mesh(new THREE.CylinderGeometry(R + 1, R + 1, 0.04, 72), toon('#fff3ea', { outline: false }));
  top.position.y = -0.02;
  const rug = new THREE.Mesh(new THREE.CircleGeometry(R * 0.72, 64), new THREE.MeshToonMaterial({ map: rugTexture() }));
  rug.material.userData.outlineParameters = { visible: false };
  rug.rotation.x = -Math.PI / 2;
  rug.position.y = 0.001;
  room.add(base, top, rug);
  const k = (R + 0.4) / 5.2;
  room.add(plant([-4.4 * k, 0, -2.6 * k], 1.25, '#9fdcaa'));
  room.add(plant([4.6 * k, 0, -2.0 * k], 1.0, '#b5e3a1'));
  room.add(plant([3.2 * k, 0, 3.8 * k], 0.8, '#a8dcc8'));
  room.add(cushion([-3.8 * k, 0, 3.0 * k], '#ffc8dd', 0.3));
  room.add(cushion([1.2 * k, 0, -4.8 * k], '#cfe3ff', 0.8));
  room.add(yarn([-1.6 * k, 0, -4.6 * k], '#ffb3c7'));
  room.add(yarn([4.8 * k, 0, 1.4 * k], '#c9b8ff'));
  scene.add(room);
  return room;
}

// ------------------------------------------------------------------ HUD
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

const LEVEL_ICON = { info: 'ℹ︎', warn: '⚠︎', bad: '⛔︎' };

export function cardHTML(a, compact = false) {
  const P = normPersona(a.persona, a.id);
  const st = a.state || 'idle';
  const pct = Math.round((a.context_pct || 0) * 100);
  const prompt = (a.title || a.creation_prompt || '').trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const flags = (a.flags || []).map((f) => `<span class="flag ${esc(f.level || 'info')}">${LEVEL_ICON[f.level] || ''} ${esc(f.text || f.id)}</span>`).join('');
  const prog = a.progress_total ? `<div class="row"><span>todos</span><b>${a.progress_done || 0}/${a.progress_total}</b></div>` : '';
  const kitten = a.kind === 'subagent' ? '<span class="pill kitten">kitten</span>' : '';
  return `
    <div class="card-head">
      <div class="avatar" style="background:${esc(P.fur)}">${SPECIES_EMOJI[P.species] || '🐱'}</div>
      <div class="who">
        <div class="name">${esc(P.name)} ${kitten}</div>
        <div class="sub"><span class="src" style="background:${SOURCE_COLOR[a.source] || '#ddd'}"></span>${esc(a.source || '')} · ${esc(a.project || '')}</div>
      </div>
      <div class="state" style="background:${STATE_COLOR[st]}">${STATE_LABEL[st] || st}</div>
    </div>
    ${prompt ? `<div class="prompt">“${esc(clip(prompt, compact ? 90 : 220))}”</div>` : ''}
    ${a.state_detail ? `<div class="detail">${esc(clip(a.state_detail, compact ? 70 : 160))}</div>` : ''}
    <div class="ctx"><div class="bar"><i style="width:${pct}%;background:${pct > 80 ? '#ff8a8a' : pct > 60 ? '#ffc46b' : '#8fd6b0'}"></i></div>
      <span>${fmtTokens(a.context_tokens)} / ${fmtTokens(a.context_limit)} · ${pct}%</span></div>
    <div class="grid">
      <div class="row"><span>cost</span><b>${fmtCost(a.cost_usd)}</b></div>
      <div class="row"><span>last 10m</span><b>${fmtCost(a.cost_10m)}</b></div>
      ${compact ? '' : `<div class="row"><span>model</span><b>${esc(a.model || '—')}</b></div>
      <div class="row"><span>tools</span><b>${a.tool_count || 0}${a.last_tool ? ' · ' + esc(a.last_tool) : ''}</b></div>
      <div class="row"><span>active</span><b>${ago(a.last_activity) || '—'}</b></div>`}
      ${prog}
    </div>
    ${flags ? `<div class="flags">${flags}</div>` : ''}
    ${!compact && a.cwd ? `<div class="cwd">${esc(a.cwd)}</div>` : ''}
  `;
}

export class Hud {
  constructor(world, { overlay }) {
    this.world = world;
    this.overlay = overlay;
    this.tags = new Map();
    this.selected = null;
    this.agents = [];
    this.tagLayer = el('div', 'tags');
    document.body.appendChild(this.tagLayer);
    this.card = el('div', 'card hidden');
    document.body.appendChild(this.card);
    if (!overlay) {
      this.panel = el('div', 'panel');
      this.panel.innerHTML = `<div class="brand"><span class="logo">🐾</span> neko <small>agent monitor</small></div>
        <div class="totals"></div><div class="list"></div><div class="foot"></div>`;
      document.body.appendChild(this.panel);
      this.totalsEl = this.panel.querySelector('.totals');
      this.listEl = this.panel.querySelector('.list');
      this.footEl = this.panel.querySelector('.foot');
      this.listEl.addEventListener('click', (e) => {
        const row = e.target.closest('[data-id]');
        if (row) this.select(row.dataset.id, true);
      });
      this.listEl.addEventListener('mouseover', (e) => {
        const row = e.target.closest('[data-id]');
        world.hoverListId = row ? row.dataset.id : null;
      });
      this.listEl.addEventListener('mouseleave', () => (world.hoverListId = null));
      this.card.addEventListener('click', (e) => {
        if (e.target.closest('.close')) this.select(null);
      });
    }
  }

  setSource(label) {
    if (this.footEl) this.footEl.textContent = label;
  }

  setSnapshot(snap) {
    this.agents = snap.agents || [];
    if (this.overlay) {
      if (this.hoverCardId) this.showHoverCard(this.hoverCardId, this.hoverXY);
      return;
    }
    const order = { waiting: 0, error: 1, working: 2, idle: 3, sleeping: 4, done: 5 };
    const list = [...this.agents].sort((a, b) => {
      const pa = a.parent_id || a.id, pb = b.parent_id || b.id;
      const oa = order[this.agents.find((x) => x.id === pa)?.state || a.state] ?? 9;
      const ob = order[this.agents.find((x) => x.id === pb)?.state || b.state] ?? 9;
      if (pa !== pb) return oa - ob || pa.localeCompare(pb);
      return (a.kind === 'subagent') - (b.kind === 'subagent') || a.id.localeCompare(b.id);
    });
    const sessions = this.agents.filter((a) => a.kind !== 'subagent' && a.state !== 'done');
    const kittens = this.agents.filter((a) => a.kind === 'subagent' && a.state !== 'done');
    const need = this.agents.filter((a) => a.state === 'waiting').length;
    const errs = this.agents.filter((a) => a.state === 'error').length;
    const burn = this.agents.reduce((s, a) => s + (a.cost_10m || 0), 0);
    this.totalsEl.innerHTML = `
      <span class="chip">${sessions.length} agent${sessions.length === 1 ? '' : 's'}${kittens.length ? ` <small>+${kittens.length} 🐾</small>` : ''}</span>
      ${need ? `<span class="chip need">${need} need${need === 1 ? 's' : ''} you</span>` : ''}
      ${errs ? `<span class="chip err">${errs} stuck</span>` : ''}
      <span class="chip">${fmtCost(burn)}<small>/10m</small></span>`;
    document.title = need ? `(${need}) neko · needs you` : `neko · ${sessions.length} agents`;
    this.listEl.innerHTML = list.map((a) => {
      const P = normPersona(a.persona, a.id);
      const pct = Math.round((a.context_pct || 0) * 100);
      const st = a.state || 'idle';
      return `<div class="item ${a.kind === 'subagent' ? 'sub' : ''} ${st} ${this.selected === a.id ? 'sel' : ''}" data-id="${esc(a.id)}">
        <span class="em" style="background:${esc(P.fur)}">${SPECIES_EMOJI[P.species] || '🐱'}</span>
        <div class="mid">
          <div class="l1"><b>${esc(P.name)}</b><i class="dot" style="background:${STATE_COLOR[st]}" title="${st}"></i><span class="proj">${esc(a.project || '')}</span></div>
          <div class="l2"><div class="bar"><i style="width:${pct}%;background:${pct > 80 ? '#ff8a8a' : pct > 60 ? '#ffc46b' : '#8fd6b0'}"></i></div><span class="cost">${fmtCost(a.cost_10m)}</span></div>
        </div></div>`;
    }).join('') || '<div class="empty">No agents running — time for a nap 💤</div>';
    if (this.selected) this.renderCard();
  }

  select(id, focus = false) {
    this.selected = id;
    if (id) {
      const c = this.world.critters.get(id);
      if (c) {
        c.pokeIt();
        if (focus) this.world.focus?.(c);
      }
    }
    this.renderCard();
    this.listEl?.querySelectorAll('.item').forEach((e) => e.classList.toggle('sel', e.dataset.id === id));
  }

  renderCard() {
    const a = this.agents.find((x) => x.id === this.selected);
    if (!a) {
      this.card.classList.add('hidden');
      return;
    }
    this.card.className = 'card';
    this.card.innerHTML = `<button class="close" title="close">×</button>` + cardHTML(a);
  }

  showHoverCard(id, xy) {
    this.hoverCardId = id;
    this.hoverXY = xy;
    const a = id && this.agents.find((x) => x.id === id);
    if (!a || !xy) {
      this.card.classList.add('hidden');
      return;
    }
    this.card.className = 'card mini';
    this.card.innerHTML = cardHTML(a, true);
    const w = this.card.offsetWidth || 260, h = this.card.offsetHeight || 160;
    const x = clamp(xy[0] - w / 2, 8, innerWidth - w - 8);
    const y = clamp(xy[1] - h - 18, 8, innerHeight - h - 8);
    this.card.style.left = x + 'px';
    this.card.style.top = y + 'px';
  }

  /** Per-frame: move name tags over heads. */
  frame(camera, critters) {
    const v = new THREE.Vector3();
    const W = innerWidth, H = innerHeight;
    for (const [id, c] of critters) {
      let t = this.tags.get(id);
      if (!t) {
        t = el('div', 'tag');
        this.tagLayer.appendChild(t);
        this.tags.set(id, t);
        t._key = '';
      }
      const st = c.leaving ? 'done' : c.state;
      const key = c.P.name + '|' + st + '|' + c.isKitten;
      if (t._key !== key) {
        t._key = key;
        t.className = `tag ${st} ${c.isKitten ? 'kit' : ''}`;
        t.innerHTML = `<i style="background:${STATE_COLOR[st]}"></i>${esc(c.P.name)}`;
      }
      c.headWorld(v);
      v.y += 0.1 * c.baseScale() + 0.1;
      if (c.state === 'waiting' && !c.leaving) v.y += 0.34 * Math.sqrt(c.baseScale());
      v.project(camera);
      const vis = v.z < 1 && !c.dead && (!this.overlay || this.world.hoverId === id);
      t.style.display = vis ? '' : 'none';
      if (vis) {
        const x = (v.x * 0.5 + 0.5) * W, y = (-v.y * 0.5 + 0.5) * H;
        t.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -100%)`;
        t.style.zIndex = String(1000 - Math.round(v.z * 500));
        t.style.opacity = c.gone ? '0' : '1';
      }
    }
    for (const [id, t] of this.tags) {
      if (!critters.has(id)) {
        t.remove();
        this.tags.delete(id);
      }
    }
  }
}
