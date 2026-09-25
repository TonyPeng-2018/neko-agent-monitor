"""OpenAI Codex CLI collector.

Sources (read-only):
  * processes named ``codex`` (``ps``/``/proc``), their cwd and open rollout files
    (``lsof -n -P`` / ``/proc/<pid>/fd``), cached per pid;
  * ``$CODEX_HOME|~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<threadId>.jsonl`` —
    parsed incrementally; rollouts written in the last 2 minutes count as live even
    without a matched process (``codex exec`` runs, or a missed ``lsof``);
  * ``session_index.jsonl`` (thread names) and ``state_5.sqlite`` (opened
    ``mode=ro``; titles + ``thread_spawn_edges`` for the subagent tree).

Costs are APPROXIMATE (rough OpenAI list prices, see ``common.OPENAI_PRICES``).
"""
from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .. import procs
from ..model import ERROR, IDLE, SLEEPING, WAITING, WORKING, Agent
from . import events
from .common import (Activity, clip, compute_flags, has_flag, openai_context_limit, openai_cost,
                     project_of, tool_detail, ts_of)

log = logging.getLogger("neko.collectors.codex")

RECENT_LIVE = 120            # rollout written within → live without a process
SLEEP_AFTER = 10 * 60
_EXCLUDE_ARGS = ("mcp", "mcp-server", "login", "logout", "completion", "--version", "-V",
                 "--help", "help", "sandbox", "debug", "apply", "features")


def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _is_context_blob(text: str) -> bool:
    t = text.lstrip()
    return t.startswith(("<environment_context", "<user_instructions", "<permissions",
                         "<skills_instructions", "<collaboration_mode", "# AGENTS.md",
                         "<INSTRUCTIONS>", "<turn_aborted", "<user_shell_command"))


