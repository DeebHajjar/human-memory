# Human Memory System — Project Status & Roadmap

*Last updated: July 2026 — v2.3*

---

## 1. Overview

The Human Memory System is a local, privacy-first memory layer for AI assistants, designed to mimic how human memory actually works rather than how a database works. It surfaces relevant context at the right moment, forgets trivial details over time, and permanently retains emotionally significant information — all while staying model-agnostic and fully offline after initial setup.

This document describes:
- What was built and verified in **v1** (Section 2)
- A full evaluation of v1, including where it fails in real-world use (Section 3)
- What was fixed in **v1.1** to address those failures, and how (Section 4)
- What was added in **v2** (Warm Layer) (Section 5)
- What was hotfixed in **v2.1** (Section 5.5)
- What was fixed in **v2.2** (Section 5.6)
- What was fixed in **v2.3** (Section 5.7)
- What each future version (**v3 → v6**) must add, in detail (Section 6)

---

## 2. What Was Built — v1

### 2.1 Scope delivered

v1 implements the minimum end-to-end loop specified in the original design:

```
receive message → check if retrieval needed → retrieve relevant context
→ send to LLM → store response → prune archive on schedule
```

Four components were built, tested, and packaged:

| Component | File(s) | Status |
|---|---|---|
| Fast Layer | `memory/fast_layer.py`, `memory/models.py` | ✅ Built & tested |
| Archive (SQLite + embeddings) | `memory/archive.py` | ✅ Built & tested |
| Retrieval logic (keyword-only) | `memory/retrieval.py` | ✅ Built & tested |
| Forgetting system | `memory/forgetting.py`, `scheduler.py` | ✅ Built & tested |
| MCP Server | `mcp_server.py`, `main.py` | ✅ Built (pending live run) |

### 2.2 Fast Layer

A JSON file (`data/fast_layer.json`) holding the user's core identity:
- Name, age, language
- Personality traits
- Key preferences and values
- Currently active task ID (placeholder — Task Layer not yet built)

This layer is always loaded on every request, never retrieved via search. It is intentionally small, stable, and human-editable — the user can open the file and edit it directly.

**Verified behavior:** save/load round-trip preserves all fields correctly; missing or malformed files fall back to safe defaults without crashing.

### 2.3 Archive Layer

A local SQLite database (`data/archive.db`) storing every memory entry with:
- `id`, `content`, `summary`, `embedding` (BLOB), `importance_score`
- `frequency_score`, `recency_score`, `emotional_weight`
- `timestamp`, `last_accessed`, `access_count`, `tags`, `source`

