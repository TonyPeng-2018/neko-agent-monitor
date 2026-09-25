"""Demo mode: a little world of fake agents that evolve over time.

``neko serve --demo`` uses :class:`DemoWorld` instead of the real collectors so
the room / overlay can be shown without any agent running. Personas are
attached by the server exactly like for real agents (``neko.persona`` if
available, else :func:`tiny_persona`, a dependency-free fallback that still
follows the persona schema in docs/DESIGN.md).
"""
from __future__ import annotations

import colorsys
import hashlib
import random
import threading
import time

from .model import DONE, ERROR, IDLE, SLEEPING, WAITING, WORKING, Agent

# ---------------------------------------------------------------- fallback persona

_NAMES = ["Mochi", "Tofu", "Boba", "Pudding", "Sesame", "Dango", "Yuzu", "Miso", "Nori",
          "Peanut", "Biscuit", "Maple", "Kiwi", "Taro", "Matcha", "Pocky", "Udon", "Latte",
          "Sushi", "Bean", "Cocoa", "Muffin", "Ramune", "Onigiri"]
_SPECIES = ["cat", "cat", "cat", "fox", "bunny", "bear", "panda", "frog", "penguin", "hamster"]
_ROLES = {  # role -> (hat, prop, keywords)
    "research": ("detective", "magnifier", ("research", "find", "investigate", "survey", "调研", "研究", "查")),
    "coding": ("hardhat", "laptop", ("implement", "code", "fix", "refactor", "bug", "build", "写代码", "实现", "修复")),
    "design": ("beret", "brush", ("design", "ui", "css", "style", "设计", "界面")),
    "testing": ("goggles", "bugnet", ("test", "pytest", "qa", "测试")),
    "writing": ("none", "quill", ("write", "doc", "readme", "blog", "文档", "写作")),
    "data": ("visor", "chart", ("data", "sql", "analy", "chart", "数据", "分析")),
    "devops": ("headset", "wrench", ("deploy", "docker", "ci", "server", "launchd", "部署", "运维")),
    "planning": ("crown", "clipboard", ("plan", "roadmap", "organize", "计划", "规划")),
}
_OUTFITS = ["none", "scarf", "hoodie", "overalls", "cape", "bowtie", "apron", "sweater"]
_PATTERNS = ["solid", "tabby", "calico", "tuxedo", "socks", "spots"]
_EYES = ["round", "sparkle", "sleepy", "happy", "dot"]
_MOUTHS = ["cat", "smile", "o", "tongue"]