def _texts(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            out.append(b["text"])
    return "\n".join(out)


# ------------------------------------------------------------------ rollout

class Rollout(Activity):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.offset = 0
        self.inode = None
        self.thread_id = ""
        self.cwd = ""
        self.originator = ""
        self.parent_thread = ""
        self.agent_name = ""
        self.started = None
        self.model = ""
        self.first_prompt = ""
        self.prompt = ""
        self.last_text = ""
        self.busy = False
        self.turn_started = None
        self.context_window = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cached_in = 0
        self.cost = 0.0
        self.approval = None        # (ts, detail) pending approval / question
        self.error = None           # (ts, text)
        self.aborted = False

    def update(self) -> "Rollout":
        try:
            st = os.stat(self.path)
        except OSError:
            return self
        if (self.inode is not None and st.st_ino != self.inode) or st.st_size < self.offset:
            self.__init__(self.path)
        self.inode = st.st_ino
        if st.st_size == self.offset:
            return self
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(st.st_size - self.offset)
        except OSError:
            return self
        end = chunk.rfind(b"\n")
        if end < 0:
            return self
        self.offset += end + 1
        for line in chunk[: end + 1].splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    self._line(d)
            except Exception:
                continue
        return self

    def _line(self, d: dict):
        t = d.get("type")
        p = d.get("payload") or {}
        if not isinstance(p, dict):
            return
        ts = ts_of(d.get("timestamp"))
        self.note_ts(ts)
        if t == "session_meta":
            self.thread_id = p.get("id") or p.get("session_id") or self.thread_id
            self.cwd = p.get("cwd") or self.cwd
            self.originator = p.get("originator") or self.originator
            self.started = ts_of(p.get("timestamp")) or ts
            src = p.get("source")
            if isinstance(src, dict):  # {"subagent": {"thread_spawn": {"parent_thread_id": …}}}
                sub = src.get("subagent") or {}
                spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
                if isinstance(spawn, dict):
                    self.parent_thread = spawn.get("parent_thread_id") or ""
                    self.agent_name = spawn.get("agent_nickname") or spawn.get("agent_role") or ""
            self.agent_name = p.get("agent_nickname") or self.agent_name
        elif t == "turn_context":
            self.model = p.get("model") or self.model
            self.cwd = p.get("cwd") or self.cwd
        elif t == "event_msg":
            self._event(p, ts)
        elif t == "response_item":
            self._item(p, ts)

    def _event(self, p: dict, ts):
        et = p.get("type") or ""
        is_ask = et.endswith("_approval_request") or et in (
            "request_user_input", "elicitation_request", "request_permissions")
        if self.approval and not is_ask and et not in ("token_count", "thread_settings_applied"):
            self.approval = None  # anything else happening means the ask was answered
        if et == "task_started":
            self.busy, self.turn_started, self.aborted = True, ts, False
            self.error = None
            self.context_window = p.get("model_context_window") or self.context_window
        elif et == "task_complete":
            self.busy = False
            self.pending.clear()
            if p.get("last_agent_message"):
                self.last_text = str(p["last_agent_message"]).strip()
        elif et == "turn_aborted":
            self.busy, self.aborted = False, True
            self.pending.clear()
        elif et == "token_count":
            info = p.get("info") or {}
            if not isinstance(info, dict):
                return
            self.context_window = info.get("model_context_window") or self.context_window
            last = info.get("last_token_usage") or {}
            tot = info.get("total_token_usage") or {}
            if last:
                self.context = int(last.get("input_tokens") or 0)
            if tot:
                tin = int(tot.get("input_tokens") or 0)
                tout = int(tot.get("output_tokens") or 0)
                tcache = int(tot.get("cached_input_tokens") or 0)
                d_in, d_out, d_cache = tin - self.tokens_in, tout - self.tokens_out, tcache - self.cached_in
                if d_in > 0 or d_out > 0:  # token_count repeats on rate-limit updates
                    c = openai_cost(self.model, max(0, d_in), max(0, d_cache), max(0, d_out))
                    self.cost += c
                    self.calls.append((ts or time.time(), c))
                self.tokens_in, self.tokens_out, self.cached_in = tin, tout, tcache
        elif et == "item_completed":
            item = p.get("item") or {}
            it = item.get("type")
            if it == "UserMessage":
                self._user(_texts(item.get("content")), ts)
            elif it == "AgentMessage":
                txt = _texts(item.get("content")).strip()
                if txt:
                    self.last_text = txt
            elif it == "CommandExecution":
                cmd = item.get("command")
                if isinstance(cmd, list):
                    cmd = cmd[-1] if len(cmd) >= 3 and cmd[1] in ("-lc", "-c") else " ".join(map(str, cmd))
                self.recent_tools.append((ts, "shell", clip(cmd, 120), "shell:" + str(cmd)[:300]))
            elif it == "FileChange":
                files = list((item.get("changes") or {}).keys())
                self.recent_tools.append((ts, "apply_patch", clip(", ".join(os.path.basename(f) for f in files), 120),
                                          "apply_patch:" + ",".join(files)[:300]))
        elif et == "user_message":  # older rollouts
            self._user(p.get("message") or "", ts)
        elif et == "agent_message":
            if p.get("message"):
                self.last_text = str(p["message"]).strip()
        elif is_ask:
            what = p.get("command") or p.get("reason") or p.get("message") or p.get("question") or ""
            if isinstance(what, list):
                what = " ".join(map(str, what))
            kind = "question" if "input" in et or "elicitation" in et else "permission"
            self.approval = (ts, clip(f"{kind}: {what}" if what else kind, 100))
        elif et in ("error", "stream_error"):
            self.error = (ts, clip(p.get("message") or p.get("error") or et, 140))

    def _user(self, text: str, ts):
        text = (text or "").strip()
        if not text or _is_context_blob(text):
            return
        if not self.first_prompt:
            self.first_prompt = text
        self.prompt = text

    def _item(self, p: dict, ts):
        it = p.get("type")
        if it in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call",
                  "tool_search_call"):
            name = p.get("name") or it.replace("_call", "")
            inp = p.get("arguments") if "arguments" in p else p.get("input") or p.get("action")
            if isinstance(inp, str):
                try:
                    inp = json.loads(inp)
                except Exception:
                    pass
            self.note_tool(ts, name, inp, p.get("call_id"))
        elif it in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
            self.pending.pop(p.get("call_id"), None)
            self.approval = None
        elif it == "message" and p.get("role") == "user" and not self.first_prompt:
            self._user(_texts(p.get("content")), ts)

    def context_limit(self) -> int:
        return int(self.context_window or 0) or openai_context_limit(self.model)

    def recent_error(self, now: float) -> str:
        if self.error and not self.busy and now - (self.error[0] or now) < 10 * 60:
            return self.error[1]
        return ""


_CACHE: Dict[str, Rollout] = {}
_LOCK = threading.Lock()


def rollout(path: str) -> Rollout:
    with _LOCK:
        r = _CACHE.get(path)
        if r is None:
            r = _CACHE[path] = Rollout(path)
    return r.update()


# ------------------------------------------------------------------ titles & tree

_index_cache: Dict[str, object] = {"key": None, "names": {}}
_db_cache: Dict[str, object] = {"ts": 0.0, "home": None, "threads": {}, "edges": {}}


