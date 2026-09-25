"""Hook events (Claude Code / Codex) → short-lived state overrides.

The server POSTs each hook payload to :func:`ingest`. We keep the latest
hook-derived state per session (and per subagent). Collectors call
:func:`override` with the timestamp of their own file-derived data; a hook state
wins only while it is *fresher* than that data.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

from ..model import DONE, ERROR, IDLE, WAITING, WORKING
from .common import clip, tool_detail, tool_summary

KEEP_SECONDS = 6 * 3600       # forget hook info for sessions silent this long
HINT_SECONDS = 10 * 60        # a hook's transcript_path marks a session live this long


class HookState:
    __slots__ = ("state", "detail", "ts", "error", "ended", "prompt", "transcript", "cwd",
                 "subagents", "flags")

    def __init__(self):
        self.state: Optional[str] = None
        self.detail = ""
        self.ts = 0.0
        self.error = ""
        self.ended: Optional[float] = None
        self.prompt = ""
        self.transcript = ""
        self.cwd = ""
        self.subagents: Dict[str, dict] = {}
        self.flags: list = []


_lock = threading.Lock()
_store: Dict[Tuple[str, str], HookState] = {}


def source_of(payload: dict) -> str:
    src = str(payload.get("neko_source") or payload.get("source") or payload.get("agent") or "").lower()
    if src in ("claude", "codex"):
        return src
    tp = str(payload.get("transcript_path") or "")
    if "/.codex/" in tp or "rollout-" in tp or "codex" in str(payload.get("originator") or ""):
        return "codex"
    return "claude"


def _perm_detail(p: dict) -> str:
    name = p.get("tool_name") or ""
    summ = tool_summary(name, p.get("tool_input") or {})
    if name:
        return "permission: " + tool_detail(name, summ)
    return "permission: " + clip(p.get("message") or "needs approval", 100)


def ingest(payload: dict, now: Optional[float] = None) -> None:
    if not isinstance(payload, dict):
        return
    sid = str(payload.get("session_id") or payload.get("sessionId") or payload.get("thread_id") or "")
    ev = str(payload.get("hook_event_name") or payload.get("event") or payload.get("type") or "")
    if not sid or not ev:
        return
    now = now or time.time()
    key = (source_of(payload), sid)
    with _lock:
        hs = _store.get(key)
        if hs is None:
            hs = _store[key] = HookState()
        if payload.get("transcript_path"):
            hs.transcript = str(payload["transcript_path"])
        if payload.get("cwd"):
            hs.cwd = str(payload["cwd"])
        agent_id = payload.get("agent_id")
        tool = payload.get("tool_name") or ""
        summ = tool_summary(tool, payload.get("tool_input") or {})

        def set_main(state, detail="", error=""):
            hs.state, hs.detail, hs.ts = state, detail, now
            if state != ERROR:
                hs.error = ""
            if error:
                hs.error = error

        def set_sub(aid, state, detail=""):
            sub = hs.subagents.setdefault(str(aid), {"agent_type": payload.get("agent_type") or ""})
            sub.update(state=state, detail=detail, ts=now)
            if payload.get("agent_type"):
                sub["agent_type"] = payload["agent_type"]
            if payload.get("agent_transcript_path"):
                sub["transcript"] = payload["agent_transcript_path"]

        if ev == "SubagentStart" and agent_id:
            set_sub(agent_id, WORKING, "starting")
        elif ev == "SubagentStop" and agent_id:
            set_sub(agent_id, DONE, "handed back")
        elif agent_id and ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure",
                                 "PermissionRequest"):
            if ev == "PreToolUse":
                set_sub(agent_id, WORKING, tool_detail(tool, summ))
            elif ev == "PermissionRequest":
                set_sub(agent_id, WAITING, _perm_detail(payload))
                set_main(WAITING, _perm_detail(payload))
            else:
                set_sub(agent_id, WORKING, "thinking")
        elif ev == "PermissionRequest":
            set_main(WAITING, _perm_detail(payload))
        elif ev == "Notification":
            nt = str(payload.get("notification_type") or "")
            if nt == "permission_prompt":
                set_main(WAITING, clip(payload.get("message") or "permission prompt", 100))
            elif nt in ("elicitation_dialog", "question"):
                set_main(WAITING, clip(payload.get("message") or "question", 100))
            elif nt == "idle_prompt":
                set_main(IDLE, "")
        elif ev == "PreToolUse":
            set_main(WORKING, tool_detail(tool, summ))
        elif ev in ("PostToolUse", "PostToolBatch"):
            set_main(WORKING, "thinking")
        elif ev == "PostToolUseFailure":
            set_main(WORKING, "thinking")
            hs.flags = [{"id": "toolfail", "level": "warn",
                         "text": clip(f"{tool} failed: {payload.get('error') or ''}", 120)}]
        elif ev == "UserPromptSubmit":
            set_main(WORKING, "thinking")
            hs.prompt = clip(payload.get("prompt") or "", 400)
            hs.flags = []
        elif ev == "Stop":
            set_main(IDLE, "")
        elif ev == "StopFailure":
            err = clip(payload.get("error") or payload.get("error_message")
                       or payload.get("message") or "turn failed", 140)
            set_main(ERROR, err, err)
        elif ev == "SessionStart":
            hs.ended = None
            set_main(IDLE, "")
        elif ev == "SessionEnd":
            hs.ended = now
            set_main(DONE, clip(payload.get("reason") or "session ended", 80))
        else:
            hs.ts = max(hs.ts, 0)  # unknown event: remember paths only
        # prune
        if len(_store) > 256:
            for k in [k for k, v in _store.items() if now - v.ts > KEEP_SECONDS]:
                _store.pop(k, None)


def get(source: str, sid: str) -> Optional[HookState]:
    with _lock:
        return _store.get((source, sid))


def override(source: str, sid: str, file_ts: Optional[float]):
    """(state, detail, error) from hooks if fresher than ``file_ts``, else None."""
    hs = get(source, sid)
    if not hs or not hs.state:
        return None
    if file_ts and hs.ts <= file_ts:
        return None
    return hs.state, hs.detail, hs.error


def sub_override(source: str, sid: str, agent_id: str, file_ts: Optional[float]):
    hs = get(source, sid)
    if not hs:
        return None
    sub = hs.subagents.get(agent_id)
    if not sub or (file_ts and sub.get("ts", 0) <= file_ts):
        return None
    return sub.get("state"), sub.get("detail", "")


def hinted(source: str, now: Optional[float] = None) -> Dict[str, str]:
    """session id → transcript path, for sessions with a recent hook event."""
    now = now or time.time()
    with _lock:
        return {sid: hs.transcript for (src, sid), hs in _store.items()
                if src == source and hs.transcript and now - hs.ts < HINT_SECONDS
                and not hs.ended}


def ended(source: str, sid: str) -> Optional[float]:
    hs = get(source, sid)
    return hs.ended if hs else None


def reset() -> None:
    with _lock:
        _store.clear()
