"""Process table helpers — macOS (``ps``/``lsof``) and Linux (``/proc``).

Everything here is cheap and cached:

* :func:`processes` — one ``ps -axww -o pid=,ppid=,lstart=,command=`` call (or a
  ``/proc`` walk on Linux) per ``max_age`` seconds (default 2 s).
* :func:`is_alive` — pid liveness *and* pid-reuse guard: Claude's session registry
  stores ``procStart`` either as a UTC ``lstart`` string (macOS: ``"Thu Sep 24
  23:21:29 2026"``) or as ``/proc/<pid>/stat`` start ticks (Linux).
* :func:`cwd_of` / :func:`open_files` — ``lsof`` (``-n -P``: no DNS/port lookups)
  or ``/proc/<pid>/{cwd,fd}``; cached per (pid, start time).

Nothing here raises: failures yield empty results.
"""
from __future__ import annotations

import calendar
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

IS_LINUX = os.path.isdir("/proc/self") and os.path.exists("/proc/stat")

_LSTART_FMT = "%a %b %d %H:%M:%S %Y"


@dataclass
class Proc:
    pid: int
    ppid: int
    start: Optional[float]          # epoch seconds (UTC based)
    command: str                    # full argv joined by spaces
    ticks: Optional[str] = None     # Linux /proc starttime (clock ticks since boot)

    @property
    def argv0(self) -> str:
        return self.command.split(" ", 1)[0] if self.command else ""

    @property
    def exe_name(self) -> str:
        return os.path.basename(self.argv0)


_lock = threading.Lock()
_cache: Dict[str, object] = {"ts": 0.0, "procs": {}}


def parse_lstart(s: str) -> Optional[float]:
    """``"Thu Sep 24 23:21:29 2026"`` (UTC, C locale) → epoch seconds."""
    try:
        return float(calendar.timegm(time.strptime(" ".join(str(s).split()), _LSTART_FMT)))
    except Exception:
        return None


def _ps_snapshot() -> Dict[int, Proc]:
    env = dict(os.environ, TZ="UTC", LC_ALL="C", LANG="C")
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,lstart=,command="],
                             capture_output=True, text=True, env=env, timeout=5,
                             errors="replace").stdout
    except Exception:
        return {}
    procs: Dict[int, Proc] = {}
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        start = parse_lstart(" ".join(parts[2:7]))
        procs[pid] = Proc(pid, ppid, start, parts[7] if len(parts) > 7 else "")
    return procs


_BTIME: Optional[float] = None


def _btime() -> float:
    global _BTIME
    if _BTIME is None:
        try:
            for line in open("/proc/stat"):
                if line.startswith("btime"):
                    _BTIME = float(line.split()[1])
                    break
        except Exception:
            pass
        if _BTIME is None:
            _BTIME = 0.0
    return _BTIME


def _proc_snapshot() -> Dict[int, Proc]:
    procs: Dict[int, Proc] = {}
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except Exception:
        hz = 100
    bt = _btime()
    try:
        names = os.listdir("/proc")
    except OSError:
        return procs
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
            rest = raw.rsplit(")", 1)[1].split()
            ppid, ticks = int(rest[1]), rest[19]
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except Exception:
            continue
        start = bt + int(ticks) / hz if bt else None
        procs[pid] = Proc(pid, ppid, start, cmd, ticks)
    return procs


def processes(max_age: float = 2.0) -> Dict[int, Proc]:
    """pid → :class:`Proc` for every process, cached for ``max_age`` seconds."""
    now = time.time()
    with _lock:
        if now - float(_cache["ts"]) < max_age and _cache["procs"]:
            return _cache["procs"]  # type: ignore[return-value]
    procs = _proc_snapshot() if IS_LINUX else _ps_snapshot()
    with _lock:
        _cache["ts"], _cache["procs"] = time.time(), procs
    return procs


def is_alive(pid, proc_start=None, procs: Optional[Dict[int, Proc]] = None) -> bool:
    """True if ``pid`` runs and (when given) was started at ``proc_start``."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    procs = processes() if procs is None else procs
    p = procs.get(pid)
    if p is None:
        return False
    if proc_start in (None, ""):
        return True
    s = str(proc_start).strip()
    if s.isdigit():  # Linux: /proc starttime ticks
        if p.ticks is not None:
            return p.ticks == s
        return True  # can't verify on this platform
    want = parse_lstart(s)
    if want is None or p.start is None:
        return True
    return abs(want - p.start) <= 2.0


# ------------------------------------------------------------------ lsof / cwd

_file_cache: Dict[tuple, tuple] = {}   # (kind, pid, start) -> (ts, value)
_file_lock = threading.Lock()


def _cached(kind: str, pid: int, ttl: float, fn):
    procs = processes()
    p = procs.get(pid)
    key = (kind, pid, p.start if p else None)
    now = time.time()
    with _file_lock:
        hit = _file_cache.get(key)
        if hit and (ttl <= 0 or now - hit[0] < ttl):
            return hit[1]
    val = fn(pid)
    with _file_lock:
        _file_cache[key] = (now, val)
        if len(_file_cache) > 512:  # forget dead pids
            for k in [k for k in _file_cache if k[1] not in procs]:
                _file_cache.pop(k, None)
    return val


def _lsof(args: List[str]) -> List[str]:
    try:
        out = subprocess.run(["lsof", "-n", "-P"] + args, capture_output=True, text=True,
                             timeout=5, errors="replace").stdout
    except Exception:
        return []
    return [l[1:] for l in out.splitlines() if l.startswith("n")]


def _cwd_now(pid: int) -> str:
    if IS_LINUX:
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return ""
    names = _lsof(["-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    return names[0] if names else ""


def _files_now(pid: int) -> List[str]:
    if IS_LINUX:
        out = []
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    out.append(os.readlink(f"/proc/{pid}/fd/{fd}"))
                except OSError:
                    pass
        except OSError:
            pass
        return out
    return _lsof(["-p", str(pid), "-Fn"])


def cwd_of(pid: int) -> str:
    """Working directory of ``pid`` (cached for the life of the process)."""
    return _cached("cwd", int(pid), 0, _cwd_now)


def open_files(pid: int, ttl: float = 15.0) -> List[str]:
    """Paths ``pid`` has open (cached ``ttl`` seconds)."""
    return _cached("files", int(pid), ttl, _files_now)


def children_of(pid: int, procs: Optional[Dict[int, Proc]] = None) -> List[Proc]:
    procs = processes() if procs is None else procs
    return [p for p in procs.values() if p.ppid == pid]
