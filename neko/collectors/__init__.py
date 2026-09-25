"""Collectors: turn local agent activity into :class:`neko.model.Agent` records.

Public interface (used by ``neko/server.py``):

* :func:`collect_all` → ``list[Agent]`` — Claude Code sessions + subagents, Codex CLI
  threads, OpenAI Agents SDK runs. Fast (incremental parsing, per-file caches),
  thread-safe, never raises.
* :func:`ingest_event` — a Claude/Codex hook payload (instant state refinement).
* :func:`ingest_sdk` — an Agents SDK trace/span export (see ``sdk_client.py``).

Agents that disappear (process exited, registry file gone) are re-emitted with state
``done`` for :data:`GONE_KEEP` seconds so the front end can play the goodbye.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Dict, List, Tuple

from ..model import DONE, Agent
from . import claude, codex, events, sdk

log = logging.getLogger("neko.collectors")

GONE_KEEP = 20.0

_collect_lock = threading.Lock()
_last: Dict[str, Agent] = {}                 # id -> last emitted agent
_gone: Dict[str, Tuple[float, Agent]] = {}   # id -> (vanished at, ghost)


def _safe(name, fn, now) -> List[Agent]:
    try:
        return list(fn(now))
    except Exception:
        log.exception("collector %s failed", name)
        return []


def collect_all() -> List[Agent]:
    """Every agent on this machine right now. Never raises."""
    try:
        with _collect_lock:
            now = time.time()
            agents = (_safe("claude", claude.collect, now) + _safe("codex", codex.collect, now)
                      + _safe("openai-sdk", sdk.collect, now))
            seen = {a.id for a in agents}
            # vanished since last time → done ghost (unless it already said goodbye)
            for aid, prev in _last.items():
                if aid not in seen and aid not in _gone and prev.state != DONE \
                        and prev.source != "openai-sdk":
                    g = copy.deepcopy(prev)
                    g.state, g.state_detail = DONE, "exited"
                    g.pid = None
                    _gone[aid] = (now, g)
            for aid in [k for k, (t, _) in _gone.items() if k in seen or now - t > GONE_KEEP]:
                _gone.pop(aid, None)
            ghosts = [g for _, g in _gone.values()]
            # a vanished session's children leave with it
            gone_ids = {g.id for g in ghosts}
            for a in agents:
                if a.parent_id in gone_ids and a.state != DONE:
                    a.state, a.state_detail = DONE, "parent exited"
            _last.clear()
            _last.update({a.id: a for a in agents})
            _last.update({g.id: g for g in ghosts})
            return agents + ghosts
    except Exception:
        log.exception("collect_all failed")
        return []


def ingest_event(payload: dict) -> None:
    """A Claude Code / Codex hook payload. Never raises."""
    try:
        events.ingest(payload)
    except Exception:
        log.exception("ingest_event failed")


def ingest_sdk(payload) -> None:
    """An OpenAI Agents SDK trace/span export (or a batch of them). Never raises."""
    try:
        sdk.ingest(payload)
    except Exception:
        log.exception("ingest_sdk failed")


def reset() -> None:
    """Forget all caches and pushed state (tests)."""
    with _collect_lock:
        _last.clear()
        _gone.clear()
        events.reset()
        sdk.reset()
        with claude._LOCK:
            claude._CACHE.clear()
        with codex._LOCK:
            codex._CACHE.clear()


__all__ = ["collect_all", "ingest_event", "ingest_sdk", "reset"]
