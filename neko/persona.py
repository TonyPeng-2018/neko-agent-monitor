"""Creation prompt → persona (the look + name of an agent's chibi).

Two kinds of traits (see docs/DESIGN.md "Persona"):

* **Semantic** traits come from what the prompt is *about*: cosine similarity of
  the prompt embedding to anchor centroids (EN + ZH anchor texts per class),
  turned into probabilities with ``softmax(sim / 0.05)``. The role is the argmax
  (so "research similar products" and "调研类似产品" agree); the species is sampled
  from its softmax (mixed with a cat prior) using an identity bit.
* **Identity** traits (colours, face, body numbers) come from a fixed seeded
  random projection of the embedding (16 × 256, seed 42) mapped to ~uniform
  u ∈ (0, 1) through the normal CDF, calibrated per embedder on a built-in prompt
  list. Similar prompts → similar looks; everything is deterministic.

The returned dict is exactly the persona schema in DESIGN.md. ``PersonaStore``
persists traits keyed by sha256(prompt) + embedder version — never the prompt.
Pure python; numpy is not needed (the model2vec backend brings its own).
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import math
import os
import random
import tempfile
import threading
import time

from . import embed as _emb

SCHEMA_V = 1

# ── enums (the contract with web/) ─────────────────────────────────────────

SPECIES = ("cat", "fox", "bunny", "bear", "panda", "frog", "penguin", "hamster")
ROLES = ("research", "coding", "design", "testing", "writing", "data", "devops", "planning", "chat")
HATS = ("none", "detective", "hardhat", "beret", "goggles", "crown", "headset", "visor",
        "beanie", "flower", "bow", "wizard", "chef")
PROPS = ("none", "magnifier", "laptop", "brush", "bugnet", "quill", "chart", "wrench", "clipboard")
OUTFITS = ("none", "scarf", "hoodie", "overalls", "cape", "bowtie", "apron", "sweater")
PATTERNS = ("solid", "tabby", "calico", "tuxedo", "socks", "spots")
EYES = ("round", "sparkle", "sleepy", "happy", "dot")
MOUTHS = ("cat", "smile", "o", "tongue")

# ── semantic anchors (EN + ZH) ─────────────────────────────────────────────

ROLE_ANCHORS: dict[str, list[str]] = {
    "research": [
        "research similar products", "investigate and survey existing solutions",
        "find and compare alternatives", "search the web and look up information",
        "explore the codebase to understand how it works", "read the docs and summarize findings",
        "调研类似产品", "调查研究现有方案", "搜索资料并总结", "寻找相关资源", "查找信息", "了解竞品",
    ],
    "coding": [
        "implement the feature", "write the code for the function", "refactor the module",
        "fix the bug in the parser", "build the app and add an API endpoint",
        "design and implement the backend", "add a new class and wire it up",
        "实现这个功能", "写代码", "重构模块", "修复程序错误", "开发后端接口", "编程实现",
    ],
    "design": [
        "design the user interface", "make the UI look beautiful", "create a logo and icons",
        "pick colors, fonts and layout", "draw an illustration and 3D character art",
        "improve the CSS styling and animation",
        "设计用户界面", "美化界面", "设计图标和标志", "配色和排版", "画插画", "三维角色造型",
    ],
    "testing": [
        "fix failing tests", "write unit tests", "run the test suite and check coverage",
        "debug the flaky test", "QA and verify the behaviour", "reproduce the bug with a test case",
        "修复失败的测试", "编写单元测试", "运行测试", "测试覆盖率", "验证功能是否正确", "排查测试问题",
    ],
    "writing": [
        "write the README", "write documentation", "draft a blog post", "edit the article",
        "write release notes and changelog", "translate and proofread the text",
        "写文档", "撰写说明文档", "写一篇文章", "润色文字", "翻译文本", "写博客",
    ],
    "data": [
        "analyze the csv data", "make charts and statistics", "query the database with SQL",
        "clean the dataset", "train a machine learning model", "build a dashboard of metrics",
        "分析数据", "数据统计和图表", "查询数据库", "清洗数据集", "训练模型", "数据分析报表",
    ],
    "devops": [
        "deploy to production", "set up CI/CD pipeline", "configure docker and kubernetes",
        "fix the server and infrastructure", "monitor logs and uptime", "install and configure launchd service",
        "部署到生产环境", "配置服务器", "搭建持续集成", "运维监控", "配置容器", "安装环境",
    ],
    "planning": [
        "plan the project", "make a roadmap and task list", "break down the work into steps",
        "coordinate the team and assign tasks", "write a design doc and architecture plan",
        "prioritize the backlog", "plan the next sprint and milestones", "schedule and estimate",
        "制定计划", "项目规划", "拆分任务", "安排工作", "制定路线图", "协调团队分工", "排期和里程碑",
    ],
    "chat": [
        "hello", "hi, how are you", "thanks", "tell me a joke", "what do you think?",
        "just chatting", "quick question",
        "你好", "谢谢", "聊聊天", "讲个笑话", "你觉得呢",
    ],
}

# Species "personalities": cat is the default all-rounder (and gets a prior bias).
SPECIES_ANCHORS: dict[str, list[str]] = {
    "cat": ["curious general helper", "code and help with anything", "好奇的通用助手", "写代码帮忙"],
    "fox": ["clever investigation and research", "security audit and debugging mysteries",
            "聪明的调查研究", "安全审计与疑难排查"],
    "bunny": ["quick small fix", "fast little tweak", "快速小修改", "简单快速的任务"],
    "bear": ["big heavy refactor of the whole codebase", "large migration and rewrite",
             "大规模重构", "大型迁移重写"],
    "panda": ["calm relaxed chat and review", "gentle planning and writing", "轻松聊天和审阅", "温和的规划写作"],
    "frog": ["testing and catching bugs", "experiments and science", "测试和抓虫", "实验和科学"],
    "penguin": ["linux servers and devops", "cloud deploy and infrastructure", "服务器运维", "云端部署"],
    "hamster": ["collect and gather data", "scrape, download and store files", "收集整理数据", "抓取下载存储"],
}
CAT_PRIOR = 0.25         # probability mass reserved for cat before the softmax share
TEMP = 0.05              # softmax temperature on cosine similarity

# role → (hat choices, weights), prop, preferred outfit
ROLE_LOOK: dict[str, tuple[tuple[str, ...], tuple[float, ...], str, str]] = {
    "research": (("detective", "wizard", "beanie"), (0.6, 0.2, 0.2), "magnifier", "cape"),
    "coding":   (("hardhat", "beanie", "wizard"), (0.5, 0.3, 0.2), "laptop", "hoodie"),
    "design":   (("beret", "flower", "bow"), (0.6, 0.2, 0.2), "brush", "apron"),
    "testing":  (("goggles", "hardhat", "detective"), (0.6, 0.25, 0.15), "bugnet", "overalls"),
    "writing":  (("beret", "bow", "none"), (0.4, 0.3, 0.3), "quill", "scarf"),
    "data":     (("visor", "goggles", "wizard"), (0.6, 0.2, 0.2), "chart", "sweater"),
    "devops":   (("headset", "hardhat", "beanie"), (0.6, 0.25, 0.15), "wrench", "overalls"),
    "planning": (("crown", "bow", "visor"), (0.6, 0.2, 0.2), "clipboard", "bowtie"),
    "chat":     (("none", "flower", "bow", "chef", "beanie"), (0.3, 0.2, 0.2, 0.15, 0.15), "none", "sweater"),
}

NAMES = (
    "Mochi", "Tofu", "Boba", "Nori", "Miso", "Yuzu", "Kiki", "Pudding", "Dango", "Sushi",
    "Onigiri", "Matcha", "Taiyaki", "Mango", "Peach", "Kiwi", "Biscuit", "Muffin", "Cocoa",
    "Latte", "Waffle", "Pancake", "Maple", "Honey", "Caramel", "Cookie", "Sesame", "Azuki",
    "Kinako", "Ramune", "Pocky", "Sakura", "Momo", "Ume", "Kuri", "Hana", "Sora", "Pico",
    "Popo", "Nana", "Lulu", "Bubu", "Pom", "Puff", "Marshmallow", "Sprinkle", "Jelly",
    "Tapioca", "Churro", "Crepe", "Bean", "Pebble", "Button", "Clover", "Tuanzi", "Xiaobao",
    "Doudou", "Nuomi", "Tangyuan", "Baozi", "Mantou", "Huahua", "Qiuqiu", "Mimi", "Niuniu",
    "Guagua", "Lele", "Taotao", "Youzi", "Mianhua", "Dumpling", "Wonton", "Pretzel", "Noodle",
)

# Soft "natural" coat colours used for ~25 % of agents (the rest are pastels).
NATURAL_FUR = ("#f3e3c7", "#cfcbd6", "#f2b27a", "#5d5763", "#fbf6ef", "#c9a184", "#e8d2b0")
EYE_COLORS = ("#3b2a2a", "#2d2a40", "#1f3a3a", "#4a2f1f", "#2a2f4a", "#3a1f33", "#2b3a22")

# Built-in prompts for calibrating the projection (mean/sigma per dimension).
_CALIB = (
    "design and implement neko agent monitor", "调研类似产品", "寻找3D模型", "fix failing tests",
    "write the README", "deploy to production", "analyze the csv data", "plan the next sprint",
    "hello", "refactor the parser module", "设计一个可爱的图标", "写一篇博客文章", "部署到服务器",
    "分析销售数据", "修复登录页面的bug", "add dark mode to the settings page", "translate docs to Chinese",
    "why is the build slow", "set up github actions ci", "compare vector databases",
    "draw a pixel art cat", "clean up the database schema", "write unit tests for the api",
    "summarize this paper", "benchmark the embedding model", "migrate from python 3.9 to 3.12",
    "create a landing page", "review my pull request", "organize my notes", "今天天气怎么样",
    "帮我写一个爬虫", "规划项目路线图", "给函数加上类型注解", "optimize sql queries",
    "make the animation bouncier", "investigate memory leak", "scrape product prices",
    "configure nginx reverse proxy", "write a poem about cats", "train a small classifier",
)

N_ID = 16
HUE_MIN_GAP = 20.0
GOLDEN = 137.5

_cache_lock = threading.Lock()
_cache: dict[str, dict] = {}     # emb_version → {"roles": {...}, "species": {...}, "mu": [...], "sd": [...]}


# ── small math helpers (pure python) ───────────────────────────────────────

def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _unit(v):
    n = math.sqrt(_dot(v, v))
    return [x / n for x in v] if n > 0 else list(v)


def _centroid(vecs):
    return _unit([sum(col) / len(vecs) for col in zip(*vecs)])


def _softmax(xs, temp=TEMP):
    m = max(xs)
    ex = [math.exp((x - m) / temp) for x in xs]
    s = sum(ex)
    return [e / s for e in ex]


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _pick(u: float, items, weights=None):
    """Choose from items with (optional) weights using a uniform u ∈ [0, 1)."""
    weights = weights or [1.0] * len(items)
    t = u * sum(weights)
    for it, w in zip(items, weights):
        t -= w
        if t < 0:
            return it
    return items[-1]


def _frac(x: float) -> float:
    return x - math.floor(x)


def _projection() -> list[list[float]]:
    rng = random.Random(42)
    return [[rng.gauss(0.0, 1.0) for _ in range(_emb.DIM)] for _ in range(N_ID)]


_R = _projection()


# ── colour helpers ─────────────────────────────────────────────────────────

def _hex(r: float, g: float, b: float) -> str:
    c = lambda x: max(0, min(255, int(round(x * 255))))
    return "#%02x%02x%02x" % (c(r), c(g), c(b))


def _rgb(hexs: str) -> tuple[float, float, float]:
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hsl_hex(h_deg: float, s: float, l: float) -> str:
    return _hex(*colorsys.hls_to_rgb((h_deg % 360) / 360, l, s))


def _hex_hsl(hexs: str) -> tuple[float, float, float]:
    h, l, s = colorsys.rgb_to_hls(*_rgb(hexs))
    return h * 360, s, l


def hue_of(hexs: str) -> float:
    """Hue in degrees of a '#rrggbb' colour (for taken_hues bookkeeping)."""
    return _hex_hsl(hexs)[0]


def _hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _mix(a: str, b: str, t: float) -> str:
    """Blend colour a toward b by t (0 → a, 1 → b) in RGB."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(*(x + (y - x) * t for x, y in zip(ra, rb)))