Embeddings are generated locally using `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dimensions) — no external API calls, no data leaves the machine.

**Verified behavior:** entries store and retrieve correctly; access tracking (`mark_accessed`) increments properly; tag indexing works for the retrieval trigger; compression and deletion operations behave as expected.

### 2.4 Retrieval Logic (v1 = keyword-only)

Per the v1 spec, only **Step 1 (keyword triggers)** was implemented — the LLM-judgment fallback (Step 2) is deliberately deferred to a later version.

Two trigger types were built:
1. **Time/context reference phrases** — in both English (*"last time," "remember when," "we discussed"*) and Arabic (*"من قبل," "تذكر," "قلت"*), reflecting the user's bilingual context.
2. **Known tag matching** — if any word in the incoming message matches a tag already present in the archive, retrieval fires.

When retrieval fires, the system performs a semantic search using cosine similarity between the query embedding and all stored embeddings, combined with each entry's importance score:

```
combined_score = 0.7 × similarity + 0.3 × importance_score
```

Results above a configurable threshold (default `0.30`) are returned, ranked, and capped at `top_k` (default `5`).

**Verified behavior:** English and Arabic time-reference triggers fire correctly; tag-based triggers fire correctly; plain factual questions with no memory relevance correctly do **not** trigger retrieval.

### 2.5 Forgetting System

A scoring formula runs on a weekly schedule (via `APScheduler`, embedded in the same process — no separate daemon):

```
importance_score = frequency_score × 0.4 + recency_score × 0.3 + emotional_weight × 0.3
```

- `frequency_score` — access count normalized against the most-accessed entry
- `recency_score` — exponential decay from last access, 60-day half-life
- `emotional_weight` — manually or automatically assigned significance (0–1)

**Pruning rules applied every cycle:**

| Condition | Action |
|---|---|
| `importance_score < 0.2` AND age ≥ 30 days | Delete permanently |
| `importance_score < 0.4` AND age ≥ 90 days | Compress to a short summary, re-embed |
| `emotional_weight = 1.0` | Never deleted, only eligible for compression after very long periods |

**Verified behavior:** an artificially aged, low-importance entry was correctly deleted after one cycle; an entry with maximum emotional weight was correctly protected from deletion regardless of age or access count.

### 2.6 MCP Server

Built using the `mcp` Python library (`FastMCP`), running on **stdio transport** — meaning it never opens a network port and integrates with any MCP-compatible client.

Two tools are exposed, exactly as scoped for v1:

**`get_context(message: str)`**
Returns the fast layer (always) plus retrieved archive entries (only if triggered). Called before the LLM generates a response.

**`store_memory(content, source, importance, tags, emotional_weight)`**
Stores a new entry in the archive with a locally generated embedding. Called after the LLM generates a response.

**Status:** code is complete and syntactically verified, but has not yet been run end-to-end with a live MCP client (e.g. Claude Desktop) because the current environment cannot reach Hugging Face to download the embedding model. This is the first thing to verify locally.

### 2.7 What was explicitly excluded from v1 (by design)

Per the original specification, the following were intentionally **not** built yet:
- Warm Layer
- Task Layer
- Topic-switching buffer
- LLM-based retrieval judgment (Step 2 of the decision logic)
- AI internal thought memory
- Personality/beliefs layer

---

## 3. Full Evaluation of v1 — Where It Fails in Real-World Use

v1's individual components were unit-tested and behave correctly in isolation. This section evaluates the system as a whole, as it would actually be used, and identifies structural failure points — not bugs, but design gaps that would surface with real usage over time.

### 3.1 The core problem: storage and context injection depend on the model's own choice

This is the most serious issue in v1, and it's structural rather than a simple bug.

**Why it happens:** MCP is a client-driven protocol. The server (`mcp_server.py`) is entirely passive — it cannot "push" itself into a conversation. It can only wait for the calling model to decide to invoke `get_context` or `store_memory`. The `instructions` text in the `FastMCP` constructor is a strong hint to the model, but it is not a technical guarantee.

**Realistic failure scenarios:**
- In a long conversation, the system instructions get buried under growing context, and the model simply stops calling `get_context` — meaning even the Fast Layer, which is meant to be *always* present, silently disappears.
- A weaker model (or even a strong model on an off turn) judges a message as "not needing memory" and skips `get_context` entirely.
- `store_memory` gets called inconsistently — sometimes after an important exchange, sometimes not — leaving the archive incomplete and unreliable over time.

This is exactly the problem raised directly by the user, and it required an architectural fix, not a configuration tweak — see Section 4.1.

### 3.2 The embedding model does not support Arabic well

`all-MiniLM-L6-v2` is trained primarily on English. Since the user works bilingually in Arabic and English, semantic search on Arabic messages would frequently fail to find relevant matches even when the meaning was clearly related — because the underlying vector space doesn't represent Arabic semantics well.

### 3.3 The weekly forgetting cycle likely never runs

`scheduler.py` (v1) relied on `APScheduler`'s in-process interval timer, which only fires if the process stays alive continuously for a full week. MCP server processes are typically **not** long-running daemons — clients like Claude Desktop/Code start the process per session and stop it afterward. In realistic usage, the process might live for minutes, not seven days, so the forgetting cycle may simply never trigger.

### 3.4 No automatic importance/emotional-weight extraction

In v1, `importance` and `emotional_weight` were entirely up to whatever value the calling model passed into `store_memory`. In practice this leads to inconsistency: one model might default everything to `0.5` out of laziness; another might mark everything `emotional_weight = 1.0`, which would defeat the forgetting system entirely by making nothing eligible for deletion. Since these fields directly drive Section 2.5's pruning rules, inconsistent input here undermines the entire forgetting system regardless of how well-tuned the formula itself is.

### 3.5 Keyword-only retrieval triggers will both under-fire and over-fire

This is an accepted, *documented* limitation of v1 (the spec explicitly deferred LLM-based judgment to v4), but it's worth stating clearly:
- **Under-fires:** a message like "how did the tracker project go?" (no exact keyword match, no known tag match) would not trigger retrieval even though it's clearly asking about stored context.
- **Over-fires:** if a generic word (e.g. "life") became a stored tag, any unrelated sentence containing that word would trigger an unnecessary archive search.

> **Resolved in v2.4** — not by v4's planned LLM judgment, but by removing the trigger gate entirely: measured bilingual testing (`experiments.md` E18) showed the under-fire case was the dominant real-world failure (a cosine-0.547 match returned nothing), while the query embedding was already being computed every call anyway. Semantic search now always runs; keyword signals became per-entry similarity boosts. See §5.8 and `ADR-011`.

### 3.6 No conflict or duplicate detection

If the user's situation changes (e.g., a stored fact becomes outdated), v1 has no mechanism to detect that a new statement contradicts or supersedes an old one. Both versions could be retrieved together in the future, potentially confusing the responding model with stale, conflicting information.

---

## 4. What Was Fixed — v1.1

v1.1 addresses the three issues identified as structural (not deferrable to a later layer): the model-dependent storage problem, the Arabic embedding gap, and the broken forgetting schedule. Sections 3.5 and 3.6 remain open — 3.5 is intentionally deferred to v4 per the original spec, and 3.6 is deferred to a later version (see Section 8).

### 4.1 Fix: deterministic Gateway + rule-based auto-extraction

**New file: `memory/gateway.py`**

A `MemoryGateway` class was added that removes the model's ability to "skip" memory operations, for any integration that routes through it:

- `build_context()` — called unconditionally before every model call. Always returns the Fast Layer, always runs the retrieval-trigger check. No tool-call decision involved.
- `auto_store_turn()` — called unconditionally after every model call. Runs rule-based extraction on both the user's message and the assistant's reply and stores whatever clears the bar, without waiting for the model to decide to call `store_memory`.
- `process_turn()` — a convenience wrapper that runs the full deterministic loop (`build_context` → call the model → `auto_store_turn`) in one call.

**New file: `memory/auto_extract.py`**

A **rule-based, non-LLM** extraction engine that decides, deterministically:
- Whether an exchange is trivial enough to skip (e.g. "ok", "thanks", "تمام") — using a word-count check rather than a flat character cutoff, since Arabic can express a complete statement in fewer characters than English (this was caught and fixed during testing — see 4.4).
- A default importance score, raised automatically when the text matches identity/preference/decision phrasing (bilingual pattern list: "my name is" / "اسمي", "I prefer" / "أفضل", "I decided" / "قررت", etc.).
- An emotional weight of `1.0` automatically, when the text matches strong life-event phrasing (bilingual: "I got married" / "تزوجت", "I lost my job" / "فقدت وظيفتي", etc.) — directly solving the "everything becomes emotional_weight=1.0" inconsistency described in 3.4.
- Candidate tags, extracted heuristically from capitalized words/proper nouns.

This is intentionally rule-based rather than LLM-based for this specific job: an LLM-based *retrieval* judgment (deferred to v4) can afford to be wrong occasionally, since a missed retrieval just means one less relevant memory surfaced. An LLM-based *storage* judgment being wrong could mean silently losing information forever — a much higher-stakes mistake — so a predictable, auditable rule set was chosen for now.

**Important documented limitation:** this fix fully solves the problem for direct API integrations (a custom wrapper around the OpenAI/Gemini/Claude API using `MemoryGateway` directly — see the updated README). It does **not** fully solve it for MCP-native clients like Claude Desktop/Code, because MCP's protocol gives the *client*, not the server, control over when tools are called — this is a protocol-level constraint, not a gap in this codebase.

**Partial mitigation added for MCP-native clients (`mcp_server.py`):**
- `get_context` — the tool most reliably called every turn by an MCP client trying to be helpful — now also opportunistically auto-stores the incoming user message as a side effect, using the same rule-based engine. This does not capture the assistant's own reply (only the Gateway path guarantees that), but it meaningfully reduces data loss on the user-message side even without `store_memory` ever being called. **(Removed in v2.2 — see §5.6. This mitigation caused test/exploratory queries to be stored verbatim as if they were memories, polluting retrieval; `get_context` is now read-only.)**
- `store_memory`'s `importance`, `tags`, and `emotional_weight` parameters are now optional (previously had hardcoded defaults). When the calling model omits them, the server falls back to the same rule-based extraction used by the Gateway, instead of a flat `0.5` default — so behavior is consistent regardless of whether a model bothers to compute these values itself.
- The MCP server's `instructions` text was strengthened to explicitly tell the model to call `get_context` on *every* message, even ones that seem unrelated to memory, specifically because it now also keeps the Fast Layer current and opportunistically captures the user's message. **(The opportunistic-capture rationale no longer applies as of v2.2 — see §5.6 — though calling `get_context` every turn is still the correct guidance for keeping the Fast Layer/Warm Layer/retrieved context current.)**

### 4.2 Fix: multilingual embeddings

**Changed file: `config.py`**

```python
# Before (v1):  EMBEDDING_MODEL = "all-MiniLM-L6-v2"
# After (v1.1): EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
```

Same embedding dimension (384) and same `.encode()` interface, so no other code needed to change. This model supports Arabic and English (and dozens of other languages) with comparable quality, directly fixing the gap identified in 3.2. Trade-off: the model download is larger (~470 MB vs ~90 MB) — a one-time cost, still fully offline afterward.

### 4.3 Fix: forgetting-cycle startup catch-up

**New table:** `system_state` (key/value) added to the existing `archive.db` — tracks `last_forgetting_run` as an ISO timestamp.

**New functions in `scheduler.py`:**
- `_is_overdue(db)` — checks whether the time since `last_forgetting_run` exceeds the configured cycle length (default 1 week). Treats "never run" as overdue.
- `run_startup_catchup()` — called once, at process startup, *before* the MCP server starts serving requests (wired into `main.py`). If the cycle is overdue, it runs immediately instead of waiting.
- `start_scheduler()` — kept as a **secondary** mechanism: the live in-process weekly timer still runs for the less common case of a long-lived server process, but it's no longer the only thing that can trigger the cycle.

This directly fixes 3.3: even a server process that only lives for a few minutes per session will now reliably run the forgetting cycle on whichever session happens to start after it becomes due, rather than requiring continuous uptime.

### 4.4 Bug caught during v1.1 testing (and fixed before shipping)

While testing the new auto-extraction rules, a real edge case surfaced: the initial implementation used a flat character-length cutoff (`len(text) < 20`) to decide whether a message was too trivial to store. Short Arabic sentences that are nonetheless complete and meaningful — e.g. `"تزوجت الشهر الماضي"` ("I got married last month," 19 characters, 3 words) — were being incorrectly skipped, silently dropping a maximum-significance emotional memory. This was caught by the test suite (Section 4.5) before release and fixed by switching to a word-count-based check (skip only if under 3 words *and* under the character threshold), which treats compact non-Latin-script sentences fairly.

### 4.5 Testing performed for v1.1

All fixes were verified with executable tests before being considered complete:
- Auto-extraction correctly skips filler ("ok", "hi", "تمام") and correctly stores substantive messages in both English and Arabic.
- Emotional-signal phrases in both languages correctly produce `emotional_weight = 1.0`.
- Identity/preference phrases correctly raise importance above the default.
- Generic-but-substantive messages correctly receive the default importance rather than being skipped or over-weighted.
- `_is_overdue()` correctly reports `True` for a never-run system, `False` immediately after a run, and `True` again once the configured cycle length has elapsed.
- `MemoryGateway.build_context()` returns the Fast Layer regardless of message content — confirming the "always present" guarantee actually holds in code, not just in documentation.
- `MemoryGateway.auto_store_turn()` stores both the user's message and the assistant's reply automatically, with zero tool-call decisions involved — confirming the core problem raised by the user is now solved for Gateway-based integrations.

---

## 5. What Was Built — v2

v2 implements the **Warm Layer**, along with its required prerequisite (the Pluggable Rule Engine, `ADR-004`).

### 5.1 Pluggable Rule Engine (v2 Prerequisite)

**Changed file: `memory/auto_extract.py`**
The extraction logic was refactored into a `Rule` object architecture. It now iterates over `FillerSkipRule`, `IdentitySignalRule`, `EmotionalSignalRule`, and the new `WarmAttributeRule`. This replaced the flat pattern lists and made the engine cleanly extensible.

**High-signal exemption fix:** During refactoring, we addressed a remaining edge case in `FillerSkipRule` where even a 2-word phrase might be skipped, by exempting any text that matches a high-signal identity or emotional pattern (e.g. "اسمي ديب").

### 5.2 The Warm Layer

**New file: `memory/warm_layer.py`**
The Warm Layer holds secondary, context-specific biographical attributes (e.g. location, occupation, recurring habits). It is stored in a `warm_layer` table inside `archive.db`.

**Upsert semantics and Dual Routing (`ADR-008`):**
Unlike the Archive, the Warm Layer uses **upsert semantics**. Storing a new value for a key (like "location") completely replaces the old one. This prevents contradictory current facts from being surfaced simultaneously.
To ensure the historical record is not lost, `MemoryGateway` implements **dual routing**: when `extract_warm()` matches a biographical fact, it is upserted into the Warm Layer *and* also appended to the Archive.

**Two-Pass Retrieval:**
Retrieval (`WarmLayerManager.retrieve_relevant()`) is faster than a full Archive search:
1. Keyword match on the `context_hint` field (fast path).
2. Cosine similarity on embeddings for remaining candidates.
The score threshold is `0.45` (higher than the Archive's `0.30`) to avoid false positives on highly specific biographical facts.

### 5.3 MCP & Gateway Integration

- `memory/gateway.py`: `build_context()` now queries the Warm Layer and includes `warm_attributes` in the context block. `auto_store_turn()` routes candidates appropriately using the new dual routing logic.
- `mcp_server.py`: `get_context` now opportunistically auto-upserts warm attributes on top of archiving them. A new explicit `update_warm_attribute` tool was added for MCP clients to explicitly manage these stable personal facts. **(The opportunistic auto-upsert was removed in v2.2 — see §5.6.)**

### 5.4 Testing performed for v2

- `FillerSkipRule` exemption verified: "اسمي ديب" is retained despite being 2 words.
- All 6 `WarmAttributeRule` categories (location, occupation, birthdate, education, recurring_habit, language_preference) trigger correctly in EN and AR.
- `WarmLayerManager.upsert()` correctly replaces existing keys instead of duplicating.
- `WarmLayerManager.retrieve_relevant()` correctly handles both keyword and semantic retrieval passes.
- Verified that Warm Layer operations do not corrupt the Fast Layer JSON.

---

### 5.5 Hotfix \u2014 v2.1

**Bug:** `TypeError: can't subtract offset-naive and offset-aware datetimes` in the forgetting cycle at startup.

