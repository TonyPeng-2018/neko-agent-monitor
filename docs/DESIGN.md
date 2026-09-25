# neko-agent-monitor — design

Every AI agent running on your Mac becomes a kawaii chibi animal on your screen.
Five agents running → five different kitties wandering around. Its look comes
from the prompt that created it; its size, animation and props come from live
monitoring data.

## Goals

1. **See at a glance** how many agents run, which one needs you, which is stuck,
   which is burning tokens or close to a full context.
2. **Cute.** Big head, tiny body, blush, toon shading, bouncy motion. Non-negotiable.
3. **Local and private.** No cloud. Prompts are read locally to seed the look and to
   show the task in a tooltip; only a hash + traits are persisted.
4. **Runs in the background** on macOS (launchd), light on CPU, survives reboot.
5. **Zero-config first**: works by tailing transcripts; hooks are an opt-in upgrade.

## Architecture

```
 Claude Code ─┐ ~/.claude/sessions/*.json (live registry)
              │ ~/.claude/projects/**/<sid>.jsonl (+ subagents/agent-*.jsonl)
 Codex CLI  ──┤ ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl + `ps` liveness
 Agents SDK ──┤ neko_tracing.NekoTracingProcessor → POST /api/ingest
 hooks (opt)──┘ POST /api/event  (Claude/Codex hook shim, instant state changes)
        │
        ▼
  neko daemon (Python stdlib http.server, 127.0.0.1:8765)
   collectors/  → list[Agent]  (neko/model.py is the contract)
   persona.py   → Agent.persona  (embedding of creation prompt → traits)
   server.py    → GET /api/agents, GET /api/stream (SSE), static web/
        │
        ▼
  web/ (three.js 0.186 procedural toon chibis, no build step)
   ├─ browser tab: "room" mode with HUD list + detail card
   └─ overlay/ Electron: transparent, click-through, always-on-top, cats walk
      along the bottom of the desktop; hover a cat for its card
```

## Mapping monitoring → character

| Signal | Character |
|---|---|
| agent created | new character pops in (spawn puff) |
| creation prompt embedding | species, hat, fur colour/pattern, outfit, face, prop, name |
| `working` (tool/model running) | walks around; typing on mini laptop for Edit/Write, magnifier for Read/Grep, hammer for Bash |
| `waiting` (permission / question) | hops + waves, "!" bubble, gentle glow — highest attention priority |
| `idle` | sits, slow blinks, looks at cursor |
| `sleeping` (quiet > 10 min or /loop asleep) | curled up, Zzz |
| `error` / stuck-loop flag | dizzy spiral eyes, orbiting stars |
| `done` | celebrates with confetti, then waves goodbye and walks off |
| context tokens / limit | body size (log-scaled 0.7×–1.6×); >80% → sweat drop |
| cost in last 10 min | coins pop above head; high burn → steam puffs |
| todo progress | tiny progress ring under feet |
| subagents | kittens (0.55× size, parent's palette ±) following the parent |
| source | Claude = warm collar tag, Codex = mint collar tag, SDK = lilac tag |

## Persona (prompt → look)

`neko/embed.py`: model2vec `minishlab/potion-multilingual-128M` (MIT, numpy only,
0.7 s load, 0.04 ms/text, EN↔ZH aligned). Fallback: deterministic hashing-trick
embedding (no deps). `emb_version` stored with every persona.

`neko/persona.py`:
- **Semantic** traits by softmax similarity to anchor centroids (EN+ZH anchors):
  role → hat + prop (research→detective hat+magnifier, coding→hard hat+laptop,
  design→beret+brush, testing→goggles+bug net, writing→quill+scarf,
  data→visor+chart, devops→headset+wrench, planning→crown+clipboard …),
  species by anchor (cat default, fox, bunny, bear, panda, frog, penguin, hamster).
- **Identity** traits by fixed seeded random projection → pastel HSL fur colour,
  pattern, eye style, ear size, chubbiness, blush, walk bounce.
- Name from a kawaii list by sha256(prompt). Hue collision avoidance among live
  agents by golden-angle rotation.
- Persisted: `~/.neko/personas.json` keyed by sha256(prompt) → traits (no prompt text).

## Monitoring design

- Works on macOS (no `/proc`): liveness via `ps`/psutil-free parsing, `procStart`
  date-string check.
- Adds OpenAI Codex CLI and Agents SDK.
- Explicit `waiting` state (permission prompt / question) — the #1 thing users want.
- Context % against per-model window, cost/10 min, stuck/loop/poll flags carried over.
- Live push (SSE) instead of polling.

## Running

- `neko serve` — daemon + web UI at http://127.0.0.1:8765
- `neko install` — launchd LaunchAgent (auto start, KeepAlive)
- `cd overlay && npm i && npm start` — desktop overlay
- `neko hooks install|uninstall` — opt-in Claude Code hooks (backs up settings.json)

## Persona schema (contract between persona.py and web/)

```json
{
  "v": 1, "emb_version": "potion-multilingual-128M" ,
  "name": "Mochi",
  "species": "cat|fox|bunny|bear|panda|frog|penguin|hamster",
  "role": "research|coding|design|testing|writing|data|devops|planning|chat",
  "hat": "none|detective|hardhat|beret|goggles|crown|headset|visor|beanie|flower|bow|wizard|chef",
  "prop": "none|magnifier|laptop|brush|bugnet|quill|chart|wrench|clipboard",
  "outfit": "none|scarf|hoodie|overalls|cape|bowtie|apron|sweater",
  "fur": "#f6d7c3", "fur2": "#fff6ee", "pattern": "solid|tabby|calico|tuxedo|socks|spots",
  "accent": "#8fb8ff", "eyes": "round|sparkle|sleepy|happy|dot", "eye_color": "#3b2a2a",
  "mouth": "cat|smile|o|tongue", "blush": true,
  "ear_size": 1.0, "chubby": 1.0, "tail": 1.0, "bounce": 0.5, "speed": 1.0
}
```
Numbers: ear_size 0.8–1.2, chubby 0.9–1.15, tail 0.7–1.3, bounce 0–1, speed 0.8–1.2.
The front end must render every enum value (unknown → "none"/"cat").
Subagents: persona derived from their own task prompt but `fur`/`accent` blended 60 % toward the parent.

## Borrowed, not reinvented

| What | From | License |
|---|---|---|
| hook → local daemon event design, state vocabulary | Pixel Agents, agent-pet-runtime, claude-pet | MIT (ideas + small snippets with attribution) |
| state set & idle→sleep timing | Clawd on Desk | AGPL — ideas only, **no code** |
| embedding model | model2vec potion-multilingual-128M | MIT |
| toon outline | three.js `OutlineEffect` addon | MIT |
| Codex pet pack format (`~/.codex/pets`) | OpenAI Codex | format only (import later) |