def _belly(fur: str) -> str:
    """fur2: lighter, less saturated belly/pattern colour of the same hue."""
    h, s, l = _hex_hsl(fur)
    return _hsl_hex(h, s * 0.55, min(0.95, max(l + 0.13, 0.84 if l > 0.5 else 0.62)))


# ── per-embedder cached state ──────────────────────────────────────────────

def _state() -> dict:
    ver = _emb.emb_version()
    st = _cache.get(ver)
    if st is not None:
        return st
    with _cache_lock:
        st = _cache.get(ver)
        if st is None:
            st = _build_state()
            _cache[ver] = st
    return st


def _build_state() -> dict:
    """Embed all anchors + calibration prompts once (one batched call)."""
    texts, spans = [], {}
    for group, anchors in (("r", ROLE_ANCHORS), ("s", SPECIES_ANCHORS)):
        for key, lst in anchors.items():
            spans[(group, key)] = (len(texts), len(texts) + len(lst))
            texts.extend(lst)
    c0 = len(texts)
    texts.extend(_CALIB)
    vecs = _emb.embed(texts)
    roles = {k: _centroid(vecs[a:b]) for (g, k), (a, b) in spans.items() if g == "r"}
    species = {k: _centroid(vecs[a:b]) for (g, k), (a, b) in spans.items() if g == "s"}
    # Calibrate each projected dimension so Φ((z - μ)/σ) is ~uniform over prompts.
    proj = [[_dot(row, v) for row in _R] for v in vecs[c0:]]
    mu = [sum(c) / len(c) for c in zip(*proj)]
    sd = [max(1e-6, math.sqrt(sum((x - m) ** 2 for x in c) / len(c))) for c, m in zip(zip(*proj), mu)]
    return {"roles": roles, "species": species, "mu": mu, "sd": sd}


