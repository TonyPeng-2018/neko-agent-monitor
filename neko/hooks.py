"""Opt-in hook installer for Claude Code (and Codex CLI).

Hooks are only a speed-up: neko works by reading transcripts / session files.
With hooks, state changes (permission prompt, tool start/stop …) reach the
daemon instantly.

Claude Code (``$CLAUDE_CONFIG_DIR/settings.json``, default ``~/.claude``):
  * ``type: "http"`` hooks that POST the hook JSON straight to
    ``http://127.0.0.1:<port>/api/event?source=claude`` with a short timeout.
    Per the Claude Code docs, connection failures / non-2xx / timeouts are
    non-blocking and an empty 2xx body means "no decision", so a stopped
    daemon never breaks the agent.
  * ``SessionStart`` only supports ``command`` hooks, so it runs
    ``scripts/neko-hook.py`` asynchronously (always exits 0, prints nothing).

Codex CLI (``$CODEX_HOME/hooks.json``, default ``~/.codex``): same JSON shape,
``command`` hooks running ``scripts/neko-hook.py --source codex``. Only with
``neko hooks install --codex``.

Every edit: back up to ``<file>.neko-bak`` first, preserve all foreign hooks,
write atomically, and be idempotent (re-running replaces only neko's entries).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from . import config

CLAUDE_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                 "PostToolUseFailure", "PermissionRequest", "Notification", "Stop", "StopFailure",
                 "SubagentStart", "SubagentStop", "PreCompact"]
CLAUDE_COMMAND_ONLY = {"SessionStart"}  # docs: SessionStart supports only command/mcp_tool hooks
CODEX_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
                "PostToolUse", "SubagentStart", "SubagentStop", "Stop", "PreCompact"]
MARK = "neko-hook"   # present in every command we install
BACKUP_SUFFIX = ".neko-bak"


# ---------------------------------------------------------------- paths / commands

def claude_settings_path() -> Path:
    return config.claude_home() / "settings.json"


def codex_hooks_path() -> Path:
    return config.codex_home() / "hooks.json"


def hook_script() -> Path:
    return config.scripts_dir() / "neko-hook.py"


def hook_command(source: str, port: int) -> str:
    script = hook_script()
    py = shlex.quote(sys.executable or "python3")
    if script.is_file():
        return f"{py} {shlex.quote(str(script))} --source {source} --port {port}"
    # installed without the repo checkout: the package can emit too
    return f"{py} -m neko.hooks emit --source {source} --port {port}  # {MARK}"


def event_url(port: int, source: str = "claude") -> str:
    return f"http://127.0.0.1:{port}/api/event?source={source}"


def is_neko_hook(h) -> bool:
    if not isinstance(h, dict):
        return False
    cmd = str(h.get("command", ""))
    url = str(h.get("url", ""))
    if MARK in cmd or "neko.hooks emit" in cmd:
        return True
    return url.startswith(("http://127.0.0.1:", "http://localhost:")) and "/api/event?source=" in url


# ---------------------------------------------------------------- JSON file edits

def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)  # raise on invalid JSON: never clobber a file we can't parse
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Keep the user's pristine file: never overwrite an existing backup with
        # a version that already contains neko's hooks (e.g. on uninstall).
        bak = Path(str(path) + BACKUP_SUFFIX)
        if not bak.exists() or not _status(path):
            shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _strip(hooks: dict) -> dict:
    """Return a copy of a hooks mapping with every neko handler removed."""
    out = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            out[event] = groups
            continue
        kept_groups = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                kept = [h for h in g["hooks"] if not is_neko_hook(h)]
                if not kept:
                    continue
                if len(kept) != len(g["hooks"]):
                    g = dict(g, hooks=kept)
            kept_groups.append(g)
        if kept_groups:
            out[event] = kept_groups
    return out


def _claude_group(event: str, port: int) -> dict:
    if event in CLAUDE_COMMAND_ONLY:
        h = {"type": "command", "command": hook_command("claude", port), "async": True, "timeout": 5}
    else:
        h = {"type": "http", "url": event_url(port, "claude"), "timeout": 2}
    return {"hooks": [h]}


def _codex_group(event: str, port: int) -> dict:
    return {"hooks": [{"type": "command", "command": hook_command("codex", port), "timeout": 2}]}


def _install(path: Path, events: list, make_group, port: int) -> bool:
    data = _load(path)
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    new_hooks = _strip(hooks)
    for ev in events:
        new_hooks.setdefault(ev, [])
        new_hooks[ev] = list(new_hooks[ev]) + [make_group(ev, port)]
    if data.get("hooks") == new_hooks:
        return False
    data = dict(data)
    data["hooks"] = new_hooks
    _write(path, data)
    return True


def _uninstall(path: Path) -> bool:
    if not path.exists():
        return False
    data = _load(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    new_hooks = _strip(hooks)
    if new_hooks == hooks:
        return False
    data = dict(data)
    if new_hooks:
        data["hooks"] = new_hooks
    else:
        data.pop("hooks")
    _write(path, data)
    return True


def _status(path: Path) -> list:
    try:
        data = _load(path)
    except (OSError, ValueError):
        return []
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    return sorted(ev for ev, groups in hooks.items() if isinstance(groups, list) and any(
        isinstance(g, dict) and any(is_neko_hook(h) for h in g.get("hooks") or []) for g in groups))


# ---------------------------------------------------------------- public API

def install(claude: bool = True, codex: bool = False, port: int | None = None) -> dict:
    port = port or config.port()
    res = {}
    if claude:
        res["claude"] = (str(claude_settings_path()),
                         _install(claude_settings_path(), CLAUDE_EVENTS, _claude_group, port))
    if codex:
        res["codex"] = (str(codex_hooks_path()),
                        _install(codex_hooks_path(), CODEX_EVENTS, _codex_group, port))
    return res


def uninstall(claude: bool = True, codex: bool = True) -> dict:
    res = {}
    if claude:
        res["claude"] = (str(claude_settings_path()), _uninstall(claude_settings_path()))
    if codex:
        res["codex"] = (str(codex_hooks_path()), _uninstall(codex_hooks_path()))
    return res


def status() -> dict:
    return {"claude": (str(claude_settings_path()), _status(claude_settings_path())),
            "codex": (str(codex_hooks_path()), _status(codex_hooks_path()))}


# ---------------------------------------------------------------- hook shim (fallback)

def emit(source: str = "claude", port: int | None = None, timeout: float = 0.3) -> int:
    """Read a hook payload on stdin and POST it to the daemon. Never fails, never prints."""
    try:
        import urllib.request
        raw = sys.stdin.buffer.read(1024 * 1024) if not sys.stdin.isatty() else b""
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = {"raw": raw.decode("utf-8", "replace")[:4000]}
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        payload.setdefault("neko_source", source)
        req = urllib.request.Request(
            event_url(port or config.port(), source), data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=timeout).close()
    except BaseException:
        pass
    return 0


if __name__ == "__main__":  # python -m neko.hooks emit --source codex --port 8765
    args = sys.argv[1:]
    if args[:1] == ["emit"]:
        src, prt = "claude", None
        try:
            if "--source" in args:
                src = args[args.index("--source") + 1]
            if "--port" in args:
                prt = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass
        emit(src, prt)
    sys.exit(0)