**Root cause:** Entries written to SQLite by v1 / v1.1 stored `timestamp` and `last_accessed` as plain ISO 8601 strings without a UTC offset, because early code called `datetime.now()` (offset-naive) instead of `datetime.now(timezone.utc)`. When v2's startup catch-up forgetting cycle ran on a machine with a real database for the first time, `_recency_score()` in `memory/forgetting.py` tried to compute `datetime.now(timezone.utc) - reference` where `reference` was offset-naive — Python raises a `TypeError` on this subtraction.

**Fix:** `_row_to_entry()` in `memory/archive.py`. After parsing the stored ISO string with `datetime.fromisoformat()`, if the result has no `tzinfo`, it is immediately tagged as `timezone.utc` via `replace(tzinfo=timezone.utc)` before returning. This is safe because all timestamps in this system are stored in UTC, just without the suffix for pre-v2 entries.

**Verified:** `main.py` was re-run after the fix. The startup catch-up completed without error, updating 6 entries (`updated=6, deleted=0, compressed=0`), and the scheduler started normally.

---

### 5.6 Fix \u2014 v2.2: Remove Opportunistic Auto-Store from `get_context`

**Problem found:** the v1.1 mitigation described in \u00a74.1 (`get_context` opportunistically auto-storing the incoming message via `auto_extract`) caused a real data-quality regression once the system saw real usage. `get_context` is also the natural tool call for test/exploratory questions about existing memory (e.g. "Where do I live?") \u2014 not just new facts to remember. Because the auto-store side effect had no way to distinguish a question from a statement, these queries were being written into the Archive and/or upserted into the Warm Layer as if they were memories. Later semantic retrieval then matched against this stored query text instead of the actual facts, actively degrading retrieval quality \u2014 the opposite of the mitigation's intent.

