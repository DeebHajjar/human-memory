# Architecture

*Human Memory System — v2*

This document describes the overall design of the system: the layers, how data flows through them, and how the pieces fit together. For *why* specific choices were made, see `decisions/` (one ADR per decision). For version-by-version history, see `changelog.md`. For what's planned next, see `roadmap.md`.

---

## 1. Design goals

The system is built around four constraints that shape every design decision below:

1. **Model-agnostic** — any AI that speaks the MCP protocol (Claude, GPT, Gemini, ...) can use it. The server never assumes a specific model on the other end.
2. **Local-first** — no cloud dependency. Embeddings are generated locally; the archive is a local SQLite file; nothing leaves the machine unless the person explicitly wires in an external service themselves.
3. **Layered, not flat** — information is organized into layers with different retrieval costs (always-present vs. retrieved-on-demand), mirroring how human memory doesn't load everything at once.
4. **Practical, not a brain simulation** — see "Scope & Non-Goals" in `PROJECT_STATUS.md` §1. Human-memory concepts (forgetting, significance, layering) are borrowed only where they solve a concrete engineering problem.

---

## 2. High-level diagram

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
                    │                        wrapper — see README.md)
                    ▼                                  ▼
         ┌─────────────────────┐          ┌─────────────────────────┐
         │   mcp_server.py      │          │   memory/gateway.py       │
         │  (stdio, passive —    │          │  (deterministic —          │
         │  client decides when  │          │  ALWAYS injects Fast Layer,│
         │  to call tools)        │          │  ALWAYS auto-stores)       │
         └──────────┬───────────┘          └─────────────┬─────────────┘
                    │                                     │
                    └───────────────┬─────────────────────┘
                                     ▼
                    ┌───────────────────────────────┐
                    │        Core memory package       │
                    │            (memory/)              │
                    └───────────────┬───────────────────┘
                                     │
         ┌───────────────┬──────────┼──────────┬───────────────┐
         ▼               ▼          ▼          ▼               ▼
   FastLayerManager  RetrievalEngine  ArchiveDB  auto_extract  forgetting
   (fast_layer.json)  (retrieval.py)  (SQLite +   (rule-based   (scoring +
                                       embeddings)  extraction)   pruning)
