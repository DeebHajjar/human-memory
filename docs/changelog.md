# Changelog

*Human Memory System*

Condensed, scannable diff between versions. For the full reasoning behind any change, see `decisions/` (one ADR per decision). For the full narrative, see `PROJECT_STATUS.md`.

---

## v2.2 (Fix)

**Theme:** architectural correction — `get_context` was doing two jobs (retrieval + opportunistic storage); real usage showed this actively corrupted retrieval quality.

### Fixed
- **`mcp_server.py` — removed opportunistic auto-store from `get_context`:** the v1.1/v2 mitigation that had `get_context` silently call `auto_extract.extract_warm()` / `auto_extract.extract()` and write to the Warm Layer/Archive as a side effect was causing test/exploratory queries (e.g. "Where do I live?") to be stored verbatim as if they were memories, polluting later semantic retrieval. `get_context` is now a pure read/retrieval function — no calls to `archive.store()`, `warm_layer_mgr.upsert()`, or `auto_extract.extract*()` remain in it. See [`ADR-009`](decisions/ADR-009-remove-opportunistic-auto-store-from-get-context.md) and `PROJECT_STATUS.md` §5.6.

### Changed
- `mcp_server.py`: `FastMCP`'s `instructions` text and the `get_context` tool docstring updated to state plainly that `get_context` is read-only, and that `store_memory`/`update_warm_attribute` must be called explicitly to persist anything.

### Known limitations (reopened, not a regression from before v1.1)
- With the mitigation removed, storage for MCP-native clients (Claude Desktop/Code) once again depends entirely on the calling model choosing to call `store_memory`/`update_warm_attribute` — the same gap ADR-001/ADR-002 originally described. This is now treated as the correct trade-off rather than something to silently paper over with a side effect that had its own correctness problems. The Gateway path (`memory/gateway.py`) is unaffected — `auto_store_turn()` still runs unconditionally after every turn for direct API integrations.

---

## v2.1 (Hotfix)

**Theme:** bug discovered during first real-machine run of `main.py` after v2.

### Fixed
- **`memory/archive.py` — `TypeError` in forgetting cycle on startup (`offset-naive and offset-aware datetimes`):** Prior to v2.1, entries written to SQLite by v1 did not include timezone information in the `timestamp` / `last_accessed` columns (SQLite stores them as plain ISO strings, and early code called `datetime.now()` without `timezone.utc`). At startup in v2, the scheduler's `run_startup_catchup()` called the forgetting cycle, which compared these offset-naive datetimes against `datetime.now(timezone.utc)` (offset-aware). Python's `datetime` raises a `TypeError` on this subtraction. Fixed in `_row_to_entry()` in `memory/archive.py`: after `datetime.fromisoformat()`, if `tzinfo is None`, attach `timezone.utc` before returning.

---

## v2

**Theme:** secondary context (Warm Layer) + extraction refactor (pluggable rule engine).

### Added
- `memory/warm_layer.py` — new `WarmLayerManager` class. Manages the `warm_layer` SQLite table with upsert semantics and two-pass retrieval (keyword match on `context_hint` -> cosine similarity fallback).
- `memory/models.py`: `WarmAttribute` dataclass added. `LayeredContext` now includes `warm_attributes` and `warm_retrieval_triggered`.
- `mcp_server.py`: added `update_warm_attribute` tool for explicit Warm Layer management.
- `config.py`: added `WARM_LAYER_TOP_K` (5), `WARM_LAYER_SCORE_THRESHOLD` (0.45), `WARM_LAYER_SIM_WEIGHT` (0.7), and `WARM_LAYER_IMP_WEIGHT` (0.3).
- `memory/auto_extract.py`: added `extract_warm()` to support auto-detecting warm candidates. Added `WarmAttributeRule` (detects location, occupation, birthdate, education, recurring_habit, language_preference in EN and AR).
- `docs/decisions/ADR-008-warm-layer-dual-routing-and-upsert.md` — documents the architectural decision to use upsert for current state but dual-route to the Archive for historical record.

### Changed
- `memory/auto_extract.py` — refactored from a flat pattern-list into a pluggable `Rule` engine architecture (implementing `ADR-004`). Includes `FillerSkipRule`, `IdentitySignalRule`, `EmotionalSignalRule`, and `WarmAttributeRule`.
- `memory/gateway.py`: `build_context()` now queries the Warm Layer and includes it in `LayeredContext`. `auto_store_turn()` now dual-routes warm candidates to both the Warm Layer (upsert) and the Archive (append).
- `mcp_server.py`: `get_context` now includes `warm_attributes` in its JSON response. It also opportunistically auto-upserts warm attributes on the user message.