**Root cause:** `get_context` had two responsibilities bundled into one call \u2014 retrieval (its intended purpose) and opportunistic storage (a side effect) \u2014 violating separation of concerns and giving the caller no way to get one without the other.

**Fix:** removed the auto-store side effect from `get_context` entirely. `get_context` (`mcp_server.py`) is now a pure read/retrieval function \u2014 it loads the Fast Layer, runs Warm Layer retrieval, runs Archive retrieval, and returns the assembled context. It no longer calls `archive.store()`, `warm_layer_mgr.upsert()`, or any `auto_extract.extract*()` function. All writes to memory now happen only through the pre-existing explicit tools: `store_memory` and `update_warm_attribute`. The `FastMCP` `instructions` text and the `get_context` tool docstring were updated to state plainly that `get_context` is read-only and that the model must call `store_memory`/`update_warm_attribute` explicitly. See [`ADR-009`](decisions/ADR-009-remove-opportunistic-auto-store-from-get-context.md) for the full decision record, including alternatives considered (e.g. trying to detect questions vs. statements before auto-storing \u2014 rejected as reintroducing the same kind of unreliable judgment call ADR-003 deliberately avoids for storage decisions).

**Consequence \u2014 a known limitation reopens:** this fix does **not** re-solve the reliability problem ADR-001/ADR-002 describe for MCP-native clients. Nothing is stored via the MCP path unless the calling model explicitly calls `store_memory`/`update_warm_attribute` \u2014 the same gap that existed before the v1.1 mitigation was added, now understood to be the correct trade-off rather than something to silently paper over. The Gateway path (`memory/gateway.py`) is completely unaffected: `auto_store_turn()` still runs unconditionally after every turn for direct API integrations, per ADR-002.

