<div align="center">

# Human Memory System

**A local, privacy-first memory layer that gives AI assistants human-like memory.**

All data stays on your machine. No cloud dependency. Works with any AI model that speaks MCP, or with a direct API integration.

</div>

---

Most AI assistants forget everything the moment a conversation ends. This system adds a persistent memory layer that works the way human memory works, not the way a database works:

- **Always knows your core identity** — name, preferences, language, values
- **Remembers past conversations and surfaces them when relevant** — without loading everything at once
- **Forgets trivial details over time**, but protects emotionally significant memories permanently
- **Integrates with any AI** via the standard MCP protocol, or via a direct API wrapper

> **New in v2:** the Warm Layer! A fast, semantic-relevance-driven layer for context-specific biographical attributes (e.g. location, occupation) with upsert semantics to prevent contradictory current facts. Also includes a fully pluggable Rule Engine for auto-extraction. See [`docs/changelog.md`](docs/changelog.md) for the full diff from v1.1.

---

## Table of Contents

- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the server](#running-the-server)
- [Connecting an AI model](#connecting-an-ai-model)
- [MCP Tools reference](#mcp-tools-reference)
- [How the forgetting system works](#how-the-forgetting-system-works)
- [Data directory](#data-directory)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Documentation](#documentation)
- [Privacy](#privacy)
- [Contributing](#contributing)
- [License](#license)

---

## How it works

The system is organized into layers with different retrieval costs, mirroring how human memory doesn't load everything at once:

| Layer | Status | What it holds | Loaded |
|---|---|---|---|
| **Fast Layer** | ✅ Built | Core identity: name, language, traits, preferences, values | Always, every request |
| **Archive** | ✅ Built | All past conversations/facts — SQLite + local embeddings | Only when retrieval is triggered |
| **Warm Layer** | ✅ Built | Secondary attributes (biography, context-specific preferences) | On semantic relevance |
| Task Layer | 🔲 Planned | One active project's working state | On task switch |

Full detail on every layer, built and planned, lives in [`docs/architecture.md`](docs/architecture.md).

---

## Architecture

```
                         ┌─────────────────────────────┐
                         │        Calling AI Model       │
                         │   (Claude / GPT / Gemini /…)  │
                         └───────────┬───────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                  │
          MCP-native client                  Direct API integration
        (Claude Desktop/Code)              (custom OpenAI/Gemini/Claude
                    │                        wrapper — see below)
                    ▼                                  ▼
         ┌─────────────────────┐          ┌─────────────────────────┐
         │   mcp_server.py       │          │   memory/gateway.py       │
         │  (stdio, protocol-     │          │  (deterministic —          │
         │  driven — client        │          │  ALWAYS injects context,   │
         │  decides when to call)  │          │  ALWAYS auto-stores)       │
         └──────────┬───────────┘          └─────────────┬─────────────┘
                    │                                     │
                    └───────────────┬─────────────────────┘
                                     ▼
                    ┌───────────────────────────────┐
                    │        Core memory package       │
                    │  Fast Layer · Archive · Retrieval  │
                    │  Auto-extraction · Forgetting       │
                    └───────────────────────────────┘
```

Two entry points exist because of a real protocol constraint, not by choice — see [Known limitations](#known-limitations) below and [`ADR-001`](docs/decisions/ADR-001-use-mcp-protocol.md) / [`ADR-002`](docs/decisions/ADR-002-memory-gateway-for-reliability.md) for the full reasoning.

---

## Requirements

- Python 3.10 or later
- ~600 MB disk space (multilingual embedding model downloads on first use)
- No internet connection required after first run

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/DeebHajjar/human-memory.git
cd human-memory

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

The `paraphrase-multilingual-MiniLM-L12-v2` embedding model (~470 MB) downloads automatically the first time you call a tool. After that, everything runs fully offline.

---

## Configuration

### 1. Fill in your Fast Layer

Edit `data/fast_layer.json` — this is what the AI always knows about you:

```json
{
  "name": "Your Name",
  "age": 30,
  "language": "en",
  "personality_traits": ["analytical", "detail-oriented"],
  "key_preferences": ["direct answers", "code examples"],
  "values": ["privacy", "efficiency"],
  "active_task_id": null
}
```

This file is human-readable and human-editable at any time. Changes take effect on the next request.

### 2. Tune constants (optional)

All thresholds live in `config.py`:

| Constant | Default | Meaning |
|---|---|---|
| `TOP_K_RESULTS` | 5 | Max memories returned per query |
| `RETRIEVAL_SCORE_THRESHOLD` | 0.30 | Minimum combined score to include a memory |
| `FORGET_LOW_SCORE` / `FORGET_LOW_DAYS` | 0.2 / 30 | Delete below this score after N days |
| `FORGET_MID_SCORE` / `FORGET_MID_DAYS` | 0.4 / 90 | Compress below this score after N days |
| `RECENCY_HALF_LIFE_DAYS` | 60 | Exponential decay half-life for recency score |
| `AUTO_STORE_MIN_CHARS` | 20 | Minimum length before a message is considered for auto-storage |

---

## Running the server

```bash
python main.py
```

This starts:
1. A **startup catch-up check** — runs the forgetting cycle immediately if it's overdue (see [`ADR-006`](docs/decisions/ADR-006-forgetting-cycle-startup-catchup.md))
2. A background scheduler — secondary live weekly timer, for long-running deployments
3. The MCP server on stdio (reads JSON-RPC from stdin, writes to stdout)

Logs go to stderr so they don't interfere with the MCP protocol on stdout.

---

## Connecting an AI model

The server speaks the standard MCP protocol over stdio, or can be used as a plain Python library via the Gateway. How you connect depends on your setup:

### Claude (Claude Code / Claude Desktop)

Add the server to your MCP config.

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "human-memory": {
      "command": "python",
      "args": ["/absolute/path/to/human-memory-system/main.py"]
    }
  }
}
```

**Claude Code** — add to `.claude/mcp.json` in your project:

```json
{
  "mcpServers": {
    "human-memory": {
      "command": "python",
      "args": ["main.py"]
    }
  }
}
```

> **Note:** You may need to add "command" to activate the virtual environment as follows: "command": ".venv/Scripts/python.exe"

> **Note:** MCP is a client-driven protocol — this server cannot force Claude Desktop/Code to call its tools on every turn. See [Known limitations](#known-limitations).

### Any LLM — recommended pattern: use the Gateway

For any custom integration (OpenAI, Gemini, or a direct Claude API wrapper that isn't going through an MCP-native client), use `memory/gateway.py` instead of calling the raw MCP tools yourself. The Gateway makes context injection and storage **deterministic** — it does not depend on the model choosing to call a tool:

```python
from memory.fast_layer import FastLayerManager
from memory.archive import ArchiveDB
from memory.retrieval import RetrievalEngine
from memory.gateway import MemoryGateway
from sentence_transformers import SentenceTransformer
from config import FAST_LAYER_PATH, ARCHIVE_DB_PATH, EMBEDDING_MODEL

embedder = SentenceTransformer(EMBEDDING_MODEL)
fl_mgr   = FastLayerManager(FAST_LAYER_PATH)
archive  = ArchiveDB(ARCHIVE_DB_PATH)
engine   = RetrievalEngine(archive, embedder=embedder)
gateway  = MemoryGateway(fl_mgr, archive, engine, embedder=embedder)

def call_my_llm(memory_block: str, user_message: str) -> str:
    # Wire this to OpenAI / Gemini / Claude API — whatever you're using
    system_prompt = f"You are a helpful assistant.\n\n{memory_block}"
    return my_llm_client.chat(system_prompt, user_message)

# One call does everything: inject context, call the model, auto-store
reply = gateway.process_turn(user_message, call_my_llm)
```

`gateway.process_turn()` always:
1. Loads the Fast Layer and runs retrieval — regardless of message content
2. Calls your model with the enriched prompt
3. Runs rule-based extraction on both the user's message and the model's reply, and stores whatever clears the bar — no tool-call decision required from the model

**Why not just call `get_context` / `store_memory` manually?** You can — but then storage only happens if your own wrapper code remembers to call `store_memory` after every response. The Gateway removes that risk by making storage automatic and rule-based instead of optional.

<details>
<summary><strong>Manual MCP client pattern (OpenAI / Gemini)</strong> — click to expand</summary>

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def call_memory_tool(tool_name: str, args: dict) -> str:
    params = StdioServerParameters(command="python", args=["/path/to/main.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            return result.content[0].text

context = asyncio.run(call_memory_tool("get_context", {"message": user_message}))
system_prompt = f"You are a helpful assistant.\n\n[Memory Context]\n{context}"

# ... call OpenAI / Gemini with system_prompt ...

asyncio.run(call_memory_tool("store_memory", {
    "content": f"User said: {user_message}",
    "source": "user",
}))
```

The Gateway pattern above is recommended over this for anything beyond a quick test — see [`ADR-002`](docs/decisions/ADR-002-memory-gateway-for-reliability.md).
</details>

---

## MCP Tools reference

### `get_context`

Call before generating a response.

**Input:** `{ "message": "The user's incoming message" }`

**Output:**
```json
{
  "fast_layer": {
    "name": "Deeb",
    "language": "ar",
    "personality_traits": ["analytical"],
    "key_preferences": ["direct answers"],
    "values": ["privacy"],
    "active_task_id": null
  },
  "retrieval_triggered": true,
  "retrieved_memories": [
    {
      "id": "3f2a...",
      "content": "User prefers dark mode in all UIs",
      "importance_score": 0.72,
      "emotional_weight": 0.0,
      "tags": ["preferences", "UI"],
      "source": "user",
      "access_count": 3
    }
  ]
}
```

`retrieval_triggered: false` means the archive was not searched (no past-context reference detected). The Fast Layer is still always returned.

### `store_memory`

Call after generating a response.

**Input:**
```json
{
  "content": "User is building a Django workout tracker with Bootstrap 5",
  "source": "user",
  "importance": 0.7,
  "tags": ["Django", "workout", "Bootstrap"],
  "emotional_weight": 0.0
}
```

`importance`, `tags`, and `emotional_weight` are optional — if omitted, they're auto-derived by rule-based extraction (`memory/auto_extract.py`) instead of falling back to a flat default.

**`source` values:** `"user"` · `"assistant_speech"` · `"assistant_thought"` (reserved for a future version)

**`emotional_weight = 1.0`** means this memory is never deleted, only potentially compressed after very long periods.

**Output:** `{ "stored": true, "id": "3f2a-..." }`

---

## How the forgetting system works

On a weekly cycle (with a startup catch-up so it doesn't depend on the process staying alive), every memory is scored:

```
importance = frequency × 0.4 + recency × 0.3 + emotional_weight × 0.3
```

| Rule | Condition | Action |
|---|---|---|
| Delete | score < 0.2 and age > 30 days | Permanently removed |
| Compress | score < 0.4 and age > 90 days | Truncated to a short summary, re-embedded |
| Protect | emotional_weight = 1.0 | Never touched |

This mirrors how humans forget trivial details naturally but remember significant events forever.

---

## Data directory

```
data/
├── fast_layer.json   ← Edit directly to update your core identity
└── archive.db        ← SQLite database (inspectable with any SQLite viewer)
```

```bash
# Inspect the archive directly
sqlite3 data/archive.db "SELECT id, content, importance_score, tags FROM memories ORDER BY importance_score DESC LIMIT 20;"
```

---

## Project structure

```
human-memory-system/
├── config.py               All constants and paths
├── main.py                 Entry point (startup catch-up → scheduler → MCP server)
├── mcp_server.py            MCP tools (get_context, store_memory)
├── scheduler.py             Forgetting-cycle catch-up + secondary live timer
├── requirements.txt
├── data/
│   ├── fast_layer.json      Your core identity (edit directly)
│   └── archive.db           SQLite archive (auto-created)
├── memory/
│   ├── models.py             MemoryEntry, FastLayer, LayeredContext
│   ├── fast_layer.py         Fast layer read/write
│   ├── archive.py             SQLite operations + embeddings
│   ├── retrieval.py           Keyword triggers + semantic search
│   ├── forgetting.py          Importance scoring + pruning logic
│   ├── auto_extract.py        Rule-based storage/importance extraction
│   └── gateway.py              Deterministic wrapper for direct API use
└── docs/
    ├── architecture.md         Full system design
    ├── roadmap.md               Current plan, version by version
    ├── changelog.md             What changed between versions
    ├── experiments.md           Tests run and their results
    └── decisions/
        └── ADR-001, ADR-002, …  One file per decision (see docs/decisions/README.md)
```

---

## Known limitations

Documented honestly rather than hidden — see [`docs/decisions/`](docs/decisions/README.md) for the full reasoning behind each:

- **MCP is client-driven.** The MCP server cannot force an MCP-native client (Claude Desktop/Code) to call `get_context`/`store_memory` on every turn — that's a protocol property, not a bug here ([ADR-001](docs/decisions/ADR-001-use-mcp-protocol.md)). The `Gateway` fully solves this only for direct API integrations that call it directly ([ADR-002](docs/decisions/ADR-002-memory-gateway-for-reliability.md)).
- **Keyword-only retrieval triggers** (current version) will under-fire on subtle references and can over-fire on generic tag words. An LLM-based judgment fallback is planned (see roadmap).
- **Linear-scan retrieval.** No vector index yet — fine at small-to-medium archive sizes, with a documented migration trigger once it isn't. See [`docs/architecture.md`](docs/architecture.md) §7 and `PROJECT_STATUS.md` §5.
- **No contradiction/duplicate detection yet.** Two conflicting facts can both be stored and retrieved together. Planned as a future version (`v7 — Memory Consistency`).

---

## Roadmap

| Version | Adds |
|---|---|
| v1 / v1.1 ✅ | Fast Layer, Archive, keyword retrieval, forgetting, Gateway, multilingual embeddings |
| v2 ✅ | Warm Layer — secondary attributes, two-pass retrieval, upsert semantics |
| v3 | Task Layer — one active task, switchable, persists state |
| v4 | LLM-based retrieval judgment — replaces keyword-only trigger |
| v5 | AI internal thought memory (`assistant_thought` source) |
| v6 | Topic-switching buffer within a single conversation |
| v7 | Memory Consistency — contradiction detection, fact replacement, duplicate merging |

Each version adds exactly one capability; no breaking changes to the MCP interface. Full detail, open questions, and success metrics per version: [`docs/roadmap.md`](docs/roadmap.md).

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Full system design: layers, request flow, package layout |
| [`docs/decisions/`](docs/decisions/README.md) | Why each major decision was made — one ADR per decision, alternatives considered |
| [`docs/experiments.md`](docs/experiments.md) | What was tested, method, results — including bugs found and fixed |
| [`docs/roadmap.md`](docs/roadmap.md) | Current plan, version by version, with success metrics |
| [`docs/changelog.md`](docs/changelog.md) | Condensed diff between v1 and v1.1 |
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Full narrative source of truth — evaluation, fixes, detailed specs |

---

## Privacy

- Zero telemetry. Nothing leaves your machine.
- The embedding model runs fully locally after the one-time download.
- The SQLite database and `fast_layer.json` are plain files you own completely.
- The MCP server uses stdio — it never opens a network port.

---

## Contributing

Issues and pull requests are welcome. Before proposing a new feature, please check [`docs/roadmap.md`](docs/roadmap.md) — each version is scoped to add exactly one capability, and larger changes are easier to review if they fit that shape. For anything touching `memory/auto_extract.py`, note the planned pluggable rule-engine refactor in [`ADR-004`](docs/decisions/ADR-004-pluggable-rule-engine.md) before adding new pattern lists.

---

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

## 📞 Support

For questions or support regarding this project, please contact:

    Developer: Deeb Hajjar
    Email: deebhajjar04@gmail.com
