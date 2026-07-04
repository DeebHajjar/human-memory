# Changelog

*Human Memory System*

Condensed, scannable diff between versions. For the full reasoning behind any change, see `decisions/` (one ADR per decision). For the full narrative, see `PROJECT_STATUS.md`.

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