def _thread_names(home: str) -> Dict[str, str]:
    p = os.path.join(home, "session_index.jsonl")
    try:
        st = os.stat(p)
    except OSError:
        return {}
    key = (p, st.st_mtime, st.st_size)
    if _index_cache["key"] == key:
        return _index_cache["names"]  # type: ignore[return-value]
    names: Dict[str, str] = {}
    try:
        with open(p, "rb") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if isinstance(d, dict) and d.get("id") and d.get("thread_name"):
                    names[str(d["id"])] = str(d["thread_name"])
    except OSError:
        pass
    _index_cache["key"], _index_cache["names"] = key, names
    return names


def _db(home: str):
    """(threads: id → row dict, edges: child → parent) from state_5.sqlite, cached 5 s."""
    now = time.time()
    if _db_cache["home"] == home and now - float(_db_cache["ts"]) < 5:
        return _db_cache["threads"], _db_cache["edges"]
    threads: Dict[str, dict] = {}
    edges: Dict[str, str] = {}
    dbs = sorted(glob.glob(os.path.join(home, "state_*.sqlite")))
    if dbs:
        try:
            con = sqlite3.connect(f"file:{dbs[-1]}?mode=ro", uri=True, timeout=0.2)
            try:
                con.row_factory = sqlite3.Row
                cols = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
                want = [c for c in ("id", "rollout_path", "cwd", "title", "name", "model",
                                    "first_user_message", "updated_at_ms", "updated_at",
                                    "agent_nickname", "agent_role", "archived") if c in cols]
                if "id" in want:
                    order = "updated_at_ms" if "updated_at_ms" in cols else "updated_at"
                    for r in con.execute(f"SELECT {', '.join(want)} FROM threads "
                                         f"ORDER BY {order} DESC LIMIT 500"):
                        threads[r["id"]] = dict(r)
                try:
                    for r in con.execute("SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"):
                        edges[r[1]] = r[0]
                except sqlite3.Error:
                    pass
            finally:
                con.close()
        except sqlite3.Error:
            pass  # locked / absent / schema drift: titles fall back to the rollout
    _db_cache.update(ts=now, home=home, threads=threads, edges=edges)
    return threads, edges


# ------------------------------------------------------------------ liveness

def _is_codex(p: procs.Proc) -> bool:
    argv = p.command.split()
    if not argv:
        return False
    base = os.path.basename(argv[0])
    rest = argv[1:]
    if base.startswith("node"):
        if not rest or os.path.basename(rest[0]) not in ("codex", "codex.js"):
            return False
        rest = rest[1:]
    elif base != "codex" and not (base.startswith("codex-") and ("darwin" in base or "linux" in base)):
        return False
    sub = next((a for a in rest if not a.startswith("-")), "")
    return sub not in _EXCLUDE_ARGS


def live_processes() -> List[procs.Proc]:
    """Codex processes, keeping only leaves (the native binary under the node shim)."""
    table = procs.processes()
    cands = {pid: p for pid, p in table.items() if _is_codex(p)}
    parents = {p.ppid for p in cands.values()}
    return [p for pid, p in cands.items() if pid not in parents]


_meta_cache: Dict[str, tuple] = {}   # path -> (mtime, cwd, thread_id)


def _meta(path: str):
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return None
    hit = _meta_cache.get(path)
    if hit and hit[0] == mt:
        return hit
    cwd = tid = ""
    try:
        with open(path, "rb") as f:
            first = f.readline(4 * 1024 * 1024)
        d = json.loads(first)
        pl = d.get("payload") or {}
        cwd, tid = pl.get("cwd") or "", pl.get("id") or ""
    except Exception:
        pass
    hit = _meta_cache[path] = (mt, cwd, tid)
    return hit


def _day_dirs(home: str, since: float) -> List[str]:
    out = []
    day = datetime.fromtimestamp(max(since, time.time() - 7 * 86400)).date()
    today = datetime.now().date() + timedelta(days=1)
    while day <= today:
        d = os.path.join(home, "sessions", f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}")
        if os.path.isdir(d):
            out.append(d)
        day += timedelta(days=1)
    return out


def _rollouts_since(home: str, since: float) -> List[tuple]:
    """[(mtime, path)] of rollouts modified after ``since`` (newest first)."""
    out = []
    for d in _day_dirs(home, since - 86400):
        for p in glob.glob(os.path.join(d, "rollout-*.jsonl")):
            try:
                mt = os.stat(p).st_mtime
            except OSError:
                continue
            if mt >= since:
                out.append((mt, p))
    out.sort(reverse=True)
    return out


