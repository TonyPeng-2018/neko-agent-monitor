"""Claude Code collector.

Sources (read-only):
  * ``$CLAUDE_CONFIG_DIR|~/.claude/sessions/<pid>.json`` — live-session registry
    (status busy/idle/waiting + waitingFor), validated by ``procStart`` vs the
    process table so a crashed session's leftover file is ignored.
  * ``projects/<slug>/<sid>.jsonl`` — transcript, parsed incrementally (byte offset).
  * ``projects/<slug>/<sid>/subagents/agent-*.jsonl`` + ``.meta.json`` — subagents.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

from .. import procs
from ..model import DONE, ERROR, IDLE, SLEEPING, WAITING, WORKING, Agent
from . import events
from .common import (Activity, claude_context_limit, claude_usage_cost, clip, compute_flags,
                     has_flag, project_of, tool_detail, ts_of)

log = logging.getLogger("neko.collectors.claude")

SLEEP_AFTER = 10 * 60          # idle this long → sleeping
SUB_ACTIVE = 120               # subagent file touched within → working
SUB_DONE_KEEP = 60             # show a finished subagent this long
SUB_MAX_AGE = 3600             # ignore subagent files older than this
ERROR_KEEP = 10 * 60           # an API error is "recent" this long
BIG_FILE = 16 * 1024 * 1024    # transcripts above this: head scan + tail parse
TAIL_BYTES = 4 * 1024 * 1024
HEAD_SCAN_BYTES = 2 * 1024 * 1024

_SKIP_PROMPT = ("<command-", "<local-command", "<task-notification>", "<system-reminder>",
                "<bash-input>", "<bash-stdout>", "<bash-stderr>", "<ide_opened_file>",
                "<ide_selection>", "<user-prompt-submit-hook>")


def claude_home() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def _user_text(c) -> str:
    """Text of a user message content (string or list of blocks); '' for tool results."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        texts = [b.get("text") or "" for b in c
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(texts)
    return ""


