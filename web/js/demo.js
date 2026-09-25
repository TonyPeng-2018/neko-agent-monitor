// Offline demo feed: 6–10 simulated agents with random valid personas, changing states,
// growing context / cost, subagent kittens spawning and finishing.
import { SPECIES, HATS, PROPS, OUTFITS, PATTERNS, EYES, MOUTHS, hslHex, shade, mixHex } from './util.js';

const NAMES = ['Mochi', 'Tofu', 'Pudding', 'Boba', 'Kiki', 'Nori', 'Dango', 'Miso', 'Yuzu', 'Momo', 'Pocky',
  'Sushi', 'Latte', 'Biscuit', 'Peach', 'Muffin', 'Sesame', 'Taro', 'Cocoa', 'Maple', 'Mimi', 'Bao', 'Hana', 'Kuro'];
const ROLES = {
  research: { hat: 'detective', prop: 'magnifier', prompts: ['Research how other apps handle SSE reconnects', '调研一下竞品的定价策略'] },
  coding: { hat: 'hardhat', prop: 'laptop', prompts: ['Refactor the auth middleware and add tests', 'Implement dark mode toggle in settings'] },
  design: { hat: 'beret', prop: 'brush', prompts: ['Design a cute onboarding illustration set', 'Polish the landing page hero'] },
  testing: { hat: 'goggles', prop: 'bugnet', prompts: ['Fix the flaky integration tests in CI', 'Find why login fails on Safari'] },
  writing: { hat: 'flower', prop: 'quill', prompts: ['Write the v2 release notes', '写一篇关于向量检索的博客'] },
  data: { hat: 'visor', prop: 'chart', prompts: ['Analyse last week’s churn cohort', 'Build a dashboard for token spend'] },
  devops: { hat: 'headset', prop: 'wrench', prompts: ['Migrate the deploy pipeline to GitHub Actions', 'Debug the k8s OOM restarts'] },
  planning: { hat: 'crown', prop: 'clipboard', prompts: ['Plan the Q4 roadmap milestones', 'Break the epic into tickets'] },
  chat: { hat: 'bow', prop: 'none', prompts: ['What should I cook tonight?', 'Explain monads like I am five'] },
};
const PROJECTS = ['neko-agent-monitor', 'dotfiles', 'blog', 'webshop', 'ml-notebooks'];
const MODELS = ['claude-opus-4-7', 'claude-sonnet-4-6', 'gpt-5-codex', 'gpt-5.1'];
const TOOLS = ['Read', 'Edit', 'Bash', 'Grep', 'Write', 'WebFetch', 'TodoWrite'];
const EXTRA_HATS = ['beanie', 'wizard', 'chef', 'none', 'bow', 'flower'];

const r = Math.random;
const pick = (a) => a[Math.floor(r() * a.length)];

let seq = 0;
let nameI = Math.floor(Math.random() * 24);
let hueCursor = r();

function persona(roleName, parent) {
  const role = ROLES[roleName];
  hueCursor = (hueCursor + 0.381966) % 1; // golden-angle hue spread
  const h = hueCursor;
  let fur = hslHex(h, 0.55 + r() * 0.3, 0.8 + r() * 0.07);
  let accent = hslHex(h + 0.4 + r() * 0.2, 0.7, 0.7);
  if (parent) {
    fur = mixHex(fur, parent.fur, 0.6);
    accent = mixHex(accent, parent.accent, 0.6);
  }
  return {
    v: 1,
    name: NAMES[nameI++ % NAMES.length],
    species: parent && r() < 0.6 ? parent.species : pick(SPECIES),
    role: roleName,
    hat: r() < 0.7 ? role.hat : pick(EXTRA_HATS.concat(HATS)),
    prop: r() < 0.85 ? role.prop : pick(PROPS),
    outfit: r() < 0.3 ? 'none' : pick(OUTFITS),
    fur, fur2: shade(fur, 0.12, -0.25), pattern: pick(PATTERNS), accent,
    eyes: pick(EYES), eye_color: pick(['#3b2a2a', '#2e3a5a', '#4a2e4a', '#2f4a3a']),
    mouth: pick(MOUTHS), blush: r() < 0.9,
    ear_size: 0.8 + r() * 0.4, chubby: 0.9 + r() * 0.25, tail: 0.7 + r() * 0.6, bounce: r(), speed: 0.8 + r() * 0.4,
  };
}

