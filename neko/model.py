"""The one data contract every part of neko shares.

Collectors (``neko/collectors/*``) produce ``Agent`` records; ``neko/persona.py``
attaches a ``persona`` (look + name) derived from the agent's creation prompt;
``neko/server.py`` serves ``snapshot()`` as JSON and the front end (``web/``)
renders one character per agent. Keep this file dependency-free.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Lifecycle states the front end animates. Collectors must pick exactly one.
WORKING = "working"    # model or a tool is running right now      → walks / types
WAITING = "waiting"    # needs the human: permission prompt, question → waves, "!" bubble
IDLE = "idle"          # alive, turn finished, waiting for next prompt → sits
SLEEPING = "sleeping"  # alive but quiet for a long time (or /loop asleep) → curls up, Zzz
ERROR = "error"        # last turn failed / API error / stuck-loop flag   → dizzy
DONE = "done"          # finished (subagent handed back, process exited recently) → celebrates, then leaves
STATES = (WORKING, WAITING, IDLE, SLEEPING, ERROR, DONE)

SOURCES = ("claude", "codex", "openai-sdk")


@dataclass
class Agent:
    id: str                         # globally unique: "<source>:<session or span id>"
    source: str                     # one of SOURCES
    kind: str = "session"           # "session" | "subagent" | "run"
    parent_id: str | None = None    # for subagents: the parent's id
    pid: int | None = None
    cwd: str = ""
    project: str = ""               # short name of cwd (last path part)
    title: str = ""                 # AI title / thread name, if any
    creation_prompt: str = ""       # FIRST real user prompt (or subagent task) → persona seed
    last_prompt: str = ""           # latest human prompt
    last_text: str = ""             # latest assistant text (clipped)
    model: str = ""
    state: str = IDLE
    state_detail: str = ""          # human-readable "Bash: npm test", "permission: Edit foo.py"
    started_at: float | None = None
    last_activity: float | None = None
    context_tokens: int = 0         # prompt size of the latest model call
    context_limit: int = 200_000
    tokens_in: int = 0              # cumulative, incl. cache reads
    tokens_out: int = 0
    cost_usd: float = 0.0           # API-list-price equivalent
    cost_10m: float = 0.0
    tool_count: int = 0
    last_tool: str = ""
    progress_done: int = 0          # todo list progress, if any
    progress_total: int = 0
    flags: list = field(default_factory=list)   # [{"id","level":"info|warn|bad","text"}]
    persona: dict = field(default_factory=dict)  # filled by persona.py

    @property
    def context_pct(self) -> float:
        return min(1.0, self.context_tokens / self.context_limit) if self.context_limit else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["context_pct"] = round(self.context_pct, 4)
        return d
