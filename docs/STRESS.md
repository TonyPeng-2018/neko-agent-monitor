# Stress test results (2026-09-24, MacBook Pro M5 Max)

Reproduce the backend part with `python scripts/stress.py`. It runs an isolated daemon on
port 8790 with temp `NEKO_HOME` / `~/.claude` / `~/.codex`, so your real data is never touched.

## Backend (daemon)

| Phase | Load | Result |
|---|---|---|
| A. Claude sessions | 40 live sessions (real `sleep` pids) + 80 subagents, 99 MB of transcripts incl. one 64 MB file | full snapshot in 2.1 s; collect 72 ms; `/api/agents` p50 0.8 ms / p95 1.2 ms (151 KB); 64 MB file parsed head+tail (`partial` flag); 0 duplicate names |
| B. SDK ramp | +50 / +150 / +300 runs via `/api/ingest` (420 agents total) | visible ≤ 0.5 s; collect 65–82 ms; p95 ≤ 10 ms; payload 465 KB |
| C. Hook flood | 5 000 `/api/event` POSTs from 32 threads | **4 072 events/s, 0 errors**, p50 0.9 ms, p99 94 ms; health responsive right after |
| D. SSE fan-out | 50 concurrent `/api/stream` clients | 50/50 received the push within 0.1 s |
| E. Cool-down | all runs finished | CPU back to ~3 % |

No errors in the daemon log.

**Memory.** The daemon alone is ~50 MB. The embedding worker (a child process) adds
~1.1–1.5 GB while it is alive, mostly the 500k-token multilingual tokenizer (~730 MB).
It only starts when a new agent needs a persona and exits after 90 s idle
(`NEKO_EMBED_IDLE`), so it is a short spike, not a resident cost. Pre-quantising the
weights to int8 would save ~350 MB (cosine ≥ 0.99 vs fp32) but would change existing
characters' looks; not done.

## Front end (Chrome, Metal GPU, vsync off)

| Characters | Room fps | Overlay fps | Draw calls | Triangles |
|---|---|---|---|---|
| ~33 | 231 | 406 | 1.4 k | 1.2 M |
| ~66 | 126 | 179 | 2.8 k | 2.4 M |
| ~107 | 76 | 96 | 4.5 k | 4.0 M |
| ~160 | 46 | 55 | 6.6 k | 5.9 M |

60 fps holds up to ~100 simultaneous agents. Above that, adaptive resolution lowers the
pixel ratio (down to 1×) until frames are under ~25 ms again.

## Churn / leak (10 min, 170 characters spawned and removed)

Before the fix, GPU geometries crept up (209 → 301 in 5 min): cache keys include
per-persona colours and proportions, and cached geometries/materials were never
released. Now every build retains what it touches and releases it on dispose.

After the fix, over 10 min: GPU geometries 215–266, cached geometries 87–102, materials
218–297, textures 70–100, JS heap 24–27 MB. **All flat.**
