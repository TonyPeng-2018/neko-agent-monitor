// Tiny particle / sprite effects: sparkles, confetti, coins, steam puffs, Zzz, "!" bubbles.
import * as THREE from 'three';

function canvasTex(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function starPath(x, cx, cy, r, inner = 0.42, n = 4) {
  x.beginPath();
  for (let i = 0; i < n * 2; i++) {
    const a = (i / (n * 2)) * Math.PI * 2 - Math.PI / 2;
    const rr = i % 2 === 0 ? r : r * inner;
    x.lineTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
  }
  x.closePath();
}

export const TEX = {};
function initTextures() {
  if (TEX.sparkle) return;
  TEX.sparkle = canvasTex(64, 64, (x) => {
    const g = x.createRadialGradient(32, 32, 0, 32, 32, 30);
    g.addColorStop(0, 'rgba(255,255,255,0.9)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    x.fillStyle = g;
    x.fillRect(0, 0, 64, 64);
    starPath(x, 32, 32, 28, 0.3, 4);
    x.fillStyle = '#ffffff';
    x.fill();
  });
  TEX.soft = canvasTex(64, 64, (x) => {
    const g = x.createRadialGradient(32, 32, 0, 32, 32, 31);
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(0.5, 'rgba(255,255,255,0.7)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    x.fillStyle = g;
    x.fillRect(0, 0, 64, 64);
  });
  TEX.confetti = canvasTex(16, 16, (x) => {
    x.fillStyle = '#fff';
    x.beginPath();
    x.roundRect(2, 4, 12, 8, 3);
    x.fill();
  });
  TEX.coin = canvasTex(64, 64, (x) => {
    x.fillStyle = '#e7a92e';
    x.beginPath(); x.arc(32, 33, 26, 0, Math.PI * 2); x.fill();
    x.fillStyle = '#ffd65c';
    x.beginPath(); x.arc(32, 31, 24, 0, Math.PI * 2); x.fill();
    x.fillStyle = '#ffe79a';
    x.beginPath(); x.arc(32, 31, 17, 0, Math.PI * 2); x.fill();
    x.fillStyle = '#d99a22';
    x.font = 'bold 26px Nunito, sans-serif';
    x.textAlign = 'center';
    x.textBaseline = 'middle';
    x.fillText('$', 32, 33);
  });
  TEX.z = canvasTex(64, 64, (x) => {
    x.font = '900 46px "M PLUS Rounded 1c", Nunito, sans-serif';
    x.textAlign = 'center';
    x.textBaseline = 'middle';
    x.lineWidth = 8;
    x.strokeStyle = '#ffffff';
    x.strokeText('Z', 32, 34);
    x.fillStyle = '#9b8ad8';
    x.fillText('Z', 32, 34);
  });
  TEX.bang = canvasTex(128, 128, (x) => {
    // round speech bubble with a pink "!"
    x.fillStyle = '#ffffff';
    x.strokeStyle = '#ff7aa8';
    x.lineWidth = 6;
    x.beginPath();
    x.arc(64, 56, 44, 0, Math.PI * 2);
    x.moveTo(52, 96);
    x.fill();
    x.stroke();
    x.beginPath();
    x.moveTo(50, 94);
    x.lineTo(60, 120);
    x.lineTo(72, 96);
    x.closePath();
    x.fill();
    x.beginPath();
    x.moveTo(50, 96); x.lineTo(60, 120); x.lineTo(72, 97);
    x.stroke();
    x.fillStyle = '#ffffff';
    x.fillRect(48, 86, 28, 12);
    x.fillStyle = '#ff5c93';
    x.beginPath();
    x.roundRect(56, 26, 16, 38, 8);
    x.fill();
    x.beginPath();
    x.arc(64, 76, 8.5, 0, Math.PI * 2);
    x.fill();
  });
  TEX.drop = canvasTex(64, 64, (x) => {
    x.fillStyle = '#9fdcff';
    x.strokeStyle = '#5fb2e6';
    x.lineWidth = 4;
    x.beginPath();
    x.moveTo(32, 6);
    x.bezierCurveTo(40, 22, 52, 32, 52, 42);
    x.arc(32, 42, 20, 0, Math.PI);
    x.bezierCurveTo(12, 32, 24, 22, 32, 6);
    x.fill();
    x.stroke();
    x.fillStyle = '#ffffff';
    x.beginPath(); x.ellipse(25, 42, 5, 8, 0.3, 0, Math.PI * 2); x.fill();
  });
  TEX.heart = canvasTex(64, 64, (x) => {
    x.fillStyle = '#ff8fb8';
    x.beginPath();
    x.moveTo(32, 54);
    x.bezierCurveTo(4, 36, 8, 10, 32, 20);
    x.bezierCurveTo(56, 10, 60, 36, 32, 54);
    x.fill();
  });
}

const CONFETTI_COLORS = ['#ff8fb8', '#8fd0ff', '#ffe066', '#a7e3a0', '#c9a7ff', '#ffb38a'];

/** World-space particle manager (one per scene). Sprites are pooled per texture. */
export class FX {
  constructor(scene) {
    initTextures();
    this.scene = scene;
    this.live = [];
    this.pool = [];
  }
  _sprite(tex) {
    let s = this.pool.pop();
    if (!s) {
      s = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, depthWrite: false }));
      s.renderOrder = 5;
    }
    s.material.map = tex;
    s.material.color.set('#ffffff');
    s.material.opacity = 1;
    s.material.rotation = 0;
    s.material.blending = THREE.NormalBlending;
    s.material.needsUpdate = true;
    s.visible = true;
    this.scene.add(s);
    return s;
  }
  spawn(tex, pos, o = {}) {
    const s = this._sprite(tex);
    s.position.copy(pos);
    const size = o.size ?? 0.1;
    s.scale.set(size, size, size);
    if (o.color) s.material.color.set(o.color);
    if (o.additive) s.material.blending = THREE.AdditiveBlending;
    this.live.push({
      s, age: 0, life: o.life ?? 1, vel: o.vel ? o.vel.clone() : new THREE.Vector3(), grav: o.grav ?? 0,
      size, grow: o.grow ?? 0, spin: o.spin ?? 0, drag: o.drag ?? 0, popIn: o.popIn ?? 0.12, wob: o.wob ?? 0, seed: Math.random() * 10,
    });
    return s;
  }
  sparkles(pos, scale = 1, n = 12) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2;
      const up = Math.random() * 0.8 + 0.3;
      const v = new THREE.Vector3(Math.cos(a) * 1.2, up * 1.6, Math.sin(a) * 1.2).multiplyScalar(scale * (0.6 + Math.random() * 0.5));
      this.spawn(TEX.sparkle, pos.clone().add(new THREE.Vector3(0, 0.35 * scale, 0)), {
        size: 0.13 * scale * (0.6 + Math.random() * 0.8), life: 0.7 + Math.random() * 0.4, vel: v, drag: 3.2,
        color: CONFETTI_COLORS[i % CONFETTI_COLORS.length], spin: (Math.random() - 0.5) * 6,
      });
    }
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      this.spawn(TEX.soft, pos.clone().add(new THREE.Vector3(Math.cos(a) * 0.15 * scale, 0.1 * scale, Math.sin(a) * 0.15 * scale)), {
        size: 0.25 * scale, grow: 0.5 * scale, life: 0.55, vel: new THREE.Vector3(Math.cos(a) * 0.6, 0.25, Math.sin(a) * 0.6).multiplyScalar(scale), drag: 3,
      });
    }
  }
  confetti(pos, scale = 1, n = 36) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2;
      const v = new THREE.Vector3(Math.cos(a) * (0.4 + Math.random()), 2.2 + Math.random() * 1.6, Math.sin(a) * (0.4 + Math.random())).multiplyScalar(scale);
      this.spawn(TEX.confetti, pos.clone().add(new THREE.Vector3(0, 0.9 * scale, 0)), {
        size: 0.07 * scale, life: 1.8 + Math.random() * 0.8, vel: v, grav: -4.5 * scale, drag: 1.2,
        color: CONFETTI_COLORS[i % CONFETTI_COLORS.length], spin: (Math.random() - 0.5) * 14, wob: 1,
      });
    }
  }
  coin(pos, scale = 1) {
    this.spawn(TEX.coin, pos.clone().add(new THREE.Vector3((Math.random() - 0.5) * 0.2 * scale, 0, 0)), {
      size: 0.14 * scale, life: 1.2, vel: new THREE.Vector3(0, 1.3 * scale, 0), grav: -1.2 * scale, spin: 0,
    });
  }
  puff(pos, scale = 1) {
    this.spawn(TEX.soft, pos.clone().add(new THREE.Vector3((Math.random() - 0.5) * 0.1 * scale, 0, 0)), {
      size: 0.09 * scale, grow: 0.2 * scale, life: 1.1, vel: new THREE.Vector3((Math.random() - 0.5) * 0.15, 0.5 * scale, 0), drag: 0.6, color: '#fff4fa',
    });
  }
  z(pos, scale = 1) {
    this.spawn(TEX.z, pos.clone(), {
      size: 0.13 * scale, grow: 0.12 * scale, life: 2.2, vel: new THREE.Vector3(0.12 * scale, 0.28 * scale, 0), wob: 0.6, popIn: 0.3,
    });
  }
  heart(pos, scale = 1) {
    this.spawn(TEX.heart, pos.clone(), { size: 0.12 * scale, life: 1.3, vel: new THREE.Vector3(0, 0.6 * scale, 0), wob: 0.5, grow: 0.05 });
  }
  update(dt) {
    for (let i = this.live.length - 1; i >= 0; i--) {
      const p = this.live[i];
      p.age += dt;
      const k = p.age / p.life;
      if (k >= 1) {
        p.s.visible = false;
        this.scene.remove(p.s);
        this.pool.push(p.s);
        this.live.splice(i, 1);
        continue;
      }
      p.vel.y += p.grav * dt;
      if (p.drag) p.vel.multiplyScalar(Math.exp(-p.drag * dt));
      p.s.position.addScaledVector(p.vel, dt);
      if (p.wob) p.s.position.x += Math.sin(p.age * 5 + p.seed) * p.wob * 0.004;
      const pop = p.popIn > 0 ? Math.min(1, p.age / p.popIn) : 1;
      const easePop = pop < 1 ? 1 - Math.pow(1 - pop, 3) * 1.0 : 1;
      const sz = (p.size + p.grow * k) * easePop;
      p.s.scale.set(sz, sz, sz);
      p.s.material.rotation += p.spin * dt;
      p.s.material.opacity = k < 0.65 ? 1 : 1 - (k - 0.65) / 0.35;
    }
  }
}