**Not addressed by this fix:** any Archive/Warm Layer entries written by the old opportunistic path before this fix (raw stored queries) are not retroactively cleaned up \u2014 they remain ordinary entries subject to the existing forgetting cycle.

---

### 5.7 Fix \u2014 v2.3: Warm Up the Embedding Model at Startup (First-Call Timeout)

**Problem found:** on a freshly started server, the *first* tool call of a session (usually `get_context`) never returned \u2014 the client waited and eventually timed out with no result. Retrying the identical call immediately after worked and returned correctly, and it only ever happened once per server process.

**Root cause (confirmed by measurement, not assumed):** the embedding model was loaded **lazily on first use** inside `_get_embedder()` in `mcp_server.py`. Every tool handler calls it, so the first call after startup absorbed the entire one-time load cost. Measured on a real machine *with the model already cached*: `import sentence_transformers` (~5.0s) + `SentenceTransformer(model)` load (~6.6s) + first `.encode()` (~0.2s) \u2248 **11.8s** \u2014 versus ~0.13s for all the eager init (SQLite schema, layer managers) and ~16ms for a warmed `.encode()`. That 11.8s exceeds the MCP client's per-tool-call timeout, so the first call appeared to hang; the load finished in the background and populated the process-global `_embedder`, so the immediate retry was instant; the global persists for the process, so it happened exactly once. On load, `sentence-transformers` also makes an HF Hub network round-trip, which stalls further on a slow/unreachable network (and becomes a multi-minute *download* if the model isn't cached yet). Neither entry point avoided this: `main.py`'s startup catch-up uses a *separate* embedder in `scheduler.py` and only when forgetting is overdue, and `python mcp_server.py` skips catch-up entirely.