const STATE_DETAIL = {
  working: () => pick(['Bash: npm test', 'Edit: src/app.ts', 'Read: README.md', 'Grep: "TODO"', 'thinking…', 'WebFetch: docs']),
  waiting: () => pick(['permission: Edit foo.py', 'question: which branch?', 'permission: Bash rm -rf build']),
  idle: () => 'turn finished',
  sleeping: () => 'quiet for 14 min',
  error: () => pick(['API error 529 overloaded', 'stuck loop: same Bash ×5', 'tool failed: tsc']),
  done: () => 'finished ✓',
};

function newAgent(parent, opts = {}) {
  const roleName = opts.role || pick(Object.keys(ROLES));
  const id = (parent ? 'claude:sub-' : pick(['claude:', 'claude:', 'codex:', 'openai-sdk:'])) + (++seq).toString(36) + Math.floor(r() * 1e6).toString(36);
  const source = parent ? parent.source : id.split(':')[0];
  const limit = source === 'codex' ? 272000 : 200000;
  const now = Date.now() / 1000;
  const a = {
    id, source, kind: parent ? 'subagent' : 'session', parent_id: parent ? parent.id : null,
    pid: 10000 + Math.floor(r() * 50000), cwd: '/Users/demo/Workspace/' + (parent ? parent.project : pick(PROJECTS)),
    project: '', title: '', creation_prompt: pick(ROLES[roleName].prompts), last_prompt: '', last_text: '',
    model: parent ? parent.model : pick(MODELS), state: opts.state || 'working', state_detail: '',
    started_at: now, last_activity: now, context_tokens: Math.floor(limit * (opts.ctx ?? r() * 0.5)), context_limit: limit,
    tokens_in: 0, tokens_out: 0, cost_usd: r() * 3, cost_10m: r() * 0.6, tool_count: Math.floor(r() * 40), last_tool: pick(TOOLS),
    progress_done: 0, progress_total: r() < 0.6 ? 3 + Math.floor(r() * 6) : 0, flags: [],
    persona: persona(roleName, parent && parent.persona), _life: 0, _next: 3 + r() * 6,
  };
  a.project = a.cwd.split('/').pop();
  a.title = a.creation_prompt;
  a.state_detail = STATE_DETAIL[a.state]();
  a.context_pct = a.context_tokens / a.context_limit;
  if (a.progress_total) a.progress_done = Math.floor(r() * a.progress_total);
  return a;
}

function transition(a) {
  const w = a.kind === 'subagent'
    ? { working: 6, waiting: 0.5, idle: 0.5, error: 0.4, done: a._life > 12 ? 3 : 0 }
    : { working: 5, waiting: 1.6, idle: 1.5, sleeping: 0.8, error: 0.6, done: a._life > 60 ? 0.35 : 0 };
  if (a.state === 'error') w.working += 4;
  if (a.state === 'waiting') w.working += 3;
  const tot = Object.values(w).reduce((s, x) => s + x, 0);
  let x = r() * tot;
  for (const [k, v] of Object.entries(w)) {
    x -= v;
    if (x <= 0) return k;
  }
  return 'working';
}