# ── the persona ────────────────────────────────────────────────────────────

def _sha(prompt: str) -> bytes:
    return hashlib.sha256((prompt or "").encode("utf-8")).digest()


def _identity(vec, st) -> list[float]:
    """16 approximately-uniform numbers in (0, 1) from the embedding."""
    if not any(vec):          # empty prompt: middle-of-the-road look
        return [0.5] * N_ID
    return [min(0.9999, max(0.0001, _phi((_dot(row, vec) - m) / s)))
            for row, m, s in zip(_R, st["mu"], st["sd"])]


def _role(vec, st) -> str:
    if not any(vec):
        return "chat"
    names = list(st["roles"])
    sims = [_dot(vec, st["roles"][k]) for k in names]
    probs = _softmax(sims)
    return names[max(range(len(names)), key=probs.__getitem__)]


def _species(vec, st, u: float) -> str:
    names = list(st["species"])
    if not any(vec):
        return "cat"
    probs = _softmax([_dot(vec, st["species"][k]) for k in names])
    # Mix in a cat prior (embedder-independent) so cat stays the most common species.
    probs = [(1 - CAT_PRIOR) * p + (CAT_PRIOR if k == "cat" else 0.0) for k, p in zip(names, probs)]
    return _pick(u, names, probs)


def _fur(u: list[float]) -> str:
    if u[3] < 0.25:                                           # ~25 % natural coats
        return NATURAL_FUR[int(_frac(u[3] * 4 * 7.0) * len(NATURAL_FUR))]
    return _hsl_hex(u[0] * 360, 0.35 + 0.25 * u[1], 0.72 + 0.14 * u[2])