**Fix:** load the embedding model **eagerly at server startup, before any request is processed**, via FastMCP's documented `lifespan` hook (`mcp` 1.28.1). The lifespan calls the existing `_get_embedder()` plus one dummy `encode("warm up")`; because both entry points call `mcp.run()`, the warm-up covers both. `_get_embedder()` is kept as an idempotent fallback so behaviour is unchanged if warm-up is ever skipped. Scope was limited to startup timing \u2014 `get_context`'s retrieval logic, `store_memory`, `update_warm_attribute`, and `memory/gateway.py` were not touched. Two concise stderr log lines around the warm-up were kept (safe for the stdio protocol; make a slow/cold start visible). See [`ADR-010`](decisions/ADR-010-eager-embedding-warmup-at-startup.md) for the decision record and alternatives considered, and `experiments.md` E17 for the timings.

**Verified:** end-to-end over stdio, the ~12s load now happens during `initialize` (stderr shows `Warming up embedding model\u2026` \u2192 `Embedding model ready in 11.6s \u2014 server accepting requests`), and the first `get_context` call is **0.088s** vs **0.036s** for the second \u2014 effectively equal, i.e. the model is fully warm before the first request. `initialize` itself now takes ~12s, but clients wait on `initialize` and its timeout is far more tolerant than the per-tool-call timeout.

### 5.8 Fix \u2014 v2.4: Always-On Semantic Retrieval with a Raw-Similarity Floor

**Problem found:** manual bilingual testing (~11 mixed English/Levantine-Arabic memories) showed retrieval failing on direct recall questions in *both* languages \u2014 "What's my cat's name?" / "\u0634\u0648 \u0627\u0633\u0645 \u0642\u0637\u062a\u064a\u061f" returned nothing despite an explicitly stored answer \u2014 while queries that did fire returned mostly irrelevant results (5 returned, 1 relevant), and the Warm Layer surfaced unrelated attributes (a running-training question returned `favorite_programming_language`). Storing the same content with Arabic tags mysteriously "fixed" Arabic retrieval, which initially pointed at embedding quality.

**Root cause (confirmed by measurement, not assumed \u2014 `experiments.md` E18):** three compounding defects, all architectural rather than model-quality. (1) `should_retrieve()` gated semantic search behind exact keyword triggers \u2014 the cat memory matched at cosine **0.547** in both languages but the search never ran because no stored tag word appeared in the query; the "Arabic tags fix" was purely this gate opening (tags are never embedded). The gate also protected nothing: the query embedding was already computed unconditionally for the Warm Layer. (2) The pass threshold mixed importance into the decision (`0.7\u00d7sim + 0.3\u00d7imp \u2265 0.30`), admitting entries at near-noise similarity ~0.26 \u2014 same-person memories cluster at 0.25\u20130.40 in this model. (3) The Warm Layer's keyword pass matched **stopwords** in `context_hint` sentences ("the", "for", "should") and auto-included hits with a fabricated 0.92 score; its semantic pass had the same importance-in-threshold flaw (false positives measured at sim 0.42\u20130.43).

**Fix (`ADR-011`):** semantic search runs on **every** message for both Archive and Warm Layer; inclusion is decided by **raw cosine similarity only** (floors 0.35 / 0.55, calibrated in E18); importance ranks passers but never admits an entry; keyword signals became similarity *boosts* (+0.15 when an entry's own tag, or a stopword-filtered hint content word, appears in the message) instead of gates/auto-includes. Embedded text is unchanged, so no data migration. `retrieval_triggered` now means "relevant memories found". A committed bilingual replay benchmark (`tools/replay_retrieval.py`) is the acceptance test for any future scoring or embedding-model change. Drive-by: `warm_layer.py`'s `upsert()` naive `datetime.utcnow()` \u2192 UTC-aware (latent E16-class bug).

**Verified:** full before/after table in E18 \u2014 cat retrieval works in both languages, the Arabic food query retrieves via tag boost, precision improved (3 results vs 7 on the English food query), and all observed warm-layer false positives are excluded while the legitimate programming-language probe still retrieves its attribute. **Known residual:** dialectal-Arabic similarity collapse (a Levantine query scored **0.031** against its own Levantine memory) is an embedding-model limitation no scoring change can fix \u2014 the candidate follow-up is a stronger multilingual model (revisits `ADR-005`).

---

## 6. What Remains \u2014 Future Versions

Each version below adds exactly **one layer or one capability**, per the original design constraint of never combining multiple additions in a single version. No version should introduce breaking changes to the existing MCP tool interface (`get_context`, `store_memory` keep working as-is; new tools are additive).

---



### v3 — Task Layer

**Purpose:** replicate how humans hold one active project "in focus" while other projects remain suspended but recoverable.

**Must contain:**
- A single "active task" concept — only one task is active at a time.
- Per-task context: recent decisions, current focus, file references, open questions.
- A suspend/resume mechanism: switching tasks compresses the current task's state into storage and loads the new task's compressed state back into working context.

**Behavior to implement:**
- New storage structure (likely its own SQLite table or JSON store) keyed by `task_id`.
- The Fast Layer's existing `active_task_id` field (already present in v1's data model) becomes functional — this is the field to update whenever a task switch occurs.
- Task compression logic: when suspending a task, produce a short structured summary (decisions made, next step, open questions) rather than storing the full raw history — mirroring how a human recalls "the structure and last decision" of a project after being away, not every detail.
- Optional: integration hook for external tools like Graphify for code projects, as mentioned in the original spec — this should be an optional adapter, not a hard dependency.