/** Start the simulation; calls onSnapshot({generated_at, agents, totals}) every second. */
export function startDemo(onSnapshot, { count } = {}) {
  const agents = [];
  const n = count || 6 + Math.floor(r() * 5);
  const roles = Object.keys(ROLES);
  const startStates = ['working', 'working', 'waiting', 'idle', 'sleeping', 'error', 'working', 'working', 'idle', 'working'];
  for (let i = 0; i < n; i++) {
    agents.push(newAgent(null, { role: roles[i % roles.length], state: startStates[i % startStates.length], ctx: i === 1 ? 0.86 : undefined }));
  }
  agents[0].cost_10m = 2.4; // one hungry agent (steam)
  // a couple of kittens from the start
  agents.push(newAgent(agents[0], { role: 'research' }));
  agents.push(newAgent(agents[0], { role: 'testing' }));

  let last = performance.now();
  function tick() {
    const now = performance.now();
    const dt = (now - last) / 1000;
    last = now;
    const tnow = Date.now() / 1000;
    for (let i = agents.length - 1; i >= 0; i--) {
      const a = agents[i];
      a._life += dt;
      if (a.state === 'done') {
        a._doneFor = (a._doneFor || 0) + dt;
        if (a._doneFor > 8) agents.splice(i, 1);
        continue;
      }
      if (a.state === 'working') {
        const grow = a.context_limit * (0.002 + r() * 0.006);
        a.context_tokens = Math.min(a.context_limit, Math.floor(a.context_tokens + grow));
        const spend = r() < 0.35 ? r() * 0.12 : 0;
        a.cost_usd += spend;
        a.cost_10m = Math.max(0, a.cost_10m * 0.99 + spend);
        a.tool_count += r() < 0.3 ? 1 : 0;
        if (r() < 0.25) a.state_detail = STATE_DETAIL.working();
        if (a.progress_total && r() < 0.06 && a.progress_done < a.progress_total) a.progress_done++;
        a.last_activity = tnow;
        // spawn kittens now and then
        const kids = agents.filter((k) => k.parent_id === a.id && k.state !== 'done').length;
        if (a.kind !== 'subagent' && kids < 3 && r() < 0.012) agents.push(newAgent(a));
      } else {
        a.cost_10m *= 0.97;
      }
      if (a.context_tokens > a.context_limit * 0.97 && r() < 0.05) a.context_tokens = Math.floor(a.context_limit * 0.2); // /compact
      a.context_pct = Math.round((a.context_tokens / a.context_limit) * 1e4) / 1e4;
      a._next -= dt;
      if (a._next <= 0) {
        const st = transition(a);
        a.state = st;
        a.state_detail = STATE_DETAIL[st]();
        a._next = st === 'waiting' ? 6 + r() * 8 : 4 + r() * 9;
        a.flags = st === 'error' && r() < 0.6 ? [{ id: 'loop', level: 'bad', text: 'same tool call repeated 5×' }]
          : a.context_pct > 0.8 ? [{ id: 'ctx', level: 'warn', text: 'context almost full' }] : [];
      }
    }
    const live = agents.filter((a) => a.kind !== 'subagent' && a.state !== 'done').length;
    if (live < 5 || (live < 10 && r() < 0.008)) agents.push(newAgent(null, { state: 'working' }));
    const out = agents.map(({ _life, _next, _doneFor, ...rest }) => ({ ...rest }));
    onSnapshot({
      generated_at: tnow,
      agents: out,
      totals: {
        agents: out.length,
        waiting: out.filter((a) => a.state === 'waiting').length,
        cost_10m: out.reduce((s, a) => s + a.cost_10m, 0),
      },
    });
  }
  tick();
  const h = setInterval(tick, 1000);
  return () => clearInterval(h);
}

/** Static line-up of every species (for ?gallery=1 screenshots / cuteness review). */
export function gallerySnapshot(state = 'idle') {
  const hats = ['none', 'detective', 'hardhat', 'beret', 'crown', 'beanie', 'bow', 'wizard'];
  const props = ['none', 'magnifier', 'laptop', 'brush', 'clipboard', 'quill', 'bugnet', 'wrench'];
  const outfits = ['scarf', 'none', 'overalls', 'bowtie', 'cape', 'hoodie', 'apron', 'sweater'];
  const pats = ['tabby', 'solid', 'calico', 'tuxedo', 'socks', 'spots', 'solid', 'solid'];
  const eyes = ['round', 'sparkle', 'round', 'dot', 'round', 'sparkle', 'happy', 'sleepy'];
  const mouths = ['cat', 'smile', 'tongue', 'cat', 'o', 'smile', 'cat', 'tongue'];
  const states = state.split(',');
  return {
    generated_at: Date.now() / 1000,
    agents: SPECIES.map((sp, i) => {
      const h = (i * 0.381966 + 0.05) % 1;
      const fur = hslHex(h, 0.65, 0.84);
      return {
        id: 'claude:gallery-' + sp, source: ['claude', 'codex', 'openai-sdk'][i % 3], kind: 'session', project: 'gallery',
        state: states[i % states.length], state_detail: 'posing', context_tokens: 40000 + i * 15000, context_limit: 200000,
        context_pct: (40000 + i * 15000) / 200000, cost_usd: 1, cost_10m: 0, progress_done: i % 4, progress_total: i % 2 ? 4 : 0,
        persona: {
          name: NAMES[i], species: sp, hat: hats[i], prop: props[i], outfit: outfits[i], fur, fur2: shade(fur, 0.1, -0.3),
          accent: hslHex(h + 0.45, 0.7, 0.72), pattern: pats[i], eyes: eyes[i], eye_color: '#3b2a2a', mouth: mouths[i], blush: true,
          ear_size: 1, chubby: 1, tail: 1, bounce: 0.5, speed: 1,
        },
      };
    }),
  };
}
