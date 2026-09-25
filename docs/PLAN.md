# Plan & task board

Planner: main session. Workers: one per module, each owns its files only.
Supervisor: reviews all modules against DESIGN.md, runs tests, fixes integration.

| # | Task | Owner | Files | Status |
|---|---|---|---|---|
| R1 | Similar products survey | researcher | docs/RESEARCH.md | done |
| R2 | Monitoring requirements + data sources | researcher | docs/RESEARCH.md | done |
| R3 | Embedding / 3D / overlay stack | researcher | docs/RESEARCH.md | done |
| W1 | Collectors: Claude, Codex, SDK ingest, liveness, flags | worker-collectors | neko/collectors/*, neko/procs.py, tests/test_collectors.py | done |
| W2 | Embedding + persona traits | worker-persona | neko/embed.py, neko/persona.py, tests/test_persona.py | done |
| W3 | 3D chibi characters, animation engine, room + HUD | worker-web | web/** | done |
| W4 | Daemon/server, CLI, launchd, hooks installer, Electron overlay, README | worker-runtime | neko/server.py, neko/cli.py, neko/hooks.py, overlay/**, scripts/**, README.md, pyproject.toml, Makefile | done |
| S1 | Supervisor: integration review, end-to-end run, screenshots, fixes | supervisor | all | done |

Rules for workers
- `neko/model.py` and the persona schema in DESIGN.md are the contract. Don't change them without noting it in your report.
- Python ≥ 3.9, stdlib-only core. Optional deps: `model2vec` (embedding). No torch.
- Never write to `~/.claude` or `~/.codex` except the opt-in hook installer (with backup).
- Tests: `python3 -m pytest -q tests` must pass offline.

## S1 review notes (2026-09-24)

Fixed during integration: duplicate names for identical sibling prompts; ~1.5 GB
daemon RSS (model now in an idle-exiting child process); hooks backup overwritten on
uninstall; IPv6 loopback bind; characters bunching in the middle and occluding each
other (best-candidate placement, size-scaled separation, decor obstacles, crowd
shrink); camera now frames the population; stray cushion behind the HUD; ~20 % fewer
draw calls (static merge, no outlines on tiny parts); frog headbands over the eyes;
very dark coats rendering as black blobs; three.js vendored + CSP (page is offline).

## Next steps

1. Real-machine soak test of `neko install` (launchd) and the overlay across Spaces /
   fullscreen apps / multiple displays (only tested with env overrides + brief runs).
2. Codex hook payloads: verify field names against a live codex-cli session with
   `neko hooks install --codex` (collector is transcript-first, so this is a speed-up).
3. Jump-to-terminal from a character's card (iTerm2 / Terminal.app / tmux by pid → tty).
4. Native notification when an agent starts `waiting` (overlay tray or `osascript`).
5. Label placement: smooth tag nudging (currently greedy per frame, can flicker in
   a crowd of 30+).
6. Instanced rendering for very large crowds (> 50 agents) and a Swift/SpriteKit
   overlay as a lighter alternative to Electron.
7. Import Codex `~/.codex/pets` packs as alternative skins.