def _avoid_collision(fur: str, taken: list[float]) -> str:
    """Rotate hue by the golden angle until ≥ 20° from every taken hue (≤ 12 tries)."""
    if not taken:
        return fur
    h, s, l = _hex_hsl(fur)
    if all(_hue_dist(h, t) >= HUE_MIN_GAP for t in taken):
        return fur
    # Greys/creams barely change when rotated: switch them to a pastel of that hue.
    s = min(0.6, max(s, 0.35))
    l = min(0.86, max(l, 0.72))
    for _ in range(12):
        h = (h + GOLDEN) % 360
        if all(_hue_dist(h, t) >= HUE_MIN_GAP for t in taken):
            break
    return _hsl_hex(h, s, l)


def _accent(fur: str, u: float) -> str:
    """Clothes colour: complementary-ish, saturated pastel."""
    h, s, _ = _hex_hsl(fur)
    base = h if s > 0.15 else u * 360          # grey fur: any hue will do
    return _hsl_hex(base + 180 + (u - 0.5) * 80, 0.62 + 0.18 * _frac(u * 3), 0.68 + 0.08 * _frac(u * 5))


def persona_for(prompt: str, parent: dict | None = None,
                taken_hues: list[float] | None = None) -> dict:
    """Deterministic persona (DESIGN.md schema) for a creation prompt.

    parent:     the parent agent's persona, for subagents (kittens): fur/accent
                blended 60 % toward the parent's; species kept 70 % of the time.
    taken_hues: fur hues (degrees) of live agents, to keep characters distinct.
    """
    prompt = prompt or ""
    st = _state()
    vec = _emb.embed([prompt])[0]
    u = _identity(vec, st)
    digest = _sha(prompt)

    role = _role(vec, st)
    hats, hat_w, prop, pref_outfit = ROLE_LOOK[role]
    hat = _pick(u[14], hats, hat_w)
    uo = _frac(u[14] * 7.0)
    outfit = pref_outfit if uo < 0.5 else _pick((uo - 0.5) * 2, OUTFITS)

    fur = _avoid_collision(_fur(u), list(taken_hues or []))
    accent = _accent(fur, u[5])
    species = _species(vec, st, u[15])

    if parent:
        pf, pa = parent.get("fur"), parent.get("accent")
        if isinstance(pf, str) and pf.startswith("#") and len(pf) == 7:
            fur = _mix(fur, pf, 0.6)
        if isinstance(pa, str) and pa.startswith("#") and len(pa) == 7:
            accent = _mix(accent, pa, 0.6)
        if parent.get("species") in SPECIES and digest[1] / 256 < 0.7:
            species = parent["species"]

    return {
        "v": SCHEMA_V,
        "emb_version": _emb.emb_version(),
        "name": NAMES[int.from_bytes(digest[:4], "big") % len(NAMES)],
        "species": species,
        "role": role,
        "hat": hat,
        "prop": prop,
        "outfit": outfit,
        "fur": fur,
        "fur2": _belly(fur),
        "pattern": _pick(u[4], PATTERNS, (30, 20, 12, 13, 13, 12)),
        "accent": accent,
        "eyes": _pick(u[6], EYES, (30, 25, 10, 20, 15)),
        "eye_color": EYE_COLORS[min(len(EYE_COLORS) - 1, int(u[7] * len(EYE_COLORS)))],
        "mouth": _pick(u[8], MOUTHS, (45, 25, 15, 15)),
        "blush": _frac(u[8] * 10.0) < 0.9,                   # it's kawaii: mostly blushing
        "ear_size": round(0.8 + 0.4 * u[9], 3),
        "chubby": round(0.9 + 0.25 * u[10], 3),
        "tail": round(0.7 + 0.6 * u[11], 3),
        "bounce": round(u[12], 3),
        "speed": round(0.8 + 0.4 * u[13], 3),
    }