def _match_by_cwd(home: str, proc: procs.Proc, threads: dict, taken: set) -> Optional[str]:
    cwd = procs.cwd_of(proc.pid)
    if not cwd:
        return None
    since = (proc.start or time.time()) - 5
    for row in threads.values():  # newest first
        upd = ts_of(row.get("updated_at_ms") or row.get("updated_at")) or 0
        rp = row.get("rollout_path") or ""
        if row.get("cwd") == cwd and upd >= since and rp and rp not in taken and os.path.exists(rp):
            return rp
    for mt, p in _rollouts_since(home, since):
        m = _meta(p)
        if m and m[1] == cwd and p not in taken:
            return p
    return None


# ------------------------------------------------------------------ agents

def _agent_for(path: str, pid: Optional[int], home: str, names: dict, threads: dict,
               edges: dict, now: float) -> Agent:
    r = rollout(path)
    tid = r.thread_id or os.path.basename(path)[:-6].split("-", 6)[-1]
    row = threads.get(tid) or {}
    a = Agent(id=f"codex:{tid}", source="codex", pid=pid, cwd=r.cwd or row.get("cwd") or "")
    a.project = project_of(a.cwd)
    parent = r.parent_thread or edges.get(tid) or ""
    if parent:
        a.kind, a.parent_id = "subagent", f"codex:{parent}"
    title = names.get(tid) or row.get("name") or ""
    if not title and row.get("title") and row.get("title") != row.get("first_user_message"):
        title = row["title"]
    a.title = clip(title or r.agent_name or row.get("agent_nickname") or "", 120)
    a.creation_prompt = clip(r.first_prompt or row.get("first_user_message") or "", 2000)
    a.last_prompt = clip(r.prompt, 400)
    a.last_text = clip(r.last_text, 300)
    a.model = r.model or row.get("model") or ""
    a.started_at = r.started or r.first_ts
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        mt = None
    a.last_activity = max([x for x in (r.last_ts, mt) if x] or [0]) or None
    a.context_tokens = r.context
    a.context_limit = r.context_limit()
    a.tokens_in, a.tokens_out = r.tokens_in, r.tokens_out
    a.cost_usd = round(r.cost, 4)
    a.cost_10m = round(r.cost_since(600, now), 4)
    a.tool_count = r.tool_count
    a.last_tool = r.last_tool_text()
    a.flags = compute_flags(r, r.busy, a.context_limit, now)
    quiet = now - (a.last_activity or now)
    if r.approval:
        a.state, a.state_detail = WAITING, r.approval[1]
    elif r.busy:
        a.state, a.state_detail = WORKING, r.current_tool() or "thinking"
        if has_flag(a.flags, "repeat"):
            a.state = ERROR
            a.state_detail = next(f["text"] for f in a.flags if f["id"] == "repeat")
    elif r.recent_error(now):
        a.state, a.state_detail = ERROR, r.recent_error(now)
    elif quiet > SLEEP_AFTER:
        a.state, a.state_detail = SLEEPING, f"quiet {int(quiet // 60)} min"
    else:
        a.state, a.state_detail = IDLE, "interrupted" if r.aborted else ""
    ov = events.override("codex", tid, a.last_activity)
    if ov and ov[0] != "done":
        a.state, a.state_detail = ov[0], ov[1] or a.state_detail
    return a


def collect(now: Optional[float] = None) -> List[Agent]:
    now = now or time.time()
    home = codex_home()
    if not os.path.isdir(home):
        return []
    names = _thread_names(home)
    threads, edges = _db(home)
    live: Dict[str, Optional[int]] = {}   # rollout path -> pid
    bare: List[procs.Proc] = []
    for p in live_processes():
        try:
            files = [f for f in procs.open_files(p.pid)
                     if "rollout-" in f and f.endswith(".jsonl")]
        except Exception:
            files = []
        if not files:
            m = _match_by_cwd(home, p, threads, set(live))
            files = [m] if m else []
        if files:
            for f in files:
                live.setdefault(f, p.pid)
        else:
            bare.append(p)
    for mt, path in _rollouts_since(home, now - RECENT_LIVE):
        live.setdefault(path, None)
    for sid, path in events.hinted("codex", now).items():
        if path and os.path.exists(path):
            live.setdefault(path, None)
    out: List[Agent] = []
    for path, pid in live.items():
        try:
            out.append(_agent_for(path, pid, home, names, threads, edges, now))
        except Exception:
            log.exception("codex rollout %s", path)
    # a codex TUI with no rollout yet (nothing typed): show it idle
    for p in bare:
        cwd = procs.cwd_of(p.pid)
        out.append(Agent(id=f"codex:pid-{p.pid}", source="codex", pid=p.pid, cwd=cwd,
                         project=project_of(cwd), state=IDLE, state_detail="new session",
                         started_at=p.start, last_activity=p.start,
                         context_limit=openai_context_limit("")))
    with _LOCK:
        for k in [k for k in _CACHE if k not in live]:
            _CACHE.pop(k, None)
    return out