### Fixed
- **Bug found during Rule Engine refactoring:** The `FillerSkipRule` in v1.1 exempted text based on word/character count, but could still incorrectly skip extremely short, high-signal phrases like "اسمي ديب" (My name is Deeb - 2 words). Fixed by explicitly exempting any text that matches a high-signal identity or emotional pattern from the length check.

### Documented (not yet implemented)
- `docs/decisions/ADR-007-archive-not-delete-superseded-facts.md` added (previously discussed in `PROJECT_STATUS.md` §3.6 and §6, formalized now as a planned decision for v7 Memory Consistency).

---

## v1.1

**Theme:** reliability fixes — three structural gaps found in v1 during evaluation, fixed before any new feature work.

### Added
- `memory/gateway.py` — new `MemoryGateway` class. Deterministic context injection (`build_context()`) and rule-based automatic storage (`auto_store_turn()`) for direct API integrations, independent of whether the calling model chooses to invoke an MCP tool. See `decisions/ADR-002-memory-gateway-for-reliability.md`.
- `memory/auto_extract.py` — new rule-based (non-LLM) extraction engine. Decides storage-worthiness, importance score, emotional weight, and tags from bilingual (Arabic/English) pattern matching. See `decisions/ADR-003-rule-based-auto-extraction.md`.
- `system_state` table in `archive.db` — key/value bookkeeping table, currently used to track `last_forgetting_run`.
- `scheduler.run_startup_catchup()` — runs the forgetting cycle immediately at process startup if overdue, instead of relying solely on a live in-process weekly timer. See `decisions/ADR-006-forgetting-cycle-startup-catchup.md`.
- `ArchiveDB.get_state()` / `set_state()` — read/write helpers for the new `system_state` table.

### Changed
- `config.py`: `EMBEDDING_MODEL` changed from `all-MiniLM-L6-v2` to `paraphrase-multilingual-MiniLM-L12-v2` (same 384-dim output, same interface) — fixes poor Arabic semantic search quality. See `decisions/ADR-005-multilingual-embeddings.md`.
- `config.py`: added `AUTO_STORE_*` constants (`AUTO_STORE_MIN_CHARS`, `AUTO_STORE_DEFAULT_IMPORTANCE`, `AUTO_STORE_HIGH_IMPORTANCE`, `AUTO_STORE_EMOTIONAL_WEIGHT`) consumed by `memory/auto_extract.py`.
- `mcp_server.py`: `store_memory`'s `importance`, `tags`, and `emotional_weight` parameters are now optional (previously had hardcoded defaults: `0.5`, `[]`, `0.0`). When omitted by the calling model, they now fall back to `memory/auto_extract.py`'s rule-based extraction instead of a flat default — for consistent scoring regardless of which model is calling.
- `mcp_server.py`: `get_context` now also opportunistically auto-stores the incoming user message as a side effect (via `auto_extract`), as a partial mitigation for MCP-native clients that may never call `store_memory` explicitly. Does not capture the assistant's own reply — only the Gateway path guarantees that.
- `mcp_server.py`: the `FastMCP` `instructions` text strengthened to explicitly tell the model to call `get_context` on every message, even ones that seem unrelated to memory.
- `scheduler.py`: rewritten. `start_scheduler()` (the live weekly timer) is now a secondary mechanism; `run_startup_catchup()` is the primary guarantee that the forgetting cycle actually runs.
- `main.py`: now calls `run_startup_catchup()` before starting the MCP server.
- `memory/archive.py`: `datetime.utcnow()` calls replaced with `datetime.now(timezone.utc)` (deprecation fix, no behavior change).
- `memory/forgetting.py`: same `datetime.utcnow()` → `datetime.now(timezone.utc)` fix.

### Fixed
- **Bug found during testing, fixed before release:** `memory/auto_extract.py`'s initial `should_skip()` used a flat character-length cutoff (`< 20` chars), which incorrectly skipped short-but-meaningful Arabic sentences (e.g. "تزوجت الشهر الماضي" — 19 characters, 3 words — a maximum-significance emotional statement). Fixed by switching to a word-count-based check (skip only if under 3 words *and* under the character threshold). See `experiments.md` E6 for the full test trace.

