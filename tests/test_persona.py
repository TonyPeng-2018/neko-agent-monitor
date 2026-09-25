"""Tests for neko/embed.py + neko/persona.py.

Everything runs offline on the pure-python hashing embedder (NEKO_EMBED=hash).
Tests marked with the ``model`` fixture additionally exercise model2vec when it is
installed and its weights are already cached; otherwise they are skipped.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import sys

import pytest

from neko import embed as E
from neko import persona as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROMPTS = [
    "design and implement neko agent monitor", "调研类似产品", "寻找3D模型", "fix failing tests",
    "write the README", "deploy to production", "analyze the csv data", "plan the next sprint",
    "hello", "", "   ", "🐱🐱🐱", "x" * 5000, "修复登录页面的bug", "设计一个可爱的图标",
    "Refactor the AUTH module; then run `pytest -q` && push!", "日本語のテキストも大丈夫？",
    "research similar products", "写使用文档", "部署到服务器", "分析销售数据",
]

HEX = re.compile(r"^#[0-9a-f]{6}$")
RANGES = {"ear_size": (0.8, 1.2), "chubby": (0.9, 1.15), "tail": (0.7, 1.3),
          "bounce": (0.0, 1.0), "speed": (0.8, 1.2)}
KEYS = {"v", "emb_version", "name", "species", "role", "hat", "prop", "outfit", "fur", "fur2",
        "pattern", "accent", "eyes", "eye_color", "mouth", "blush", *RANGES}


def _use(monkeypatch, mode: str | None):
    if mode is None:
        monkeypatch.delenv("NEKO_EMBED", raising=False)
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")        # never hit the network in tests
    else:
        monkeypatch.setenv("NEKO_EMBED", mode)
    E._reset_for_tests()


@pytest.fixture
def hashed(monkeypatch):
    _use(monkeypatch, "hash")
    assert E.emb_version() == E.HASH_VERSION
    yield
    E._reset_for_tests()


@pytest.fixture
def model(monkeypatch):
    _use(monkeypatch, None)
    if E.emb_version() == E.HASH_VERSION:
        E._reset_for_tests()
        pytest.skip("model2vec or its cached weights not available")
    yield
    E._reset_for_tests()


@pytest.fixture(params=["hash", "model"])
def backend(request):
    yield request.getfixturevalue("hashed" if request.param == "hash" else "model")


def assert_valid(p: dict):
    assert set(p) == KEYS, set(p) ^ KEYS
    assert p["v"] == 1 and isinstance(p["emb_version"], str) and p["emb_version"]
    assert isinstance(p["name"], str) and p["name"] in P.NAMES
    assert p["species"] in P.SPECIES
    assert p["role"] in P.ROLES
    assert p["hat"] in P.HATS
    assert p["prop"] in P.PROPS
    assert p["outfit"] in P.OUTFITS
    assert p["pattern"] in P.PATTERNS
    assert p["eyes"] in P.EYES
    assert p["mouth"] in P.MOUTHS
    for k in ("fur", "fur2", "accent", "eye_color"):
        assert HEX.match(p[k]), (k, p[k])
    assert isinstance(p["blush"], bool)
    for k, (lo, hi) in RANGES.items():
        assert isinstance(p[k], float) and lo <= p[k] <= hi, (k, p[k])
    json.dumps(p)                     # must be JSON-serialisable as-is


# ── embed ──────────────────────────────────────────────────────────────────

def test_embed_unit_vectors(backend):
    vs = E.embed(["hello world", "调研类似产品", "", "🐱", "x" * 10000])
    assert len(vs) == 5 and all(len(v) == E.DIM for v in vs)
    for i in (0, 1, 4):
        assert math.isclose(sum(x * x for x in vs[i]), 1.0, rel_tol=1e-6)
    assert not any(vs[2])             # empty text → zero vector
    assert E.embed([]) == []


def test_hash_embed_is_process_independent(hashed):
    """No reliance on Python's randomised str hash: same vector in a fresh process."""
    code = ("import os,json; os.environ['NEKO_EMBED']='hash';"
            "from neko.embed import embed; print(json.dumps(embed(['调研类似产品 fix tests'])[0]))")
    env = dict(os.environ, PYTHONHASHSEED="12345", PYTHONPATH=ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == pytest.approx(E.embed(["调研类似产品 fix tests"])[0])


# ── persona ────────────────────────────────────────────────────────────────

def test_schema_valid_for_many_prompts(backend):
    for prompt in PROMPTS:
        assert_valid(P.persona_for(prompt))
    assert_valid(P.persona_for(None))          # tolerated, treated as ""


def test_empty_prompt(hashed):
    p = P.persona_for("")
    assert_valid(p)
    assert p["role"] == "chat" and p["species"] == "cat"


def test_deterministic(backend):
    for prompt in PROMPTS[:8]:
        assert P.persona_for(prompt) == P.persona_for(prompt)
    assert P.persona_for("x", taken_hues=[10.0]) == P.persona_for("x", taken_hues=[10.0])


def test_roles_hash(hashed):
    expect = {
        "fix failing tests": "testing", "write unit tests for the parser": "testing",
        "write the README": "writing", "deploy to production": "devops",
        "analyze the csv data": "data", "research similar products": "research",
        "调研类似产品": "research", "部署到服务器": "devops", "分析数据": "data",
        "设计用户界面": "design", "hello": "chat",
    }
    got = {k: P.persona_for(k)["role"] for k in expect}
    assert got == expect


def test_similar_prompts_share_role_hash(hashed):
    pairs = [("fix failing tests", "fix the failing unit tests"),
             ("deploy to production", "deploying the app to production"),
             ("分析销售数据", "分析用户数据")]
    for a, b in pairs:
        assert P.persona_for(a)["role"] == P.persona_for(b)["role"], (a, b)


def test_similar_prompts_share_role_model(model):
    """EN↔ZH alignment only exists with the real model."""
    pairs = [("research similar products", "调研类似产品"),
             ("fix failing tests", "修复失败的测试"),
             ("write the README", "写使用文档"),
             ("deploy to production", "部署到服务器"),
             ("analyze the csv data", "分析销售数据")]
    for a, b in pairs:
        ra, rb = P.persona_for(a)["role"], P.persona_for(b)["role"]
        assert ra == rb, (a, ra, b, rb)
    assert P.persona_for("寻找3D模型")["role"] == "research"


def test_role_sets_prop(backend):
    for prompt in PROMPTS:
        p = P.persona_for(prompt)
        assert p["prop"] == P.ROLE_LOOK[p["role"]][2]
        assert p["hat"] in P.ROLE_LOOK[p["role"]][0]


def test_diverse_hues(backend):
    prompts = ["design and implement neko agent monitor", "调研类似产品", "寻找3D模型", "fix failing tests",
               "write the README", "deploy to production", "analyze the csv data", "plan the next sprint",
               "hello", "refactor the auth module", "设计一个可爱的图标", "investigate memory leak",
               "写一篇博客文章", "optimize sql queries", "scrape product prices", "draw a pixel art cat"]
    furs = [P.persona_for(p)["fur"] for p in prompts]
    assert len(set(furs)) >= 12
    buckets = {int(P.hue_of(f) // 30) for f in furs}
    assert len(buckets) >= 5, buckets


def test_hue_collision_avoidance(backend):
    taken: list[float] = []
    for prompt in ["fix tests", "fix tests", "fix tests", "write docs", "deploy", "hello", "调研", "画图"]:
        p = P.persona_for(prompt, taken_hues=taken)
        h = P.hue_of(p["fur"])
        assert all(P._hue_dist(h, t) >= P.HUE_MIN_GAP for t in taken), (prompt, h, taken)
        taken.append(h)


def test_species_mostly_cat(backend):
    import collections
    words = ("fix add write test deploy analyze design refactor plan research build docs data "
             "chart server bug ui icon api cli 修复 设计 数据 文档 部署 测试 调研 计划 代码 模型").split()
    c = collections.Counter()
    for i in range(240):
        prompt = " ".join(words[(i * 7 + j * 13) % len(words)] for j in range(2 + i % 4)) + f" #{i}"
        c[P.persona_for(prompt)["species"]] += 1
    assert c.most_common(1)[0][0] == "cat", c
    assert len(c) >= 6, c


def test_subagent_blending(backend):
    parent = P.persona_for("design and implement neko agent monitor")

    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(P._rgb(a), P._rgb(b))) ** 0.5

    kept = 0
    tasks = [f"subtask {i}: {w}" for i, w in enumerate(
        ["research similar products", "find 3D models", "write tests", "fix lint", "write docs",
         "调研类似产品", "部署", "analyze logs", "draw icons", "plan", "refactor", "review the diff",
         "benchmark", "translate", "clean data", "summarize", "hello", "check CI", "css tweaks", "profile"])]
    for task in tasks:
        own = P.persona_for(task)
        kid = P.persona_for(task, parent=parent)
        assert_valid(kid)
        assert kid["role"] == own["role"] and kid["name"] == own["name"]
        # 60 % toward the parent → the kid sits at 40 % of the own→parent distance.
        for k in ("fur", "accent"):
            assert dist(kid[k], parent[k]) <= 0.4 * dist(own[k], parent[k]) + 0.01
        kept += kid["species"] == parent["species"]
    assert 0.45 <= kept / len(tasks) <= 0.95, kept


def test_store_round_trip(hashed, tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_HOME", str(tmp_path / "home"))
    secret = "调研类似产品 SECRET-PROMPT-TEXT 42"
    store = P.PersonaStore()
    assert store.path == str(tmp_path / "home" / "personas.json")
    p = store.get_or_create(secret)
    assert_valid(p)
    assert p == P.persona_for(secret)

    raw = open(store.path, encoding="utf-8").read()
    assert "SECRET" not in raw and "调研" not in raw
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert not [f for f in os.listdir(tmp_path / "home") if f.endswith(".tmp")]

    again = P.PersonaStore()                     # fresh instance reads it back
    assert again.get(secret) == p
    assert again.get_or_create(secret) == p

    # Colliding with a live agent → rotated variant, stored entry unchanged.
    rotated = again.get_or_create(secret, taken_hues=[P.hue_of(p["fur"])])
    assert rotated["fur"] != p["fur"]
    assert P.PersonaStore().get(secret) == p

    # Subagent personas are keyed separately.
    kid = again.get_or_create("child task", parent=p)
    assert P.PersonaStore().get("child task", parent=p) == kid
    assert P.PersonaStore().get("child task") is None


def test_store_survives_corrupt_file(hashed, tmp_path):
    path = tmp_path / "personas.json"
    path.write_text("{not json")
    store = P.PersonaStore(str(path))
    assert_valid(store.get_or_create("hello"))
    assert json.loads(path.read_text())
