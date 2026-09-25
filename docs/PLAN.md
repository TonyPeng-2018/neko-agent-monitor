# Plan & task board

Planner: main session. Workers: one per module, each owns its files only.
Supervisor: reviews all modules against DESIGN.md, runs tests, fixes integration.

| # | Task | Owner | Files | Status |
|---|---|---|---|---|
| R1 | Similar products survey | researcher | docs/RESEARCH.md | done |
| R2 | Monitoring requirements + data sources | researcher | docs/RESEARCH.md | done |
| R3 | Embedding / 3D / overlay stack | researcher | docs/RESEARCH.md | done |
| W1 | Collectors: Claude, Codex, SDK ingest, liveness, flags | worker-collectors | neko/collectors/*, neko/procs.py, tests/test_collectors.py | |
| W2 | Embedding + persona traits | worker-persona | neko/embed.py, neko/persona.py, tests/test_persona.py | |
| W3 | 3D chibi characters, animation engine, room + HUD | worker-web | web/** | |
| W4 | Daemon/server, CLI, launchd, hooks installer, Electron overlay, README | worker-runtime | neko/server.py, neko/cli.py, neko/hooks.py, overlay/**, scripts/**, README.md, pyproject.toml, Makefile | |
| S1 | Supervisor: integration review, end-to-end run, screenshots, fixes | supervisor | all | |

Rules for workers
- `neko/model.py` and the persona schema in DESIGN.md are the contract. Don't change them without noting it in your report.
- Python ≥ 3.9, stdlib-only core. Optional deps: `model2vec` (embedding). No torch.
- Never write to `~/.claude` or `~/.codex` except the opt-in hook installer (with backup).
- Tests: `python3 -m pytest -q tests` must pass offline.
