# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, privacy-first, model-agnostic memory layer for AI assistants. It mimics human memory (layered recall, forgetting, protected emotional memories) rather than a flat database. Everything runs offline after a one-time embedding-model download; nothing leaves the machine.

`PROJECT_STATUS.md` is the narrative source of truth (full evaluation, per-version specs, rationale). `docs/architecture.md` covers the design; `docs/decisions/` holds one ADR per major decision; `docs/experiments.md` records what was actually tested. When making a non-trivial design choice, check for a relevant ADR first — decisions here are deliberate and documented.

## Commands

```bash
# Install (Python >= 3.10)
python -m venv .venv
.venv\Scripts\activate            # Windows (bash: source .venv/bin/activate)
pip install -r requirements.txt

# Run: startup forgetting catch-up -> background scheduler -> MCP server (stdio)
python main.py

# Run just the MCP server (no scheduler / catch-up)
python mcp_server.py

# Syntax-check everything (the project's baseline structural test)
python -m py_compile config.py main.py mcp_server.py scheduler.py memory/*.py

# Inspect the archive directly
sqlite3 data/archive.db "SELECT id, content, importance_score, tags FROM memories ORDER BY importance_score DESC LIMIT 20;"
```

Note: the venv Python is `.venv/Scripts/python.exe` on Windows; MCP client configs should point at that interpreter so dependencies resolve.

### Testing

There is **no committed test suite**. The `test_phase*.py` scripts referenced in `docs/experiments.md` were run during development but are not in the repo. Verification is documented as experiments (Question → Method → Result → Conclusion) in `docs/experiments.md` — add an entry there when you verify a change. The first-line check is `py_compile` across all modules.

The embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, ~470 MB) downloads from Hugging Face on first tool use and needs internet **once**. Sandboxes without network access cannot exercise real retrieval/storage — this is why several experiments use fake random embeddings.

## Architecture: the two things that matter most

### 1. Two entry points, one shared core — because of an MCP protocol constraint

Both `mcp_server.py` and `memory/gateway.py` sit on top of the same `memory/` package and never duplicate storage/retrieval logic. They differ only in **when** that logic runs:

- **`mcp_server.py`** — for MCP-native clients (Claude Desktop/Code). Exposes tools (`get_context`, `store_memory`, `update_warm_attribute`) over stdio. It is **passive**: MCP is client-driven, so the server *cannot force* the client to call a tool on any given turn. All guarantees here are best-effort. Mitigations: strengthened `instructions` text, and `get_context` opportunistically auto-stores the incoming user message as a side effect (the tool clients call most reliably).
- **`memory/gateway.py`** (`MemoryGateway`) — for direct API integrations (custom OpenAI/Gemini/Claude wrappers). Plain Python, so it *can* guarantee behavior: `build_context()` runs unconditionally before the model, `auto_store_turn()` runs unconditionally after. `process_turn()` wraps the full loop. This is the **recommended** integration path and the only one where "always inject / always store" actually holds. See `ADR-001`, `ADR-002`.

When changing retrieval/storage behavior, remember it must work through **both** paths.

### 2. Storage decisions are rule-based, not LLM-based (deliberate)

`memory/auto_extract.py` decides what to store, how important it is, and emotional weight — with **no LLM call**. This is intentional: a wrong *retrieval* judgment just misses a memory, but a wrong *storage* judgment loses information permanently, so storage uses a predictable, auditable rule set (`ADR-003`).

It's structured as a **pluggable Rule Engine** (`ADR-004`): `FillerSkipRule`, `IdentitySignalRule`, `EmotionalSignalRule`, `WarmAttributeRule`. **Add a new `Rule` class rather than growing flat pattern lists.** Rules and patterns are **bilingual (English + Arabic)** throughout — any new signal pattern must cover both.

**Arabic gotcha:** never gate on raw character length. A complete Arabic sentence can be shorter than an English one (e.g. "تزوجت الشهر الماضي" = "I got married last month", 19 chars). The filler-skip check uses **word count**, with a high-signal exemption so short identity/emotional phrases (e.g. "اسمي ديب") are never dropped. This was a real shipped bug — see `experiments.md` E6 / E13.

## The memory layers

| Layer | Storage | Loaded | Semantics |
|---|---|---|---|
| **Fast Layer** | `data/fast_layer.json` (`fast_layer.py`) | Always, whole | Human-editable core identity. Never searched. |
| **Warm Layer** (v2) | `warm_layer` table in `archive.db` (`warm_layer.py`) | On semantic relevance | **Upsert by key** — new value replaces old (no contradictory current facts). |
| **Archive** | `memories` table in `archive.db` (`archive.py`) | Semantic search every message (v2.4, `ADR-011`) | **Append-only.** Every entry permanent until the forgetting cycle prunes it. |
| Task Layer | — | — | Planned (v3). `active_task_id` in Fast Layer is a placeholder. |

**Retrieval scoring (v2.4, `ADR-011`):** inclusion is decided by **raw cosine similarity only** (`RETRIEVAL_SIM_THRESHOLD` / `WARM_LAYER_SIM_THRESHOLD`); importance ranks passers but never admits an entry. Keyword signals (an entry's own tags, warm `context_hint` content words) are similarity *boosts*, never gates — the pre-v2.4 keyword trigger gate silently dropped high-confidence matches (`experiments.md` E18). `tools/replay_retrieval.py` is the acceptance benchmark for any retrieval-scoring or embedding-model change.

**Dual routing (`ADR-008`):** when `extract_warm()` matches a biographical fact, the Gateway/server upserts it into the Warm Layer *and* appends it to the Archive — the upsert keeps the "current value" clean while the Archive preserves history. A change to warm-attribute extraction must respect both destinations.

All embeddings are 384-dim, stored as raw byte BLOBs. `archive.db` holds three tables: `memories`, `system_state` (key/value bookkeeping, e.g. `last_forgetting_run`), and `warm_layer`.

## Forgetting cycle

`importance = frequency×0.4 + recency×0.3 + emotional_weight×0.3` (weights in `config.py`). Delete if score < 0.2 and age > 30d; compress if score < 0.4 and age > 90d; `emotional_weight == 1.0` is never deleted.

The cycle runs via **startup catch-up** (`scheduler.run_startup_catchup()` in `main.py`), not (primarily) a live timer — MCP processes are short-lived and a weekly in-process timer usually never fires. On startup it checks `last_forgetting_run` and runs immediately if overdue. The live weekly timer is a secondary mechanism for long-running deployments. See `ADR-006`.

**Timezone rule:** all timestamps are stored/compared as UTC-aware. Pre-v2 rows stored naive ISO strings; `_row_to_entry()` in `archive.py` re-tags them as `timezone.utc` on read. Always use `datetime.now(timezone.utc)` — a naive datetime reaching `forgetting.py` crashes the cycle (the v2.1 hotfix, `experiments.md` E16).

## Conventions

- **Config lives in `config.py`.** Paths, model name, scoring weights, and thresholds are centralized there — don't hardcode them elsewhere.
- **Additive, non-breaking versions.** Each roadmap version adds exactly one capability; existing MCP tools (`get_context`, `store_memory`) must keep working. New tools are additive. See `docs/roadmap.md`.
- **Data models** (`memory/models.py`) are plain dataclasses with `to_dict()`; embeddings are never exposed in `to_dict()`. Use `ensure_ascii=False` when JSON-encoding for output so Arabic renders.
- **Logs go to stderr** (stdout is reserved for the MCP JSON-RPC protocol).