def _hex(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def hue_of(hex_color: str) -> float | None:
    """Hue in degrees of a ``#rrggbb`` colour (None if unparsable)."""
    try:
        s = hex_color.lstrip("#")
        r, g, b = (int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return None
    h, _l, _s = colorsys.rgb_to_hls(r, g, b)
    return h * 360.0


def tiny_persona(prompt: str, parent: dict | None = None, taken_hues=None) -> dict:
    """Deterministic, dependency-free persona following the DESIGN.md schema."""
    seed = int(hashlib.sha256((prompt or "neko").encode("utf-8")).hexdigest(), 16)
    rnd = random.Random(seed)
    low = (prompt or "").lower()
    role = "chat"
    for r, (_h, _p, kws) in _ROLES.items():
        if any(k in low for k in kws):
            role = r
            break
    hat, prop = (_ROLES[role][0], _ROLES[role][1]) if role in _ROLES else (
        rnd.choice(["none", "beanie", "flower", "bow", "wizard", "chef"]), "none")
    hue = rnd.uniform(0, 360)
    taken = [h for h in (taken_hues or []) if isinstance(h, (int, float))]
    for _ in range(12):  # golden-angle rotation away from hues already on screen
        if all(min(abs(hue - t) % 360, 360 - abs(hue - t) % 360) > 22 for t in taken):
            break
        hue = (hue + 137.508) % 360
    fur = _hex(hue, rnd.uniform(0.45, 0.7), rnd.uniform(0.78, 0.86))
    accent = _hex(hue + 150 + rnd.uniform(-30, 30), 0.7, 0.72)
    if parent:  # kittens take after their parent
        fur = parent.get("fur", fur)
        accent = parent.get("accent", accent)
    return {
        "v": 1, "emb_version": "tiny-fallback",
        "name": _NAMES[seed % len(_NAMES)],
        "species": (parent or {}).get("species") or rnd.choice(_SPECIES),
        "role": role, "hat": hat, "prop": prop,
        "outfit": rnd.choice(_OUTFITS),
        "fur": fur, "fur2": _hex(hue, 0.5, 0.95), "pattern": rnd.choice(_PATTERNS),
        "accent": accent, "eyes": rnd.choice(_EYES), "eye_color": "#3b2a2a",
        "mouth": rnd.choice(_MOUTHS), "blush": rnd.random() < 0.8,
        "ear_size": round(rnd.uniform(0.8, 1.2), 2), "chubby": round(rnd.uniform(0.9, 1.15), 2),
        "tail": round(rnd.uniform(0.7, 1.3), 2), "bounce": round(rnd.random(), 2),
        "speed": round(rnd.uniform(0.8, 1.2), 2),
    }


# ---------------------------------------------------------------- demo world

_PROMPTS = [
    ("claude", "neko-agent-monitor", "Research how other desktop pet apps map agent state to animations"),
    ("claude", "webshop", "Fix the flaky checkout test and refactor the cart reducer"),
    ("codex", "dataviz", "Analyze last month's sales data and chart the weekly trend"),
    ("claude", "blog", "写一篇关于本地嵌入模型的博客文章"),
    ("codex", "infra", "Deploy the staging server with docker compose and check the CI pipeline"),
    ("openai-sdk", "triage-bot", "Plan the Q3 roadmap and organize the backlog"),
    ("claude", "portfolio", "Design a cute pastel landing page with CSS animations"),
    ("codex", "api", "Write pytest tests for the auth module"),
]
_SUBTASKS = ["Search the codebase for state machine code", "Read the three.js docs on toon shading",
             "Run the unit tests and report failures", "调研 Electron 透明窗口"]
_TOOLS = [("Edit", "Edit: src/app.py"), ("Read", "Read: README.md"), ("Bash", "Bash: npm test"),
          ("Grep", "Grep: 'TODO'"), ("Write", "Write: docs/notes.md"), ("WebSearch", "WebSearch: toon shader")]
_MODELS = {"claude": ("claude-opus-4-5", 200_000), "codex": ("gpt-5-codex", 272_000),
           "openai-sdk": ("gpt-5", 400_000)}


class DemoWorld:
    """Fake agents whose state, tokens and cost drift over time."""

    def __init__(self, n: int = 5, seed: int | None = None, max_agents: int = 7):
        self.rnd = random.Random(seed)
        self.lock = threading.Lock()
        self.max_agents = max_agents
        self.agents: dict[str, Agent] = {}
        self._done_at: dict[str, float] = {}
        self._next_prompt = 0
        self._last_tick = time.time()
        for _ in range(n):
            self._spawn()
        # make sure the first frame shows the interesting states
        states = [WORKING, WAITING, IDLE, SLEEPING, ERROR]
        for a, st in zip([a for a in self.agents.values() if a.kind == "session"], states):
            self._set_state(a, st)
        parent = next(iter(self.agents.values()))
        self._spawn_sub(parent)

    # -- helpers
    def _spawn(self) -> Agent:
        src, proj, prompt = _PROMPTS[self._next_prompt % len(_PROMPTS)]
        self._next_prompt += 1
        sid = "demo-%04x" % self.rnd.getrandbits(16)
        model, limit = _MODELS[src]
        now = time.time()
        a = Agent(id=f"{src}:{sid}", source=src, kind="run" if src == "openai-sdk" else "session",
                  pid=40000 + self.rnd.randint(0, 9999), cwd=f"~/Workspace/{proj}", project=proj,
                  title=prompt[:40], creation_prompt=prompt, last_prompt=prompt, model=model,
                  started_at=now - self.rnd.randint(30, 3600), last_activity=now,
                  context_tokens=self.rnd.randint(8_000, 60_000), context_limit=limit,
                  progress_total=self.rnd.choice([0, 3, 5, 6]))
        a.tokens_in = a.context_tokens * self.rnd.randint(2, 8)
        a.tokens_out = a.tokens_in // 20
        a.cost_usd = round(a.tokens_in / 1e6 * 3 + a.tokens_out / 1e6 * 15, 4)
        self._set_state(a, WORKING)
        self.agents[a.id] = a
        return a

    def _spawn_sub(self, parent: Agent) -> Agent:
        task = self.rnd.choice(_SUBTASKS)
        a = Agent(id=f"{parent.id}:sub-{self.rnd.getrandbits(16):04x}", source=parent.source,
                  kind="subagent", parent_id=parent.id, cwd=parent.cwd, project=parent.project,
                  title=task[:40], creation_prompt=task, last_prompt=task, model=parent.model,
                  started_at=time.time(), last_activity=time.time(),
                  context_tokens=self.rnd.randint(4_000, 20_000), context_limit=parent.context_limit)
        self._set_state(a, WORKING)
        self.agents[a.id] = a
        return a

    def _set_state(self, a: Agent, st: str) -> None:
        a.state = st
        a.flags = []
        if st == WORKING:
            tool, detail = self.rnd.choice(_TOOLS)
            a.last_tool, a.state_detail = tool, detail
            a.tool_count += 1
        elif st == WAITING:
            a.state_detail = self.rnd.choice(["permission: Bash rm -rf build/", "question: which color scheme?",
                                              "permission: Edit package.json"])
        elif st == IDLE:
            a.state_detail = "turn finished"
            a.last_text = "All done! Anything else? (=^･ω･^=)"
        elif st == SLEEPING:
            a.state_detail = "quiet for 12 min"
        elif st == ERROR:
            a.state_detail = "API error: overloaded"
            a.flags = [{"id": "loop", "level": "bad", "text": "same tool call repeated 6×"}]
        elif st == DONE:
            a.state_detail = "finished"
        if a.context_pct > 0.8:
            a.flags.append({"id": "ctx", "level": "warn", "text": "context %d%% full" % int(a.context_pct * 100)})

    # -- public
    def tick(self) -> None:
        with self.lock:
            now = time.time()
            dt = max(0.0, min(10.0, now - self._last_tick))
            self._last_tick = now
            for aid, t in list(self._done_at.items()):
                if now - t > 12:
                    self.agents.pop(aid, None)
                    self._done_at.pop(aid, None)
            recent = 0.0
            for a in list(self.agents.values()):
                if a.state == DONE:
                    continue
                if a.state == WORKING:
                    grow = int(self.rnd.uniform(200, 2500) * dt)
                    a.context_tokens = min(a.context_limit, a.context_tokens + grow)
                    a.tokens_in += grow * 3
                    a.tokens_out += grow // 8
                    inc = grow * 3 / 1e6 * 3 + grow / 8 / 1e6 * 15
                    a.cost_usd = round(a.cost_usd + inc, 4)
                    a.cost_10m = round(min(a.cost_usd, a.cost_10m * 0.995 + inc), 4)
                    a.last_activity = now
                    if a.progress_total and self.rnd.random() < 0.05 * dt:
                        a.progress_done = min(a.progress_total, a.progress_done + 1)
                else:
                    a.cost_10m = round(a.cost_10m * 0.98, 4)
                recent += a.cost_10m
                if self.rnd.random() < 0.12 * dt:  # state change
                    if a.kind == "subagent":
                        nxt = self.rnd.choice([WORKING, WORKING, DONE])
                    else:
                        nxt = self.rnd.choices([WORKING, WAITING, IDLE, SLEEPING, ERROR],
                                               weights=[5, 2, 3, 1, 1])[0]
                    self._set_state(a, nxt)
                    if nxt == DONE:
                        self._done_at[a.id] = now
                elif a.state == WORKING and self.rnd.random() < 0.3 * dt:
                    self._set_state(a, WORKING)  # new tool call
            sessions = [a for a in self.agents.values() if a.kind != "subagent" and a.state != DONE]
            if len(self.agents) < self.max_agents and self.rnd.random() < 0.03 * dt:
                if sessions and self.rnd.random() < 0.5:
                    self._spawn_sub(self.rnd.choice(sessions))
                else:
                    self._spawn()
            elif sessions and len(sessions) > 3 and self.rnd.random() < 0.01 * dt:
                victim = self.rnd.choice(sessions)
                self._set_state(victim, DONE)
                self._done_at[victim.id] = now

    def collect(self) -> list[Agent]:
        self.tick()
        with self.lock:
            # return copies so the server can attach personas freely
            return [Agent(**{k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                             for k, v in a.__dict__.items()}) for a in self.agents.values()]