def is_real_prompt(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and not t.startswith(_SKIP_PROMPT) and not t.startswith("[Request interrupted")


# ------------------------------------------------------------------ transcript

class Transcript(Activity):
    """Incremental parse of one transcript .jsonl (only new bytes are read)."""

    def __init__(self, path: str, sidechain: bool = False):
        super().__init__()
        self.path = path
        self.sidechain = sidechain   # subagent file: every record is a sidechain
        self.offset = 0
        self.inode = None
        self.title = ""
        self.first_prompt = ""
        self.queue_prompt = ""       # -p runs: queue-operation enqueue content
        self.prompt = ""             # latest real human prompt
        self.last_prompt_rec = ""    # 'last-prompt' record
        self.turn_started = None
        self.last_text = ""
        self.last_text_ts = None
        self.model = ""
        self.model_hint = ""         # attachment{type:model}.identity.modelId, e.g. "…[1m]"
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost = 0.0
        self.cost_state = None       # (ts, totalCostUSD)
        self.cost_after_state = 0.0  # computed cost of calls after the last cost-state
        self.todos: list = []
        self.tasks: dict = {}
        self._task_seq = 0
        self.compactions = 0
        self.api_error = None        # (ts, text, retryAttempt) — latest, cleared by later success
        self.synthetic_error = None  # (ts, text) — isApiErrorMessage assistant
        self.last_kind = ""          # "assistant" | "user" | "tool_result" | "api_error"
        self.last_stop_reason = None
        self.last_assistant_ts = None
        self.handed_back = False
        self.partial = False         # True if we skipped the middle of a huge file
        self._seen_msgs = set()

    # -- reading
    def update(self) -> "Transcript":
        try:
            st = os.stat(self.path)
        except OSError:
            return self
        size = st.st_size
        if (self.inode is not None and st.st_ino != self.inode) or size < self.offset:
            self.__init__(self.path, self.sidechain)  # rewritten / replaced
        self.inode = st.st_ino
        if size == self.offset:
            return self
        if self.offset == 0 and size > BIG_FILE:
            self._head_scan()
            self.offset = size - TAIL_BYTES
            self.partial = True
            skip_first = True
        else:
            skip_first = False
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(size - self.offset)
        except OSError:
            return self
        if skip_first:  # we landed mid-line: drop the fragment
            nl = chunk.find(b"\n")
            if nl < 0:
                return self
            self.offset += nl + 1
            chunk = chunk[nl + 1:]
        end = chunk.rfind(b"\n")
        if end < 0:
            return self
        self.offset += end + 1
        for line in chunk[: end + 1].splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if isinstance(d, dict):
                try:
                    self._line(d)
                except Exception:  # one odd record must not kill the parse
                    log.debug("bad record in %s", self.path, exc_info=True)
        return self

    def _head_scan(self):
        """Huge file: find the creation prompt / title near the top, stop early."""
        try:
            with open(self.path, "rb") as f:
                read = 0
                for line in f:
                    read += len(line)
                    if read > HEAD_SCAN_BYTES:
                        break
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(d, dict):
                        continue
                    t = d.get("type")
                    if t in ("queue-operation", "ai-title", "user"):
                        self._line(d)
                    if self.first_prompt:
                        break
        except OSError:
            pass

    # -- records
    def _line(self, d: dict):
        t = d.get("type")
        ts = ts_of(d.get("timestamp"))
        self.note_ts(ts)
        if t == "assistant":
            self._assistant(d, ts)
        elif t == "user":
            self._user(d, ts)
        elif t == "ai-title":
            self.title = d.get("aiTitle") or self.title
        elif t == "last-prompt":
            self.last_prompt_rec = d.get("lastPrompt") or self.last_prompt_rec
        elif t == "queue-operation":
            if d.get("operation") == "enqueue" and isinstance(d.get("content"), str):
                if not self.queue_prompt:
                    self.queue_prompt = d["content"]
                if not self.first_prompt and is_real_prompt(d["content"]):
                    self.first_prompt = d["content"].strip()  # `claude -p`: the prompt
        elif t == "cost-state":
            try:
                self.cost_state = (ts, float(d.get("totalCostUSD") or 0.0))
                self.cost_after_state = 0.0
            except (TypeError, ValueError):
                pass
        elif t == "attachment":
            a = d.get("attachment") or {}
            if isinstance(a, dict) and a.get("type") == "model":
                ident = a.get("identity") or {}
                self.model_hint = ident.get("modelId") or a.get("model") or self.model_hint
        elif t == "system":
            st = d.get("subtype")
            if st == "api_error":
                err = d.get("error") or {}
                text = err.get("formatted") or err.get("message") if isinstance(err, dict) else err
                self.api_error = (ts, clip(text or "API error", 140),
                                  int(d.get("retryAttempt") or 0), int(d.get("maxRetries") or 0))
                self.last_kind = "api_error"
            elif st == "compact_boundary":
                self.compactions += 1
                self.context = 0
            elif st == "scheduled_task_fire":
                self.loop_fires.append(ts or time.time())

    def _user(self, d, ts):
        if d.get("isMeta") or (d.get("isSidechain") and not self.sidechain):
            return
        c = (d.get("message") or {}).get("content")
        had_result = False
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.pending.pop(b.get("tool_use_id"), None)
                    had_result = True
        text = _user_text(c).strip()
        if self.sidechain and not self.first_prompt and text:
            self.first_prompt = text  # subagent: line 1 is the task, whatever it looks like
        if is_real_prompt(text):
            if not self.first_prompt:
                self.first_prompt = text
            self.prompt = text
            self.turn_started = ts
            self.last_kind = "user"
            self.synthetic_error = None
            self.handed_back = False
        elif had_result:
            self.last_kind = "tool_result"

    def _assistant(self, d, ts):
        if d.get("isSidechain") and not self.sidechain:
            return
        m = d.get("message") or {}
        model = m.get("model") or ""
        if d.get("isApiErrorMessage") or model == "<synthetic>":
            text = ""
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text":
                    text = b.get("text") or text
            if d.get("isApiErrorMessage") or d.get("error"):
                self.synthetic_error = (ts, clip(text or d.get("error") or "API error", 140))
                self.last_kind = "api_error"
            return
        self.model = model or self.model
        mid = m.get("id")
        u = m.get("usage") or {}
        if mid and mid not in self._seen_msgs and u:
            self._seen_msgs.add(mid)
            if len(self._seen_msgs) > 20000:
                self._seen_msgs = set(list(self._seen_msgs)[-5000:])
            c = claude_usage_cost(self.model, u)
            self.cost += c
            if self.cost_state:
                self.cost_after_state += c
            self.calls.append((ts or time.time(), c))
            ctx = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                   + (u.get("cache_creation_input_tokens") or 0))
            self.context = ctx
            self.tokens_in += ctx
            self.tokens_out += u.get("output_tokens") or 0
        self.api_error = None
        self.last_kind = "assistant"
        self.last_assistant_ts = ts
        if m.get("stop_reason"):
            self.last_stop_reason = m.get("stop_reason")
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and (b.get("text") or "").strip():
                self.last_text = b["text"].strip()
                self.last_text_ts = ts
            elif b.get("type") == "tool_use":
                self._tool(b, ts)

    def _tool(self, b, ts):
        name, inp = b.get("name") or "?", b.get("input") or {}
        self.note_tool(ts, name, inp, b.get("id"))
        if not isinstance(inp, dict):
            return
        if name == "TodoWrite" and isinstance(inp.get("todos"), list):
            self.todos = [{"content": clip(t.get("content") or t.get("activeForm"), 90),
                           "status": t.get("status", "pending")}
                          for t in inp["todos"] if isinstance(t, dict)]
        elif name == "TaskCreate":
            self._task_seq += 1
            self.tasks[str(self._task_seq)] = {
                "content": clip(inp.get("subject") or inp.get("description"), 90),
                "status": "pending"}
        elif name == "TaskUpdate" and str(inp.get("taskId")) in self.tasks and inp.get("status"):
            self.tasks[str(inp["taskId"])]["status"] = inp["status"]
        elif name == "ScheduleWakeup":
            self.wakeup = None if inp.get("stop") else (
                ts, float(inp.get("delaySeconds") or 0), clip(inp.get("reason"), 100))
        elif name == "CronCreate":
            self.crons[str(inp.get("name") or len(self.crons))] = clip(inp.get("prompt"), 100)
        elif name == "CronDelete":
            self.crons.pop(str(inp.get("name") or inp.get("id") or ""), None)
        elif name == "SubagentHandback":
            self.handed_back = True

    # -- derived
    def creation_prompt(self) -> str:
        return self.first_prompt or self.queue_prompt

    def total_cost(self) -> float:
        if self.cost_state:
            return self.cost_state[1] + self.cost_after_state
        return self.cost

    def progress(self):
        items = self.todos or list(self.tasks.values())
        if not items:
            return 0, 0
        done = sum(1 for t in items if t.get("status") == "completed")
        return done, len(items)

    def context_limit(self) -> int:
        lim = claude_context_limit(self.model_hint, self.model)
        if self.context > lim:
            lim = 1_000_000
        return lim

    def recent_error(self, now: float) -> str:
        if self.synthetic_error and self.last_kind == "api_error":
            ts, text = self.synthetic_error
            if ts and now - ts < ERROR_KEEP:
                return text
        if self.api_error and self.last_kind == "api_error":
            ts, text, attempt, maxr = self.api_error
            # a first retry is routine (e.g. laptop sleep); keep going unless it persists
            if ts and now - ts < ERROR_KEEP and (attempt >= 3 or (maxr and attempt >= maxr)
                                                 or now - ts > 90):
                return text
        return ""


