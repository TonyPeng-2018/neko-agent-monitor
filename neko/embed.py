"""Text → unit vector (dim 256), used to seed each agent's persona.

Two backends, chosen once per process:

* **model2vec** ``minishlab/potion-multilingual-128M`` (MIT, numpy only). Static
  token embeddings, EN↔ZH aligned, ~0.7 s load and ~0.04 ms per text. Optional:
  only used when ``model2vec`` imports and the weights can be found/downloaded.
* **hash** fallback: a pure-python hashing-trick embedding (word tokens, word
  char-trigrams, CJK char unigrams + bigrams, blake2b buckets and signs). No
  dependencies, deterministic across machines and Python versions, never raises.

Environment:
  NEKO_EMBED=hash         force the fallback
  NEKO_EMBED_MODEL=repo   override the model2vec repo id (or a local path)

``emb_version()`` names the backend actually in use; it is stored with every
persona so cached looks are invalidated when the embedder changes.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading

DIM = 256
DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
HASH_VERSION = "hash-v1"

# Only these files are needed by StaticModel; skips the ~500 MB onnx export.
_ALLOW = ["*.json", "*.safetensors", "vocab.txt", "tokenizer*"]

_lock = threading.Lock()
_backend: tuple | None = None   # ("model", StaticModel, version) | ("hash", None, version)
_models: dict = {}              # repo → loaded StaticModel (survives _reset_for_tests)


def _model_repo() -> str:
    return os.environ.get("NEKO_EMBED_MODEL", "").strip() or DEFAULT_MODEL


def _load_model2vec(repo: str):
    """Return a StaticModel, or None if model2vec/weights are unavailable."""
    try:
        from model2vec import StaticModel  # optional dependency
    except Exception:
        return None
    try:
        path = repo
        if not os.path.isdir(repo):
            from huggingface_hub import snapshot_download
            try:   # cache first: works offline and avoids a network round-trip
                path = snapshot_download(repo, allow_patterns=_ALLOW, local_files_only=True)
            except Exception:
                path = snapshot_download(repo, allow_patterns=_ALLOW)
        return StaticModel.from_pretrained(path)
    except Exception:
        return None


def _get_backend() -> tuple:
    """Pick and load the backend exactly once (thread-safe)."""
    global _backend, EMB_VERSION
    if _backend is not None:
        return _backend
    with _lock:
        if _backend is None:
            chosen = ("hash", None, HASH_VERSION)
            if os.environ.get("NEKO_EMBED", "").strip().lower() != "hash":
                repo = _model_repo()
                model = _models.get(repo) or _load_model2vec(repo)
                if model is not None:
                    _models[repo] = model
                if model is not None:
                    chosen = ("model", model, repo.rstrip("/").split("/")[-1])
            _backend = chosen
            EMB_VERSION = chosen[2]
    return _backend


def emb_version() -> str:
    """Name of the embedder in use (loads the backend if needed)."""
    return _get_backend()[2]


def _reset_for_tests() -> None:
    """Forget the chosen backend so env changes take effect (tests only)."""
    global _backend
    with _lock:
        _backend = None


# Best-effort guess without loading anything; refreshed once the backend loads.
# Prefer ``emb_version()`` when the exact value matters.
EMB_VERSION = HASH_VERSION if os.environ.get("NEKO_EMBED", "").strip().lower() == "hash" \
    else _model_repo().rstrip("/").split("/")[-1]


# ── hashing-trick fallback ─────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
# Tiny English stop list so glue words don't dominate short prompts.
_STOP = frozenset("a an the and or of to in on for with at by from is are be it this that "
                  "please can you could would we i me my our your do some".split())


def _stem(w: str) -> str:
    """Very light suffix stripping: tests→test, deploying→deploy, analyzed→analyz."""
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _features(text: str) -> list[tuple[str, float]]:
    text = text.lower()
    feats: list[tuple[str, float]] = []
    for w in _WORD.findall(text):
        if w in _STOP:
            continue
        s = _stem(w)
        feats.append(("w:" + s, 1.0))
        if len(s) >= 5:                          # char trigrams: typo/morphology tolerance
            p = "<" + s + ">"
            for i in range(len(p) - 2):
                feats.append(("t:" + p[i:i + 3], 0.25))
    for run in _CJK.findall(text):
        for ch in run:
            feats.append(("c:" + ch, 0.6))
        for i in range(len(run) - 1):
            feats.append(("b:" + run[i:i + 2], 1.0))
    return feats


def _hash_embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for feat, wt in _features(text or ""):
        h = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % DIM
        sign = 1.0 if h[4] & 1 else -1.0
        v[idx] += sign * wt
    return _normalize(v)


def _normalize(v) -> list[float]:
    v = [float(x) if math.isfinite(float(x)) else 0.0 for x in v]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else [0.0] * len(v)


def _fit_dim(v: list[float]) -> list[float]:
    """Pad/truncate a model vector to DIM (potion models are already 256-d)."""
    if len(v) == DIM:
        return v
    return (v + [0.0] * DIM)[:DIM]


# ── public API ─────────────────────────────────────────────────────────────

def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts → unit vectors of length DIM (zero vector for empty text).

    Never raises: any model failure falls back to the hashing embedding for
    that call (the version string still names the loaded model, so callers
    should treat such rare vectors as best-effort).
    """
    texts = ["" if t is None else str(t) for t in texts]
    if not texts:
        return []
    try:
        kind, model, _ = _get_backend()
    except Exception:
        kind, model = "hash", None
    if kind == "model":
        try:
            arr = model.encode(texts)
            out = []
            for t, row in zip(texts, arr.tolist()):
                out.append(_normalize(_fit_dim(row)) if t.strip() else [0.0] * DIM)
            return out
        except Exception:
            pass
    return [_hash_embed(t) for t in texts]
