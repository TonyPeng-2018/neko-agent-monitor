"""OpenAI Agents SDK runs, pushed to us as trace/span exports.

``ingest(payload)`` accepts any of:
  * one export dict — ``trace.export()`` (``{"object": "trace", "id", "workflow_name", …}``)
    or ``span.export()`` (``{"object": "trace.span", "id", "trace_id", "parent_id",
    "started_at", "ended_at", "span_data": {"type": …}, "error"}``);
  * ``{"data": [...]}`` / ``{"items": [...]}`` / a list of those (OpenAI's batch format);
  * our client's envelope ``{"event": "trace_start|trace_end|span_start|span_end", "item": {...}}``.

One :class:`~neko.model.Agent` per trace (kind ``run``); ``agent`` spans that have a
parent become ``subagent`` records. Finished runs show ``done`` and expire after
``DONE_KEEP`` seconds; runs silent for ``STALE`` seconds are dropped.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from ..model import DONE, ERROR, IDLE, WORKING, Agent
from .common import clip, openai_context_limit, openai_cost, ts_of

DONE_KEEP = 60
IDLE_AFTER = 5 * 60
STALE = 30 * 60


class _Span:
    __slots__ = ("id", "parent", "type", "name", "started", "ended", "error", "data")

    def __init__(self, sid):
        self.id, self.parent, self.type, self.name = sid, None, "", ""
        self.started = self.ended = None
        self.error = None
        self.data: dict = {}


class _Run:
    def __init__(self, tid: str, now: float):
        self.id = tid
        self.workflow = ""
        self.group = ""
        self.started = now
        self.ended: Optional[float] = None
        self.last = now
        self.spans: Dict[str, _Span] = {}
        self.first_prompt = ""
        self.counted: set = set()          # generation span ids already counted


_lock = threading.Lock()
_runs: Dict[str, _Run] = {}


def _items(payload) -> List[tuple]:
    """→ [(event or None, item dict)]"""
    if isinstance(payload, list):
        out = []
        for x in payload:
            out.extend(_items(x))
        return out
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("data"), list):
        return _items(payload["data"])
    if isinstance(payload.get("items"), list):
        return _items(payload["items"])
    if isinstance(payload.get("item"), dict):
        return [(payload.get("event"), payload["item"])]
    return [(payload.get("event"), payload)]


def _first_user_text(inp) -> str:
    if isinstance(inp, str):
        return inp
    if isinstance(inp, list):
        for m in inp:
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str):
                    return c
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and isinstance(b.get("text"), str):
                            return b["text"]
    return ""


def ingest(payload, now: Optional[float] = None) -> None:
    now = now or time.time()
    with _lock:
        for event, it in _items(payload):
            if not isinstance(it, dict):
                continue
            obj = it.get("object") or ("trace.span" if it.get("span_data") or it.get("trace_id") else "trace")
            if obj == "trace":
                tid = str(it.get("id") or "")
                if not tid:
                    continue
                run = _runs.get(tid) or _runs.setdefault(tid, _Run(tid, now))
                run.workflow = it.get("workflow_name") or run.workflow
                run.group = it.get("group_id") or run.group
                run.last = now
                if event == "trace_end" or it.get("ended_at") or it.get("ended"):
                    run.ended = run.ended or now
                elif event == "trace_start":
                    run.ended = None
                continue
            tid = str(it.get("trace_id") or "")
            sid = str(it.get("id") or "")
            if not tid or not sid:
                continue
            run = _runs.get(tid) or _runs.setdefault(tid, _Run(tid, now))
            run.last = now
            sp = run.spans.get(sid) or run.spans.setdefault(sid, _Span(sid))
            sp.parent = it.get("parent_id") or sp.parent
            sd = it.get("span_data") or {}
            if isinstance(sd, dict):
                sp.type = sd.get("type") or sp.type
                sp.name = sd.get("name") or sp.name
                sp.data.update({k: v for k, v in sd.items() if v is not None})
            sp.started = ts_of(it.get("started_at")) or sp.started or now
            if it.get("ended_at") or event == "span_end":
                sp.ended = ts_of(it.get("ended_at")) or now
            if it.get("error"):
                sp.error = it["error"]
            if sp.type == "generation" and not run.first_prompt:
                run.first_prompt = _first_user_text(sd.get("input"))
            if sp.type == "response" and not run.first_prompt:
                run.first_prompt = _first_user_text(sd.get("input"))
        # expire
        for tid in [t for t, r in _runs.items()
                    if (r.ended and now - r.ended > DONE_KEEP) or now - r.last > STALE]:
            _runs.pop(tid, None)


def _agent_of(run: _Run, sp: _Span) -> Optional[str]:
    """Nearest enclosing agent span id (or None)."""
    seen = 0
    cur = sp
    while cur is not None and seen < 64:
        if cur.type == "agent" and cur is not sp:
            return cur.id
        cur = run.spans.get(cur.parent) if cur.parent else None
        seen += 1
    return None


def _err_text(e) -> str:
    if isinstance(e, dict):
        return clip(e.get("message") or str(e.get("data") or "") or "error", 140)
    return clip(e or "error", 140)


def collect(now: Optional[float] = None) -> List[Agent]:
    now = now or time.time()
    out: List[Agent] = []
    with _lock:
        for tid in [t for t, r in _runs.items()
                    if (r.ended and now - r.ended > DONE_KEEP) or now - r.last > STALE]:
            _runs.pop(tid, None)
        for run in list(_runs.values()):
            out.extend(_run_agents(run, now))
    return out


def _run_agents(run: _Run, now: float) -> List[Agent]:
    rid = f"openai-sdk:{run.id}"
    spans = list(run.spans.values())
    # top-level agent spans belong to the run; nested ones become subagents
    subs = {s.id: s for s in spans if s.type == "agent" and s.parent}
    run_a = Agent(id=rid, source="openai-sdk", kind="run", started_at=run.started,
                  last_activity=run.last)
    agents = {None: run_a}
    for s in subs.values():
        agents[s.id] = Agent(id=f"openai-sdk:{s.id}", source="openai-sdk", kind="subagent",
                             parent_id=rid, title=clip(s.name, 120), started_at=s.started,
                             last_activity=s.ended or s.started)
    top_agents = [s for s in spans if s.type == "agent" and not s.parent]
    run_a.title = clip(run.workflow or (top_agents[0].name if top_agents else ""), 120)
    run_a.creation_prompt = clip(run.first_prompt or run.workflow
                                 or (top_agents[0].name if top_agents else ""), 2000)
    for s in sorted(spans, key=lambda x: x.started or 0):
        owner = _agent_of(run, s)
        target = agents.get(owner if owner in subs else None, run_a)
        if s.type == "generation" or s.type == "response":
            usage = s.data.get("usage") or {}
            model = s.data.get("model") or ""
            if isinstance(s.data.get("response"), dict):
                model = model or s.data["response"].get("model") or ""
            tin = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            tout = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0) \
                if isinstance(usage.get("input_tokens_details"), dict) else 0
            cost = openai_cost(model, tin, cached, tout)
            for a in {id(target): target, id(run_a): run_a}.values():
                a.tokens_in += tin
                a.tokens_out += tout
                a.cost_usd += cost
                if (s.ended or s.started or 0) >= now - 600:
                    a.cost_10m += cost
                if tin:
                    a.context_tokens = tin
                if model:
                    a.model = model
            if target is not run_a and not target.creation_prompt:
                target.creation_prompt = clip(_first_user_text(s.data.get("input")), 2000)
        elif s.type in ("function", "mcp_tools", "custom"):
            for a in {id(target): target, id(run_a): run_a}.values():
                a.tool_count += 1
                a.last_tool = clip(f"{s.name}: {s.data.get('input') or ''}".rstrip(": "), 120)
        if s.error:
            target.flags.append({"id": "error", "level": "bad", "text": _err_text(s.error)})
        target.last_activity = max(target.last_activity or 0, s.ended or s.started or 0) or None
        run_a.last_activity = max(run_a.last_activity or 0, s.ended or s.started or 0) or None
    open_spans = [s for s in spans if not s.ended]
    for key, a in agents.items():
        a.context_limit = openai_context_limit(a.model)
        a.cost_usd, a.cost_10m = round(a.cost_usd, 5), round(a.cost_10m, 5)
        if key is None:
            mine = open_spans
            ended = run.ended
        else:
            sp = subs[key]
            mine = [s for s in open_spans if s.id == key or _agent_of(run, s) == key]
            ended = sp.ended
            if not a.creation_prompt:
                a.creation_prompt = a.title
        errs = [f for f in a.flags if f["id"] == "error"]
        if ended:
            a.state, a.state_detail = (ERROR, errs[-1]["text"]) if errs and key is None else (DONE, "finished")
        elif errs:
            a.state, a.state_detail = ERROR, errs[-1]["text"]
        elif mine or now - (a.last_activity or now) < IDLE_AFTER:
            tool = next((s for s in reversed(mine) if s.type in ("function", "mcp_tools")), None)
            a.state = WORKING
            a.state_detail = f"{tool.name}" if tool else "thinking"
        else:
            a.state, a.state_detail = IDLE, ""
        if key is not None and run.ended and not ended:
            a.state, a.state_detail = DONE, "finished"
    return [run_a] + [agents[k] for k in subs]


def reset() -> None:
    with _lock:
        _runs.clear()