_CACHE: Dict[str, Transcript] = {}
_LOCK = threading.Lock()


def transcript(path: str, sidechain: bool = False) -> Transcript:
    with _LOCK:
        tr = _CACHE.get(path)
        if tr is None:
            tr = _CACHE[path] = Transcript(path, sidechain)
    return tr.update()


def _forget_unused(keep: set):
    with _LOCK:
        for p in [p for p in _CACHE if p not in keep]:
            _CACHE.pop(p, None)


def find_transcript(home: str, sid: str, cwd: str) -> Optional[str]:
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd or "")
    p = os.path.join(home, "projects", slug, f"{sid}.jsonl")
    if os.path.exists(p):
        return p
    hits = glob.glob(os.path.join(home, "projects", "*", f"{sid}.jsonl"))
    return hits[0] if hits else None


# ------------------------------------------------------------------ registry

def registry(home: Optional[str] = None) -> List[dict]:
    home = home or claude_home()
    out = []
    table = procs.processes()
    for f in glob.glob(os.path.join(home, "sessions", "*.json")):
        try:
            with open(f) as fh:
                d = json.load(fh)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        pid = d.get("pid")
        if not isinstance(pid, int) or not procs.is_alive(pid, d.get("procStart"), table):
            continue  # stale file: that process is gone
        out.append(d)
    return out


