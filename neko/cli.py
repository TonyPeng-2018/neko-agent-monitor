"""``neko`` command line.

  neko serve [--port N] [--demo] [--open]   run the daemon + web UI
  neko status [--json]                      table of live agents
  neko install / uninstall                  launchd LaunchAgent (auto start)
  neko hooks install|uninstall|status [--codex]
  neko fetch-model                          pre-download the embedding model
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import config

FACES = {"working": "(=^･ω･^)ﾉ", "waiting": "(=ﾟωﾟ)ﾉ!", "idle": "(=^･ｪ･^=)",
         "sleeping": "(=－ω－)zZ", "error": "(=@ω@=)", "done": "\\(=^▽^=)/"}


# ---------------------------------------------------------------- serve

def cmd_serve(a) -> int:
    from .server import serve
    serve(port=a.port, demo=a.demo, open_browser=a.open)
    return 0


# ---------------------------------------------------------------- status

def _fetch_api(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/agents", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _direct_snapshot() -> dict:
    agents = []
    try:
        from .collectors import collect_all  # type: ignore
        agents = list(collect_all())
    except Exception as e:
        print(f"(collectors unavailable: {e})", file=sys.stderr)
    try:
        from .server import Hub
        hub = Hub.__new__(Hub)  # just for persona attachment, no threads
        hub._persona_cache, hub.last_error, hub._store, hub.demo = {}, "", None, False
        from .server import _load_persona
        hub.persona_mod = _load_persona()
        hub.attach_personas(agents)
    except Exception:
        pass
    from .server import _totals
    dicts = [a.to_dict() for a in agents]
    return {"generated_at": time.time(), "agents": dicts, "totals": _totals(dicts)}


def _ago(ts) -> str:
    if not ts:
        return "-"
    s = max(0, time.time() - float(ts))
    return f"{int(s)}s" if s < 90 else f"{int(s // 60)}m" if s < 5400 else f"{int(s // 3600)}h"


def _w(s: str) -> int:
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1
               for c in s)


def _cell(s: str, width: int) -> str:
    s = str(s).replace("\n", " ")
    while _w(s) > width:
        s = s[:-2] + "…" if len(s) > 1 else ""
    return s + " " * (width - _w(s))


def render_table(snap: dict) -> str:
    agents = snap.get("agents") or []
    t = snap.get("totals") or {}
    if not agents:
        return "no agents running — the room is quiet. (=－ω－)zZ"
    order = {"waiting": 0, "error": 1, "working": 2, "idle": 3, "sleeping": 4, "done": 5}
    by_parent = {}
    for a in agents:
        by_parent.setdefault(a.get("parent_id"), []).append(a)
    rows = []
    tops = [a for a in agents if not a.get("parent_id") or a["parent_id"] not in {x["id"] for x in agents}]
    for a in sorted(tops, key=lambda a: (order.get(a.get("state"), 9), -(a.get("last_activity") or 0))):
        rows.append((a, 0))
        for k in by_parent.get(a["id"], []):
            rows.append((k, 1))
    cols = [("", 11), ("name", 10), ("src", 6), ("state", 9), ("project", 16), ("ctx", 4),
            ("$10m", 6), ("seen", 4), ("doing", 34)]
    lines = ["  ".join(_cell(h, w) for h, w in cols).rstrip(),
             "  ".join("─" * w for _h, w in cols)]
    for a, depth in rows:
        p = a.get("persona") or {}
        name = ("└ " if depth else "") + (p.get("name") or a["id"].split(":")[-1][:8])
        st = a.get("state", "")
        doing = a.get("state_detail") or a.get("title") or a.get("last_prompt") or ""
        vals = [FACES.get(st, ""), name, a.get("source", "")[:6], st, a.get("project", ""),
                f"{int(round((a.get('context_pct') or 0) * 100))}%", f"{a.get('cost_10m') or 0:.2f}",
                _ago(a.get("last_activity")), doing]
        lines.append("  ".join(_cell(v, w) for v, (_h, w) in zip(vals, cols)).rstrip())
    lines.append("")
    lines.append(f"{t.get('agents', len(agents))} agents · {t.get('working', 0)} working · "
                 f"{t.get('waiting', 0)} waiting · {t.get('subagents', 0)} kittens · "
                 f"${t.get('cost_10m', 0):.2f}/10m · ${t.get('cost_total', 0):.2f} total")
    return "\n".join(lines)


def cmd_status(a) -> int:
    snap = _fetch_api(a.port or config.port())
    src = "daemon"
    if snap is None:
        snap, src = _direct_snapshot(), "direct (daemon not running)"
    if a.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_table(snap))
        print(f"source: {src}")
    return 0


# ---------------------------------------------------------------- launchd

def plist_path() -> Path:
    return config.launch_agents_dir() / f"{config.LAUNCHD_LABEL}.plist"


def build_plist(port: int | None = None) -> dict:
    env = {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "PYTHONUNBUFFERED": "1", "LANG": os.environ.get("LANG", "en_US.UTF-8")}
    if config.hf_model_cached():
        env["HF_HUB_OFFLINE"] = "1"
    for k in ("NEKO_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "HF_HOME"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if port:
        env["NEKO_PORT"] = str(port)
    log = str(config.log_file())
    return {
        "Label": config.LAUNCHD_LABEL,
        "ProgramArguments": [sys.executable, "-m", "neko", "serve"],
        "WorkingDirectory": str(config.REPO_ROOT),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _launchctl(*args) -> subprocess.CompletedProcess:
    if os.environ.get("NEKO_NO_LAUNCHCTL"):
        return subprocess.CompletedProcess(args, 0, "", "")
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def cmd_install(a) -> int:
    if sys.platform != "darwin" and not os.environ.get("NEKO_NO_LAUNCHCTL"):
        print("neko install uses launchd (macOS only). Run `neko serve` under your own supervisor.")
        return 1
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    config.log_file().parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{config.LAUNCHD_LABEL}")  # ignore "not loaded"
    with open(p, "wb") as f:
        plistlib.dump(build_plist(a.port), f)
    r = _launchctl("bootstrap", domain, str(p))
    if r.returncode != 0:
        print(f"launchctl bootstrap failed: {r.stderr.strip() or r.stdout.strip()}")
        return 1
    print(f"installed {p}\n  → neko starts at login and restarts if it crashes"
          f"\n  → logs: {config.log_file()}\n  → {config.base_url(a.port)}/  (=^･ω･^=)")
    if not config.hf_model_cached():
        print("  (tip: `neko fetch-model` then `neko install` again to run fully offline)")
    return 0


def cmd_uninstall(a) -> int:
    p = plist_path()
    _launchctl("bootout", f"gui/{os.getuid()}/{config.LAUNCHD_LABEL}")
    if p.exists():
        p.unlink()
        print(f"removed {p}. bye bye~ (=；ω；=)ﾉ")
    else:
        print("not installed.")
    return 0


# ---------------------------------------------------------------- hooks

def cmd_hooks(a) -> int:
    from . import hooks
    if a.action == "install":
        res = hooks.install(claude=not a.codex_only, codex=a.codex or a.codex_only, port=a.port)
        for tool, (path, changed) in res.items():
            print(f"{tool}: {'installed neko hooks in' if changed else 'already up to date:'} {path}"
                  + (f"  (backup: {path}{hooks.BACKUP_SUFFIX})" if changed else ""))
        print("restart running agents (or open /hooks in Claude Code) to pick them up.")
    elif a.action == "uninstall":
        res = hooks.uninstall(claude=not a.codex_only, codex=True)
        for tool, (path, changed) in res.items():
            print(f"{tool}: {'removed neko hooks from' if changed else 'nothing to remove in'} {path}")
    else:
        for tool, (path, events) in hooks.status().items():
            print(f"{tool}: {', '.join(events) if events else 'not installed'}  [{path}]")
    return 0


# ---------------------------------------------------------------- model

def cmd_fetch_model(a) -> int:
    try:
        from . import embed  # type: ignore
        for name in ("fetch_model", "download_model", "get_model", "load_model"):
            fn = getattr(embed, name, None)
            if callable(fn):
                print(f"fetching embedding model via neko.embed.{name}() …")
                fn()
                print("ok (=^･ω･^=)")
                return 0
    except Exception as e:
        print(f"neko.embed could not fetch the model ({e!r}); trying model2vec directly")
    try:
        from model2vec import StaticModel  # type: ignore
    except ImportError:
        print("model2vec is not installed: pip install 'neko-agent-monitor[embed]'\n"
              "(neko still works with the built-in hashing fallback)")
        return 1
    print(f"downloading {config.EMBED_MODEL} …")
    StaticModel.from_pretrained(config.EMBED_MODEL)
    print("ok (=^･ω･^=)")
    return 0


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="neko", description="kawaii monitor for your AI agents (=^･ω･^=)")
    ap.add_argument("--version", action="version", version=f"neko {config.__version__}")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the daemon + web UI")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--demo", action="store_true", help="fake agents, no real monitoring")
    s.add_argument("--open", action="store_true", help="open the room in your browser")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("status", help="print live agents")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("install", help="install launchd agent (auto start at login)")
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("uninstall", help="remove launchd agent")
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("hooks", help="opt-in agent hooks for instant updates")
    s.add_argument("action", choices=["install", "uninstall", "status"])
    s.add_argument("--codex", action="store_true", help="also Codex CLI (~/.codex/hooks.json)")
    s.add_argument("--codex-only", action="store_true", help="only Codex CLI")
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(fn=cmd_hooks)

    s = sub.add_parser("fetch-model", help="pre-download the embedding model")
    s.set_defaults(fn=cmd_fetch_model)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    return int(a.fn(a) or 0)


if __name__ == "__main__":
    sys.exit(main())