```

Two entry points exist because of a hard protocol limitation, not by choice — see Section 4 below and `decisions/ADR-001-use-mcp-protocol.md` / `decisions/ADR-002-memory-gateway-for-reliability.md`.

---

## 3. The four memory layers

Only two of these are implemented (v1/v1.1); the other two are specified but not yet built, per the roadmap.

| Layer | Status | What it holds | Loaded |
|---|---|---|---|
| **Fast Layer** | ✅ Built | Core identity: name, age, language, personality traits, key preferences, values, active task id | Always, every request |
| **Warm Layer** | ✅ Built (v2) | Secondary attributes: birthdate, location, occupation, recurring habits, context-specific preferences | On semantic relevance, faster than Archive |
| **Task Layer** | 🔲 Planned (v3) | One active project's working state: recent decisions, focus, open questions | On task switch |
| **Archive** | ✅ Built | All past conversations/facts, SQLite + local embeddings | On retrieval trigger only |

### 3.1 Fast Layer

A single JSON file (`data/fast_layer.json`), read and written by `memory/fast_layer.py`. Intentionally small and human-editable — someone can open the file directly and correct it. Never searched; always loaded whole.

### 3.2 Warm Layer (v2)

A `warm_layer` table in `data/archive.db`, managed by `memory/warm_layer.py`. Each row is one `WarmAttribute`: a `key` (semantic category), `value` (full text), `context_hint` (when to surface it), `embedding` (384-dim BLOB), `importance`, and `last_updated`.

Unlike the Archive (which appends entries), the Warm Layer uses **upsert semantics**: storing a new `location` value replaces the old one — no duplicate "I live in Dubai" + "I live in London" conflict. Two-pass retrieval: keyword match on `context_hint` first (fast path), then cosine similarity for remaining candidates. Threshold is 0.45 (vs Archive's 0.30) because warm attributes are specific stable facts where false positives are costly.

### 3.3 Archive

A local SQLite database (`data/archive.db`), managed by `memory/archive.py`. Each row is one `MemoryEntry`: content, an optional compressed summary, a 384-dimension embedding stored as a BLOB, and a set of scores (`importance_score`, `frequency_score`, `recency_score`, `emotional_weight`) plus metadata (`tags`, `source`, `timestamp`, `last_accessed`, `access_count`).

A second table, `system_state` (added in v1.1), stores simple key/value bookkeeping — currently just `last_forgetting_run`. A third table, `warm_layer` (added in v2), is the Warm Layer described above — same file, separate concern.

---

## 4. Two entry points: MCP Server vs. Gateway

This is the most important structural fact about the system and the thing most likely to confuse a new contributor, so it's called out explicitly here.

### 4.1 `mcp_server.py` — for MCP-native clients

Exposes two tools (`get_context`, `store_memory`) over the standard MCP protocol on stdio transport. This is what Claude Desktop, Claude Code, or any other MCP-compatible client talks to.

**Critical property: this server is entirely passive.** It cannot decide on its own to inject context or store a memory — it can only respond when the client's model chooses to call one of its tools. This is a property of the MCP protocol itself (client-driven), not a limitation specific to this codebase. See `decisions/ADR-001-use-mcp-protocol.md` and `decisions/ADR-002-memory-gateway-for-reliability.md` for the full reasoning and what was done to mitigate it.

### 4.2 `memory/gateway.py` — for direct API integrations

`MemoryGateway` is a plain Python class, not an MCP tool. It's meant to be called directly by custom code wrapping the OpenAI, Gemini, or Claude API. Because it's regular code rather than a protocol-mediated tool call, it can make guarantees the MCP server cannot:

- `build_context()` — called unconditionally before every model call.
- `auto_store_turn()` — called unconditionally after every model call, using rule-based extraction (`memory/auto_extract.py`) instead of waiting for the model to decide what's worth storing.
- `process_turn()` — convenience wrapper running the full loop in one call.

**Both entry points share the same underlying `memory/` package** — `FastLayerManager`, `ArchiveDB`, `RetrievalEngine`. The Gateway does not duplicate any storage or retrieval logic; it only changes *when* that logic runs (always, vs. only when a model chooses to invoke a tool).

---

## 5. Request flow

### 5.1 Retrieval (`get_context` / `build_context`)

```
incoming message
      │
      ▼
Fast Layer always loaded (FastLayerManager.load())
      │
      ├──────────────────────────────────────────────────────────┐
      │                                                           │
      ▼ (always)                                                 ▼ (always, v2)
Step 1 — keyword trigger check (RetrievalEngine.should_retrieve()) Warm Layer retrieval (WarmLayerManager.retrieve_relevant())
  • time/context reference phrases (bilingual EN/AR)              • keyword match on context_hint (fast path)
  • known-tag word match                                          • cosine similarity on embeddings (fallback)
      │                                                           • threshold: 0.45
      ├── no match ──────────────────────────► return Fast Layer + warm_attributes
      │
      ▼ match
Step 2 — semantic search (RetrievalEngine.retrieve())
  • embed the message (sentence-transformers, local)
  • cosine similarity against all archive embeddings
  • combined_score = 0.7 × similarity + 0.3 × importance_score
  • top_k results above threshold, access stats updated
      │
      ▼
return Fast Layer + warm_attributes + retrieved memories
```

Step 2's "LLM judgment" fallback described in the original spec is intentionally not built yet — that's v4 (see `roadmap.md`).

### 5.2 Storage (`store_memory` / `auto_store_turn`)

```
text (user message and/or assistant reply)
      │
      ▼
memory/auto_extract.py — rule-based, no LLM call
  • should_skip()? (filler, too short) ──► discard, nothing stored
  • high-signal phrase match? ──► importance raised
  • emotional-signal phrase match? ──► emotional_weight = 1.0
  • naive tag extraction (capitalized words)
      │
      ▼
embed content locally (sentence-transformers)
      │
      ▼