# ------------------------------------------------------------------ agents

def _session_agent(reg: dict, home: str, now: float, used: set) -> List[Agent]:
    sid = str(reg.get("sessionId") or "")
    cwd = reg.get("cwd") or ""
    path = find_transcript(home, sid, cwd) if sid else None
    tr = transcript(path) if path else Transcript("/nonexistent")
    if path:
        used.add(path)
    status = reg.get("status") or "idle"
    status_ts = ts_of(reg.get("statusUpdatedAt") or reg.get("updatedAt"))
    busy = status in ("busy", "shell")
    a = Agent(id=f"claude:{sid}", source="claude", kind="session", pid=reg.get("pid"),
              cwd=cwd, project=project_of(cwd))
    a.title = tr.title or (reg.get("name") if reg.get("nameSource") not in ("derived",) else "") or ""
    a.creation_prompt = clip(tr.creation_prompt(), 2000)
    a.last_prompt = clip(tr.prompt or tr.last_prompt_rec, 400)
    a.last_text = clip(tr.last_text, 300)
    a.model = tr.model_hint or tr.model
    a.started_at = ts_of(reg.get("startedAt")) or tr.first_ts
    stamps = [x for x in (tr.last_ts, status_ts) if x]
    a.last_activity = max(stamps) if stamps else None
    a.context_tokens = tr.context
    a.context_limit = tr.context_limit()
    a.tokens_in, a.tokens_out = tr.tokens_in, tr.tokens_out
    a.cost_usd = round(tr.total_cost(), 4)
    a.cost_10m = round(tr.cost_since(600, now), 4)
    a.tool_count = tr.tool_count
    a.last_tool = tr.last_tool_text()
    a.progress_done, a.progress_total = tr.progress()
    a.flags = compute_flags(tr, busy, a.context_limit, now)
    if tr.partial:
        a.flags.append({"id": "partial", "level": "info",
                        "text": "huge transcript: totals cover only the recent part"})

    # state
    if status == "waiting":
        a.state, a.state_detail = WAITING, clip(reg.get("waitingFor") or "needs you", 100)
    elif busy:
        a.state = WORKING
        a.state_detail = tr.current_tool() or "thinking"
    else:
        a.state = IDLE
        quiet = now - (a.last_activity or now)
        nxt = (tr.wakeup[0] or now) + tr.wakeup[1] if tr.wakeup else None
        if nxt and nxt > now:
            a.state, a.state_detail = SLEEPING, f"/loop wakeup in {int((nxt - now) // 60)}m"
        elif quiet > SLEEP_AFTER:
            a.state, a.state_detail = SLEEPING, f"quiet {int(quiet // 60)} min"
    err = tr.recent_error(now)
    if err and a.state in (IDLE, SLEEPING, WORKING):
        a.state, a.state_detail = ERROR, err
    if a.state == WORKING and has_flag(a.flags, "repeat"):
        a.state = ERROR
        a.state_detail = next(f["text"] for f in a.flags if f["id"] == "repeat")

    # hooks win while fresher than the files
    ov = events.override("claude", sid, a.last_activity)
    if ov:
        st, det, er = ov
        if st == DONE:
            st = IDLE  # process still alive: SessionEnd arrived early
        a.state, a.state_detail = st, det or (er if st == ERROR else a.state_detail)
    hs = events.get("claude", sid)
    if hs and hs.flags and hs.ts > (tr.last_ts or 0) - 60:
        a.flags.extend(hs.flags)
    if hs and hs.prompt and not a.last_prompt:
        a.last_prompt = hs.prompt

    out = [a]
    if path:
        out.extend(_subagents(path[:-len(".jsonl")], a, sid, now, used))
    return out