**New/changed interface:**
- Implement the `set_active_task(task_id)` tool exactly as originally scoped: suspends the current task, activates the requested one, returns the new `TaskContext`.
- `get_context` should include the active task's compressed state automatically, without requiring a separate call.

**Testing checklist for v3:**
- Switching away from and back to a task correctly restores its compressed state.
- Only one task is ever "active" at a time; suspended tasks are excluded from the Fast Layer's default context.
- Task switching does not lose data — compression must be lossy only in *detail*, not in *structure* (decisions, next steps, and open questions must survive).

---

### v4 — LLM-Based Retrieval Judgment

> **Premise revised by v2.4 (`ADR-011`):** the original v4 design assumed the two-step trigger logic ("Step 1 keyword gate, Step 2 LLM fallback deciding *whether* to search"). v2.4 removed the gate — semantic search now always runs, which resolves the under-fire problem v4 was primarily meant to close (§3.5). What remains for v4 is the *precision* half of the problem: raw similarity cannot distinguish "mentions the same person/style" from "actually answers the question" (E18's residual: same-person noise passing the floor on English queries). v4's judgment call is therefore now a **relevance filter/re-ranker over already-retrieved candidates**, not a search trigger.

**Must contain:**
- A judgment call over the top-k candidates returned by the (always-on) semantic search, pruning entries that clear the similarity floor but don't bear on the message.
- The judgment call should use a small, fast model — explicitly *not* the main conversational model — to keep latency and cost low. This should be configurable (model name/endpoint) rather than hardcoded, to preserve model-agnosticism.
- Prompt should be minimal and per-candidate binary: "Does this stored memory help answer the current message? Answer yes or no."

**Behavior to implement:**
- Add the filtering stage inside `RetrievalEngine.retrieve()` (or as a wrapper), applied only when candidates exist — zero candidates means zero LLM calls.
- Add a timeout/fallback: if the judgment call fails or the small model is unavailable, return the unfiltered similarity-ranked candidates rather than blocking the main response — this preserves the "graceful degradation" constraint from the original spec.
- Log or expose which candidates were pruned by the judgment stage for debugging and future tuning.

**New/changed interface:**
- No new MCP tools required — this is an internal enhancement to `get_context`'s existing decision logic.
- `config.py` should gain settings for the judgment model endpoint/name and a timeout value.

**Testing checklist for v4:**
- E18's measured precision failures (e.g. the cat memory passing the floor on "What is Deeb's favorite food?") are pruned by the judgment stage; re-run `tools/replay_retrieval.py` as the benchmark.
- Judgment-call failures do not crash or hang `get_context` — degrade gracefully to unfiltered results.
- Latency overhead of the added judgment call is measured and stays within an acceptable bound (should be documented once implemented).

---

### v5 — AI Internal Thought Memory

**Purpose:** allow the AI to remember not just what was *said*, but what it was *thinking* — reasoning steps, uncertainties, tentative conclusions — so it can genuinely continue a line of reasoning across sessions instead of reconstructing it from conversation history alone.

**Must contain:**
- Full use of the `source` field already present in v1's `MemoryEntry` model (`user`, `assistant_speech`, `assistant_thought`) — v1 defined this field but v5 is where `assistant_thought` entries actually get populated and used meaningfully.
- A mechanism for the AI to explicitly log intermediate reasoning as its own memory type, separate from what it says to the user.

**Behavior to implement:**
- `store_memory` already accepts a `source` parameter — v5 should add clear guidance/prompting (in the MCP server's `instructions` field or tool docstring) encouraging the calling AI to use `assistant_thought` for reasoning it wants to preserve.
- Retrieval logic may need a `source` filter option, so `get_context` (or a new dedicated call) can retrieve only prior reasoning when the AI is resuming a complex, unfinished thought process.
- Consider whether internal thoughts should have different default importance/decay behavior than user-facing content — reasoning that led nowhere may deserve faster forgetting than reasoning that produced a firm conclusion.

**New/changed interface:**
- Possibly a new tool: `get_thought_history(task_id or topic)` — dedicated retrieval for prior reasoning, separate from general memory search.
- `get_context` response could optionally include a `prior_reasoning` block.

**Testing checklist for v5:**
- Assistant-authored reasoning is stored distinctly from assistant-authored speech and from user statements.
- A multi-session task can be resumed with the AI's prior reasoning intact, not just the conversation transcript.
- Internal thoughts do not leak into contexts where they'd be inappropriate (e.g. should not be casually surfaced in `get_context` results the same way user facts are).

---

### v6 — Topic-Switching Buffer

**Purpose:** maintain focus on a newly introduced topic within a single ongoing conversation, without fully losing track of the topic that was just abandoned — mirroring short-term human attention shifts.

**Must contain:**
- A rolling **Working Memory Buffer**, scoped to the current conversation only (distinct from the persistent Archive):

```json
{
  "current_topic": {
    "summary": "Designing the archive retrieval logic",
    "depth": "full context"
  },
  "previous_topics": [
    {"summary": "Discussed task layer switching", "depth": "compressed"},
    {"summary": "Defined importance scoring formula", "depth": "compressed"}
  ]
}
```

**Behavior to implement:**
- On each new message, detect whether the topic has shifted (this may reuse or extend the keyword-trigger and/or LLM-judgment mechanisms already built in v1/v4).
- When a shift is detected, compress the outgoing topic to 1–2 sentences and push it into `previous_topics`; the new topic becomes `current_topic` with full depth.
- If the user returns to a previous topic, first check the in-memory buffer (fast path); only fall back to a full Archive semantic search if the buffer has already been flushed (e.g. conversation ended or buffer size limit exceeded).

**New/changed interface:**
- This buffer is likely conversation-scoped and lightweight — it may not need to be a new MCP tool at all, but rather logic living in `get_context`'s internal state for the duration of a session. Needs a design decision: should the buffer persist across sessions (stored) or reset each new conversation (ephemeral)? The original spec implies it's conversation-scoped, so ephemeral-by-default with optional archival on flush is the most faithful interpretation.

**Testing checklist for v6:**
- Returning to a topic discussed earlier in the *same* conversation is fast (buffer hit) rather than requiring a full Archive search.
- Compression of an abandoned topic preserves enough meaning that re-expansion (or archive fallback) still makes sense to the user.
- Buffer size/limits are enforced so this doesn't grow unbounded within a very long conversation.

---

## 7. Summary Table

| Version | Adds | Depends on | New MCP tools |
|---|---|---|---|
| v1 ✅ | Fast Layer, Archive, keyword retrieval, forgetting | — | `get_context`, `store_memory` |
| v1.1 ✅ | Multilingual embeddings, forgetting-cycle startup catch-up, deterministic Gateway + rule-based auto-extraction | v1 | none new (Gateway is a library, not an MCP tool) |
| v2 ✅ | Warm Layer, two-pass retrieval, upsert semantics, pluggable rule engine | v1.1 | `update_warm_attribute` |
| v2.1 ✅ | Hotfix: timezone-naive datetime crash in forgetting cycle (`memory/archive.py`) | v2 | none |
| v2.2 ✅ | Fix: removed opportunistic auto-store from `get_context` — it's now read-only; storage requires an explicit `store_memory`/`update_warm_attribute` call | v2.1 | none |
| v2.3 ✅ | Fix: warm up the embedding model at startup (FastMCP `lifespan`) so the first tool call no longer times out on a cold server (`mcp_server.py`) | v2.2 | none |
| v2.4 ✅ | Fix: always-on semantic retrieval — removed the keyword trigger gate, raw-similarity floors (importance ranks, never admits), keyword signals became boosts, warm-layer stopword false positives eliminated (`ADR-011`) | v2.3 | none |
| v3 | Task Layer | v1 Fast Layer (`active_task_id`) | `set_active_task` |
| v4 | LLM-based retrieval judgment (re-scoped by v2.4: relevance filter over retrieved candidates, no longer a search trigger) | v2.4 retrieval logic | none (internal) |
| v5 | AI internal thought memory | v1 `MemoryEntry.source` field | `get_thought_history` (proposed) |
| v6 | Topic-switching buffer | v1/v4 retrieval logic | none (internal, conversation-scoped) |

**Open items** (see Section 3): keyword-trigger under-fire/over-fire (3.5) was resolved by v2.4's always-on retrieval; the remaining retrieval gaps are precision on same-person queries (deferred to v4's re-scoped relevance filter) and dialectal-Arabic embedding quality (needs a stronger multilingual model — revisits `ADR-005`). There is still no conflict/duplicate detection for contradicting stored facts (3.6, not yet scheduled to a specific version — candidate for a future v7).

---

## 8. Immediate Next Step

v2.1 has been confirmed working on a real machine (startup catch-up passed, embedding model downloaded and loaded successfully). Before starting v3:

1. ✅ **Done** — `main.py` confirmed running locally: the multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2`) downloads and loads correctly, and the startup catch-up forgetting cycle completes without errors.
2. Connect the MCP server to a real client (Claude Desktop or Claude Code) and confirm `get_context` is called consistently and that `warm_attributes` appear in the response correctly.
3. If a custom API wrapper is planned (OpenAI/Gemini/direct Claude API), integrate `memory/gateway.py` per the updated README and confirm `process_turn()` behaves as expected against the real model, including Warm Layer upserts.
4. Run one full real-world loop against Arabic input specifically: store "أعيش في دبي" (I live in Dubai), confirm it upserts `location`, and confirm later retrieval works.
5. Test `update_warm_attribute` MCP tool via a real client — confirm the `warm_layer` table is updated and the attribute appears in the next `get_context` response.
6. Collect a first real retrieval-latency measurement: Warm Layer retrieval should be measurably faster than Archive retrieval.
7. Only after these are confirmed working should Task Layer (v3) development begin.
