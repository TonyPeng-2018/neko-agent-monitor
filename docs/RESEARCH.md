# Research notes (2026-09-24)

## Similar work — what we borrow

| Project | Stack / license | Detection | Rendering | Take |
|---|---|---|---|---|
| [Pixel Agents](https://github.com/pablodelucca/pixel-agents) ~9.4k★ | TS/React/Canvas, MIT | Claude hooks → local server; JSONL fallback | pixel office, 6 fixed sprites | hooks + transcript fallback, one character per (sub)agent |
| [Clawd on Desk](https://github.com/rullerzhou-afk/clawd-on-desk) ~6.3k★ | Electron, **AGPL** | Claude+Codex hooks, JSONL fallback, liveness, `POST /state` | APNG/SVG, 12 states | state vocabulary, idle→sleep timing — ideas only |
| [AgentPet](https://github.com/ntd4996/agentpet) | Swift/Tauri, MIT | hooks for 11 agents, `agentpet run --` wrapper | Codex pet pack | menu-bar list + overlay |
| [agent-pet-runtime](https://github.com/dncore/agent-pet-runtime) | Swift, MIT | hook shim → unix socket | NSPanel | privacy stance, state mapping |
| [claude-pet](https://github.com/IMMINJU/claude-pet) | Tauri, MIT | `--hook` stdin → TCP | 17 states | |
| Codex `/pet`, Claude `/buddy` | first-party | — | atlas / procedural seeded | `/buddy`: deterministic seeded traits → our prompt-embedding version |
| [BongoCat](https://github.com/ayangweb/BongoCat), Shimeji-ee, vscode-pets, AI Town | MIT etc. | — | Live2D / sprites | data-driven weighted behaviours |
| [ccusage](https://github.com/ryoppippi/ccusage), Claude-Code-Usage-Monitor, disler/…-observability | MIT | JSONL / hooks→SQLite | — | parsing knowledge, per-session stable colour |

Gap we fill: **one 3D character per agent, look derived from the creation prompt, Claude + Codex + Agents SDK, open source (MIT).**

## Data sources (verified on this Mac, Claude Code 2.1.282, codex-cli 0.155.1)

### Claude Code
- `~/.claude/sessions/<pid>.json`: `pid, sessionId, cwd, startedAt, procStart, kind, entrypoint, name, status ∈ {busy, idle, waiting}, waitingFor ∈ {permission prompt, input needed, dialog open, sandbox request, worker request, goal proposal}, statusUpdatedAt`. **`waiting` needs no hooks.**
- Liveness: `procStart == TZ=UTC LC_ALL=C ps -o lstart= -p PID` (whitespace-normalised) — guards pid reuse.
- Transcript `~/.claude/projects/<slug>/<sid>.jsonl` (slug = cwd with `/` and `.` → `-`). Records: user, assistant (`message.model`, `usage`), `ai-title`, `last-prompt`, `cost-state{totalCostUSD,…}`, system subtypes `turn_duration|api_error|compact_boundary|away_summary`, `attachment{type:model}`.
- First prompt: first `user` record, not meta/sidechain, string or text block, not starting with `<command-`, `<local-command`, `<task-notification>`; `-p` runs: `queue-operation.content`.
- Context = input + cache_creation + cache_read of last non-sidechain assistant usage. Window: 1M for sonnet-5, opus-4-7/4-8/5/5-5, fable-5*, mythos-5*, any `[1m]`; else 200k. `compact_boundary` resets.
- Subagents `<slug>/<sid>/subagents/agent-<id>.jsonl` + `.meta.json {agentType, description, toolUseId, spawnDepth}`; line 1 = task prompt.
- Hooks: 33 events; `type:"http"` hooks POST JSON directly to a URL (no helper script). Notification `notification_type ∈ {permission_prompt, idle_prompt, elicitation_dialog, …}`.

### Codex CLI
- `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<threadId>.jsonl`, lines `{timestamp, type, payload}`: `session_meta{id,cwd,originator}`, `turn_context{model}`, `event_msg` (`task_started{model_context_window}`, `task_complete`, `token_count{info{last_token_usage, total_token_usage, model_context_window}, rate_limits}`, `item_completed{item.type: UserMessage|AgentMessage|CommandExecution|FileChange}`), `response_item`.
- Busy = `task_started` without `task_complete`. Context = last_token_usage.input_tokens / model_context_window.
- `state_5.sqlite` (open read-only via python `file:…?mode=ro`): `threads(id, rollout_path, cwd, title, model, tokens_used, first_user_message, updated_at_ms, agent_nickname, agent_role…)`, `thread_spawn_edges(parent_thread_id, child_thread_id, status)` → subagent tree.
- Liveness: `ps` for codex processes, cwd via `lsof -a -p PID -d cwd -Fn`, open rollout via `lsof -p PID -Fn | grep rollout-`.
- Hooks `~/.codex/hooks.json` (SessionStart, PermissionRequest, PreToolUse, PostToolUse, Stop, …), `notify` in config.toml.

### OpenAI Agents SDK
`TracingProcessor` (`on_trace_start/finish`, `on_span_start/finish`) + `add_trace_processor()`; AgentSpanData / GenerationSpanData(usage, model) / FunctionSpanData / HandoffSpanData.

## What users want from an agent monitor (ranked)
1. Which agent needs me now (permission / question) + notification
2. Clear per-session state (working / waiting / idle / error / done)
3. Context fill % with near-compact warning
4. Cost / token burn rate, rate-limit %
5. Current activity & elapsed time
6. Subagent tree
7. Stuck / loop detection
8. Jump to the session's terminal
9. History / analytics
10. Zero-config (files first, hooks optional)

## Stack decisions
- Embedding: model2vec `potion-multilingual-128M` (0.7 s load, 0.04 ms/text, EN↔ZH cos 0.52–0.66); hashing fallback.
- Characters: procedural three.js toon chibis (full trait control; CC0 packs have no cute cat).
- Overlay: Electron transparent click-through panel (reuses the web page). Native Swift/SpriteKit is lighter — a possible v2.
- Background: launchd LaunchAgent.