def _read_json(p: str):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _subagents(sess_dir: str, parent: Agent, sid: str, now: float, used: set) -> List[Agent]:
    out = []
    for f in glob.glob(os.path.join(sess_dir, "subagents", "agent-*.jsonl")):
        try:
            mtime = os.stat(f).st_mtime
        except OSError:
            continue
        age = now - mtime
        if age > SUB_MAX_AGE:
            continue
        aid = os.path.basename(f)[len("agent-"):-len(".jsonl")]
        meta = _read_json(f[:-len(".jsonl")] + ".meta.json") or {}
        tr = transcript(f, sidechain=True)
        used.add(f)
        finished = tr.handed_back or (
            tr.last_kind == "assistant" and tr.last_stop_reason == "end_turn" and not tr.pending)
        a = Agent(id=f"claude:{sid}:{aid}", source="claude", kind="subagent",
                  parent_id=parent.id, pid=parent.pid, cwd=parent.cwd, project=parent.project)
        a.title = clip(meta.get("description") or "", 120)
        a.creation_prompt = clip(tr.creation_prompt(), 2000)
        a.last_prompt = clip(tr.prompt, 400)
        a.last_text = clip(tr.last_text, 300)
        a.model = tr.model
        a.started_at = tr.first_ts
        a.last_activity = max(mtime, tr.last_ts or 0)
        a.context_tokens = tr.context
        a.context_limit = tr.context_limit()
        a.tokens_in, a.tokens_out = tr.tokens_in, tr.tokens_out
        a.cost_usd = round(tr.total_cost(), 4)
        a.cost_10m = round(tr.cost_since(600, now), 4)
        a.tool_count = tr.tool_count
        a.last_tool = ("finished — report handed back" if tr.handed_back else tr.last_tool_text())
        a.progress_done, a.progress_total = tr.progress()
        active = age < SUB_ACTIVE and not finished
        a.flags = compute_flags(tr, active, a.context_limit, now)
        if finished or (age >= SUB_ACTIVE and tr.last_text and not tr.pending):
            if age > SUB_DONE_KEEP:
                continue  # handed back a while ago: gone
            a.state, a.state_detail = DONE, "handed back"
        elif active:
            a.state = WORKING
            a.state_detail = tr.current_tool() or "thinking"
            if has_flag(a.flags, "repeat"):
                a.state = ERROR
                a.state_detail = next(x["text"] for x in a.flags if x["id"] == "repeat")
        elif tr.pending and age < 30 * 60:
            a.state, a.state_detail = WORKING, tr.current_tool()  # a long-running tool
        elif tr.last_kind in ("user", "tool_result") and age < SLEEP_AFTER:
            a.state, a.state_detail = WORKING, "thinking"  # a long model call
        else:
            a.state, a.state_detail = IDLE, f"quiet {int(age // 60)} min"
        err = tr.recent_error(now)
        if err and a.state != DONE:
            a.state, a.state_detail = ERROR, err
        ov = events.sub_override("claude", sid, aid, a.last_activity)
        if ov and ov[0]:
            a.state, a.state_detail = ov[0], ov[1] or a.state_detail
        out.append(a)
    # hook-only subagents (SubagentStart arrived before any file was written)
    hs = events.get("claude", sid)
    if hs:
        seen = {x.id for x in out}
        for aid, sub in list(hs.subagents.items()):
            aid_full = f"claude:{sid}:{aid}"
            if aid_full in seen or now - sub.get("ts", 0) > SUB_DONE_KEEP:
                continue
            if sub.get("state") == DONE:
                continue
            out.append(Agent(id=aid_full, source="claude", kind="subagent", parent_id=parent.id,
                             pid=parent.pid, cwd=parent.cwd, project=parent.project,
                             title=sub.get("agent_type") or "", state=sub.get("state") or WORKING,
                             state_detail=sub.get("detail") or "", started_at=sub.get("ts"),
                             last_activity=sub.get("ts"), context_limit=parent.context_limit))
    return out


def collect(now: Optional[float] = None) -> List[Agent]:
    now = now or time.time()
    home = claude_home()
    out: List[Agent] = []
    used: set = set()
    for reg in registry(home):
        try:
            out.extend(_session_agent(reg, home, now, used))
        except Exception:
            log.exception("claude session %s", reg.get("sessionId"))
    _forget_unused(used)
    return out
