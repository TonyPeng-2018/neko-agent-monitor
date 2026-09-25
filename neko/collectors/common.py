"""Helpers shared by the collectors: time parsing, clipping, pricing, context windows,
and :class:`Activity` — the per-transcript bookkeeping the flags are computed from.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from datetime import datetime
from typing import Optional

# ------------------------------------------------------------------ small utils


def ts_of(s) -> Optional[float]:
    """ISO-8601 string / epoch number → epoch seconds."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        v = float(s)
        return v / 1000.0 if v > 1e11 else v
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def clip(s, n: int = 160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def project_of(cwd: str) -> str:
    if not cwd:
        return ""
    cwd = cwd.rstrip("/") or "/"
    if cwd == os.path.expanduser("~").rstrip("/"):
        return "~"
    return os.path.basename(cwd) or cwd


def tool_summary(name: str, inp) -> str:
    if not isinstance(inp, dict):
        return clip(inp, 120) if isinstance(inp, str) else ""
    for key in ("description", "command", "cmd", "file_path", "path", "pattern", "query",
                "url", "prompt", "subject", "skill"):
        v = inp.get(key)
        if v:
            if isinstance(v, list):
                v = " ".join(str(x) for x in v)
            return clip(v, 120)
    return ""


def tool_detail(name: str, summ: str) -> str:
    return f"{name}: {summ}" if summ else (name or "")


# ------------------------------------------------------------------ pricing
# API list prices, $ per 1M tokens: (input, output, cache-read, cache-write).
# Claude values checked against the CLI's cost-state records
# (Claude Code caches with the 1h TTL so write = 2x input). Ordered: specific first.
CLAUDE_PRICES = (
    ("claude-fable", (10.0, 50.0, 0.25, 20.0)),
    ("claude-mythos", (10.0, 50.0, 0.25, 20.0)),
    ("claude-opus-5-5", (4.0, 20.0, 0.20, 8.0)),
    ("claude-opus", (5.0, 25.0, 0.50, 10.0)),
    ("claude-sonnet", (2.0, 10.0, 0.20, 4.0)),
    ("claude-haiku", (1.0, 5.0, 0.10, 2.0)),
)

# OpenAI prices — APPROXIMATE, rough list-price guesses used only to show a burn rate
# (input, output, cached-input). Unknown non-gpt models → 0.
OPENAI_PRICES = (
    ("gpt-5-mini", (0.25, 2.0, 0.025)),
    ("gpt-5-nano", (0.05, 0.4, 0.005)),
    ("gpt-4.1-mini", (0.4, 1.6, 0.1)),
    ("gpt-4.1", (2.0, 8.0, 0.5)),
    ("gpt-4o-mini", (0.15, 0.6, 0.075)),
    ("gpt-4o", (2.5, 10.0, 1.25)),
    ("o4-mini", (1.1, 4.4, 0.275)),
    ("o3", (2.0, 8.0, 0.5)),
    ("gpt-", (1.25, 10.0, 0.125)),      # gpt-5 / gpt-6 / codex family fallback
    ("codex", (1.25, 10.0, 0.125)),
)


def claude_price(model: str):
    model = (model or "").lower()
    for prefix, p in CLAUDE_PRICES:
        if model.startswith(prefix):
            return p
    return dict(CLAUDE_PRICES)["claude-opus"]


def claude_usage_cost(model: str, u: dict) -> float:
    pin, pout, pread, pwrite = claude_price(model)
    return ((u.get("input_tokens") or 0) * pin + (u.get("output_tokens") or 0) * pout
            + (u.get("cache_read_input_tokens") or 0) * pread
            + (u.get("cache_creation_input_tokens") or 0) * pwrite) / 1e6


def openai_cost(model: str, input_tokens: int, cached: int, output: int) -> float:
    """Approximate $ for an OpenAI call (``input_tokens`` includes ``cached``)."""
    m = (model or "").lower()
    for prefix, (pin, pout, pcache) in OPENAI_PRICES:
        if m.startswith(prefix):
            fresh = max(0, (input_tokens or 0) - (cached or 0))
            return (fresh * pin + (cached or 0) * pcache + (output or 0) * pout) / 1e6
    return 0.0


# ------------------------------------------------------------------ context windows
_ONE_M = re.compile(r"(sonnet-5|opus-4-7|opus-4-8|opus-5(?!\d)|opus-5-5|fable-5|mythos-5)")


def claude_context_limit(*models: str) -> int:
    """1M for sonnet-5, opus-4-7/4-8/5/5-5, fable-5*, mythos-5* and any ``[1m]``; else 200k."""
    for m in models:
        m = (m or "").lower()
        if not m:
            continue
        if "[1m]" in m or _ONE_M.search(m):
            return 1_000_000
    return 200_000


def openai_context_limit(model: str) -> int:
    m = (model or "").lower()
    if m.startswith("gpt-4.1"):
        return 1_047_576
    if m.startswith(("gpt-5", "gpt-6")):
        return 400_000
    if m.startswith(("o3", "o4")):
        return 200_000
    return 128_000


# ------------------------------------------------------------------ activity + flags

HIGH_BURN_USD_10MIN = 2.0
BIG_CONTEXT_TOKENS = 250_000
QUIET_SECONDS = 15 * 60
LONG_TOOL_SECONDS = 20 * 60


class Activity:
    """What the flags look at. Collector parsers subclass / fill this."""

    def __init__(self):
        self.first_ts: Optional[float] = None
        self.last_ts: Optional[float] = None
        self.calls = deque(maxlen=4000)        # (ts, cost) per model call
        self.context = 0                       # prompt size of the latest call
        self.tool_count = 0
        self.recent_tools = deque(maxlen=40)   # (ts, name, summary, signature)
        self.pending = {}                      # call id -> (ts, name, summary)
        self.wakeup = None                     # (ts, delaySeconds, reason) ScheduleWakeup
        self.crons = {}
        self.loop_fires = deque(maxlen=50)

    def note_ts(self, ts):
        if ts:
            self.first_ts = self.first_ts or ts
            if not self.last_ts or ts >= self.last_ts:
                self.last_ts = ts

    def note_tool(self, ts, name: str, inp, call_id=None) -> str:
        summ = tool_summary(name, inp)
        self.tool_count += 1
        try:
            sig = name + ":" + json.dumps(inp, sort_keys=True, default=str)[:300]
        except Exception:
            sig = name + ":" + str(inp)[:300]
        self.recent_tools.append((ts, name, summ, sig))
        if call_id:
            self.pending[call_id] = (ts, name, summ)
        return summ

    def cost_since(self, seconds: float, now: Optional[float] = None) -> float:
        cut = (now or time.time()) - seconds
        return float(sum(c for t, c in self.calls if t and t >= cut))

    def last_tool_text(self) -> str:
        if not self.recent_tools:
            return ""
        _, name, summ, _ = self.recent_tools[-1]
        return tool_detail(name, summ)

    def current_tool(self) -> str:
        """The oldest still-running tool, as "Name: summary"."""
        if not self.pending:
            return ""
        _, name, summ = min(self.pending.values(), key=lambda v: v[0] or 0)
        return tool_detail(name, summ)


def compute_flags(act: Activity, busy: bool, context_limit: int = 0,
                  now: Optional[float] = None) -> list:
    """Waste / stuck flags: loop, repeat, poll, burn, context, longtool, quiet."""
    now = now or time.time()
    f = []
    loops = []
    if act.wakeup:
        loops.append("ScheduleWakeup")
    if act.loop_fires and now - act.loop_fires[-1] < 6 * 3600:
        loops.append(f"/loop fired {len(act.loop_fires)}×")
    if act.crons:
        loops.append(f"{len(act.crons)} cron(s)")
    if loops:
        f.append({"id": "loop", "level": "warn", "text": "recurring: " + ", ".join(loops)})
    recent = list(act.recent_tools)
    sigs = [x[3] for x in recent[-12:]]
    if len(sigs) >= 8:
        counts = {}
        for s in sigs:
            counts[s] = counts.get(s, 0) + 1
        sig, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= 6:
            f.append({"id": "repeat", "level": "bad",
                      "text": f"same call repeated {n}/12: {sig.split(':', 1)[0]}"})
    polls = sum(1 for x in recent[-10:]
                if x[1] in ("Bash", "Monitor", "shell", "exec", "exec_command")
                and re.search(r"\bsleep\b", x[2] or x[3] or ""))
    if polls >= 5:
        f.append({"id": "poll", "level": "warn", "text": f"polling with sleep ({polls}/10 calls)"})
    c10 = act.cost_since(600, now)
    if c10 >= HIGH_BURN_USD_10MIN:
        f.append({"id": "burn", "level": "bad", "text": f"high burn: ${c10:.2f} in 10 min"})
    if context_limit and act.context >= 0.8 * context_limit:
        f.append({"id": "context", "level": "warn",
                  "text": f"context {act.context // 1000}k / {context_limit // 1000}k — near compaction"})
    elif act.context >= BIG_CONTEXT_TOKENS:
        f.append({"id": "context", "level": "warn",
                  "text": f"big context {act.context // 1000}k tok — every call re-reads it (/compact?)"})
    if busy:
        if act.pending:
            t0, name, _ = min(act.pending.values(), key=lambda v: v[0] or now)
            if t0 and now - t0 > LONG_TOOL_SECONDS:
                f.append({"id": "longtool", "level": "warn",
                          "text": f"{name} running {int((now - t0) / 60)} min"})
        elif act.last_ts and now - act.last_ts > QUIET_SECONDS:
            f.append({"id": "quiet", "level": "warn",
                      "text": f"busy but silent for {int((now - act.last_ts) / 60)} min"})
    return f


def has_flag(flags: list, fid: str) -> bool:
    return any(x.get("id") == fid for x in flags)