# ── persistence ────────────────────────────────────────────────────────────

def neko_home() -> str:
    return os.environ.get("NEKO_HOME") or os.path.join(os.path.expanduser("~"), ".neko")


class PersonaStore:
    """Traits cache at $NEKO_HOME/personas.json (default ~/.neko), mode 0600.

    Keyed by sha256(prompt) + emb_version (+ parent colours for subagents), so an
    agent keeps its look across daemon restarts. The prompt text is never stored.
    """

    MAX_ENTRIES = 5000

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(neko_home(), "personas.json")
        self._lock = threading.Lock()
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def key(prompt: str, parent: dict | None = None) -> str:
        k = hashlib.sha256((prompt or "").encode("utf-8")).hexdigest() + ":" + _emb.emb_version()
        if parent:
            k += ":p" + hashlib.sha256(
                f"{parent.get('fur')}{parent.get('accent')}{parent.get('species')}".encode()).hexdigest()[:12]
        return k

    def get(self, prompt: str, parent: dict | None = None) -> dict | None:
        e = self._data.get(self.key(prompt, parent))
        return dict(e["p"]) if isinstance(e, dict) and isinstance(e.get("p"), dict) else None

    def get_or_create(self, prompt: str, parent: dict | None = None,
                      taken_hues: list[float] | None = None) -> dict:
        """Cached persona; if its hue collides with a live agent, a rotated
        variant is returned for this session (the stored one is unchanged)."""
        cached = self.get(prompt, parent)
        taken = list(taken_hues or [])
        if cached is not None:
            h = hue_of(cached.get("fur", "#ffffff"))
            if parent or not taken or all(_hue_dist(h, t) >= HUE_MIN_GAP for t in taken):
                return cached
            return persona_for(prompt, parent, taken)
        p = persona_for(prompt, parent, taken)
        with self._lock:
            self._data[self.key(prompt, parent)] = {"t": int(time.time()), "p": p}
            if len(self._data) > self.MAX_ENTRIES:     # drop the oldest entries
                for k, _ in sorted(self._data.items(), key=lambda kv: kv[1].get("t", 0))[
                        : len(self._data) - self.MAX_ENTRIES]:
                    self._data.pop(k, None)
            self._save()
        return p

    def _save(self) -> None:
        """Atomic write (tmp file + rename), private to the user."""
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".personas.", suffix=".tmp", dir=d)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