### Documented (not yet implemented)
- `PROJECT_STATUS.md` §4.6 / `decisions/ADR-004-pluggable-rule-engine.md`: planned refactor of `memory/auto_extract.py` from a flat pattern-list file into a pluggable Rule-object architecture. Recorded as a prerequisite for v2, not built yet.
- `PROJECT_STATUS.md` §5 / `docs/roadmap.md`: vector index (FAISS/HNSW) migration plan and trigger conditions, to replace the current linear-scan retrieval once the archive grows large enough. Recorded now, not built, and deliberately not an ADR yet — no choice has been made between FAISS and HNSW; that choice gets its own ADR once it's actually made.
- `PROJECT_STATUS.md` §6, v7: new "Memory Consistency" version specified (contradiction detection, fact supersession, duplicate merging) — addresses the gap in §3.6. Specified, not built.
- Per-version success **Metrics** (retrieval latency, retrieval accuracy, useful-memory ratio, database size) added to every future version's specification in `PROJECT_STATUS.md` §6, so future versions are evaluated against measurements, not just "was it built."
- `PROJECT_STATUS.md` §1: added an explicit "Scope & Non-Goals" statement — the system does not attempt to simulate the human brain; it is a practical memory layer for AI assistants.

### Known limitations (unchanged from v1, explicitly documented rather than fixed)
- MCP is a client-driven protocol; the MCP server cannot force an MCP-native client (e.g. Claude Desktop/Code) to call `get_context`/`store_memory` on every turn. The Gateway (new in v1.1) fully solves this only for direct API integrations that call it directly — see `decisions/ADR-002-memory-gateway-for-reliability.md`.
- Keyword-only retrieval triggers (v1, unchanged in v1.1) still under-fire on subtle references and can over-fire on generic tag words. Intentionally deferred to v4 per the original spec.

---

## v1

**Theme:** initial build — the v1 scope defined in the original project specification.

### Added
- `config.py` — central configuration: paths, embedding model choice, retrieval/forgetting thresholds, scoring weights.
- `memory/models.py` — `MemoryEntry`, `FastLayer`, `LayeredContext` dataclasses.
- `memory/fast_layer.py` — `FastLayerManager`: read/write `data/fast_layer.json`, with safe fallback to defaults on missing/malformed files.
- `memory/archive.py` — `ArchiveDB`: SQLite-backed archive with embedding BLOB storage, access tracking, tag indexing, compression, and deletion.
- `memory/retrieval.py` — `RetrievalEngine`: two-step retrieval decision (Step 1 keyword/tag triggers implemented; Step 2 LLM judgment explicitly deferred to a later version), plus cosine-similarity-based semantic search combined with importance scoring.
- `memory/forgetting.py` — importance scoring formula (`frequency × 0.4 + recency × 0.3 + emotional_weight × 0.3`) and pruning rules (delete below 0.2 after 30 days, compress below 0.4 after 90 days, protect `emotional_weight = 1.0` entries).
- `mcp_server.py` — MCP server (`FastMCP`, stdio transport) exposing `get_context` and `store_memory` tools, per the v1 scope.
- `scheduler.py` (original version) — `APScheduler`-based live in-process weekly timer for the forgetting cycle.
- `main.py` — entry point: starts the scheduler, then runs the MCP server.
- `data/fast_layer.json` — blank template for the Fast Layer.
- `requirements.txt` — `mcp`, `sentence-transformers`, `numpy`, `APScheduler`.
- `README.md` — setup instructions and integration guidance for Claude, OpenAI, Gemini, and generic MCP/API patterns.
- `PROJECT_STATUS.md` — initial version: what was built, and detailed specs for the then-planned v2–v6.

### Explicitly out of scope (by design, per the original spec)
- Warm Layer, Task Layer, topic-switching buffer, LLM-based retrieval judgment (Step 2), AI internal thought memory, personality/beliefs layer.

### Verified before release
- Fast Layer save/load round-trip.
- Archive store/read/access-tracking/tag-indexing.
- Keyword-trigger retrieval logic (English and Arabic time references, tag matching, correct non-triggering on unrelated questions).
- Forgetting-cycle scoring and pruning (deletion of old low-importance entries, protection of `emotional_weight = 1.0` entries).
- All modules compile cleanly (`py_compile`).

### Known limitations at release (carried into v1.1's evaluation)
- Embedding model (`all-MiniLM-L6-v2`) is English-oriented; Arabic semantic search quality untested and likely weak.
- Live embedding-model download and live MCP client integration not verified (sandbox network restrictions).
- Forgetting cycle's live in-process timer assumes a long-running process — untested against realistic short-lived MCP session behavior.
- Storage and context injection depend entirely on the calling model choosing to invoke MCP tools — no deterministic guarantee.