ArchiveDB.store() — insert into SQLite with embedding BLOB
```

The MCP server's `store_memory` tool accepts explicit `importance`/`tags`/`emotional_weight` from the calling model, but falls back to this same rule-based extraction for any field the model omits — see `decisions/ADR-003-rule-based-auto-extraction.md` for why this fallback exists.

### 5.3 Forgetting cycle

```
process startup (main.py)
      │
      ▼
scheduler.run_startup_catchup()
  • check ArchiveDB.get_state("last_forgetting_run")
  • overdue (never run, or past the configured cycle length)?
      │
      ├── no ──► skip, nothing to do
      │
      ▼ yes
memory/forgetting.run_forgetting_cycle()
  • recompute importance_score for every entry
      (frequency × 0.4 + recency × 0.3 + emotional_weight × 0.3)
  • score < 0.2 AND age ≥ 30d ──► delete
  • score < 0.4 AND age ≥ 90d ──► compress to summary, re-embed
  • emotional_weight = 1.0 ──► always protected
      │
      ▼
ArchiveDB.set_state("last_forgetting_run", now)
```

A secondary live weekly timer (`scheduler.start_scheduler()`) also runs for the less common case of a long-lived process — but the startup catch-up above is what actually guarantees the cycle runs in typical short-lived MCP sessions. See `decisions/ADR-006-forgetting-cycle-startup-catchup.md`.

---

## 6. Package layout

```
human-memory-system/
├── config.py              Constants: paths, model name, scoring weights, thresholds (v2 adds Warm Layer constants)
├── main.py                Entry point: startup catch-up → scheduler → MCP server
├── mcp_server.py           MCP tools: get_context, store_memory, update_warm_attribute (v2)
├── scheduler.py            Forgetting-cycle catch-up + secondary live timer
├── data/
│   ├── fast_layer.json     Human-editable core identity
│   └── archive.db          SQLite: memories table + system_state table + warm_layer table (v2)
└── memory/
    ├── models.py           MemoryEntry, FastLayer, WarmAttribute (v2), LayeredContext dataclasses
    ├── fast_layer.py        Read/write fast_layer.json
    ├── archive.py           SQLite operations, embedding storage, system_state
    ├── warm_layer.py        WarmLayerManager — upsert + two-pass retrieval (v2)
    ├── retrieval.py         Keyword triggers + semantic search
    ├── forgetting.py        Importance scoring + pruning logic
    ├── auto_extract.py      Pluggable Rule Engine: FillerSkipRule, IdentitySignalRule,
    │                         EmotionalSignalRule, WarmAttributeRule (v2)
    └── gateway.py            MemoryGateway — deterministic wrapper for direct API use (v2 updated)
```

---

## 7. What's deliberately not in this architecture yet

Per the roadmap (`roadmap.md`), the following are specified but not built, and nothing above should be read as already supporting them:

- **Task Layer** (v3) — no active-task suspend/resume exists yet; `active_task_id` in the Fast Layer is a placeholder field.
- **LLM-based retrieval judgment** (v4) — retrieval is keyword-trigger-only; there is no fallback model call when Step 1 is ambiguous.
- **Vector index (FAISS/HNSW)** — retrieval is a linear scan over all embeddings. See `PROJECT_STATUS.md` §5 for the migration trigger conditions; nothing is built yet.
- **Consistency/contradiction handling** (v7) — the archive has no notion of a fact being superseded or duplicated; every stored entry is permanent until the forgetting system prunes it on its own schedule. Note: the Warm Layer's upsert semantics partially solve this for stable biographical facts (location, occupation, etc.), but general contradiction detection is still v7.

---

## 8. Related documents

- `PROJECT_STATUS.md` — full narrative history, evaluation, and detailed per-version specs (source of truth for anything not covered here).
- `decisions/` — one ADR per major decision (the Gateway/MCP split, rule-based extraction, etc.).
- `experiments.md` — what was actually tested, and the results.
- `roadmap.md` — current plan, version by version.
- `changelog.md` — condensed diff between v1 and v1.1.
