"""Runtime configuration for the neko daemon (stdlib only).

Everything is read from the environment *at call time* so tests (and the
launchd plist) can override paths without touching real user directories.

Env overrides
  NEKO_HOST          bind address (default 127.0.0.1 — keep it local!)
  NEKO_PORT          port (default 8765)
  NEKO_HOME          state dir (default ~/.neko)
  NEKO_POLL          collector poll interval in seconds (default 2)
  NEKO_WEB_DIR       static front-end dir (default <repo>/web)
  CLAUDE_CONFIG_DIR  Claude Code home (default ~/.claude)
  CODEX_HOME         Codex CLI home (default ~/.codex)
  NEKO_LAUNCH_AGENTS_DIR  where `neko install` writes the plist (default ~/Library/LaunchAgents)
"""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_POLL = 2.0
LAUNCHD_LABEL = "com.neko-agent-monitor"
EMBED_MODEL = "minishlab/potion-multilingual-128M"

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name, "").strip()
    return Path(v).expanduser() if v else default


def host() -> str:
    return os.environ.get("NEKO_HOST", "").strip() or DEFAULT_HOST


def port() -> int:
    try:
        return int(os.environ.get("NEKO_PORT", "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def poll_interval() -> float:
    try:
        return max(0.2, float(os.environ.get("NEKO_POLL", "") or DEFAULT_POLL))
    except ValueError:
        return DEFAULT_POLL


def neko_home() -> Path:
    return _env_path("NEKO_HOME", Path.home() / ".neko")


def claude_home() -> Path:
    return _env_path("CLAUDE_CONFIG_DIR", Path.home() / ".claude")


def codex_home() -> Path:
    return _env_path("CODEX_HOME", Path.home() / ".codex")


def web_dir() -> Path:
    return _env_path("NEKO_WEB_DIR", REPO_ROOT / "web")


def scripts_dir() -> Path:
    return REPO_ROOT / "scripts"


def launch_agents_dir() -> Path:
    return _env_path("NEKO_LAUNCH_AGENTS_DIR", Path.home() / "Library" / "LaunchAgents")


def log_file() -> Path:
    return _env_path("NEKO_LOG_FILE", Path.home() / "Library" / "Logs" / "neko-agent-monitor.log")


def base_url(p: int | None = None) -> str:
    return f"http://127.0.0.1:{p or port()}"


def hf_model_cached(model: str = EMBED_MODEL) -> bool:
    """True if the embedding model is already in the Hugging Face cache."""
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(Path.home(), ".cache", "huggingface"), "hub")
    d = Path(hub) / ("models--" + model.replace("/", "--")) / "snapshots"
    try:
        return any(d.iterdir())
    except OSError:
        return False
