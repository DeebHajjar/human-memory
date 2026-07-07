# Experiments Log

*Human Memory System*

This document records what was actually tested (not just designed), how, and what the results were. Each entry is dated to the development phase it belongs to. Unlike `PROJECT_STATUS.md` (which narrates the project as a whole), this file is meant to stay close to raw test output and be easy to scan for "what did we actually verify, and what's still unverified."

Format per experiment: **Question → Method → Result → Conclusion**.

---

## v1 — Initial build

### E1: Does the Fast Layer survive a save/load round-trip correctly?

**Question:** does `FastLayerManager` correctly persist and reload all fields, including edge cases like a missing or malformed file?

**Method:** save a `FastLayer` with name, language, and traits set; reload it and compare field-by-field. Separately, point the manager at a non-existent/corrupted path and confirm it falls back to safe defaults instead of crashing.

**Result:** round-trip preserved all fields exactly. Malformed/missing files correctly triggered the default-fallback path with no exception raised.

**Conclusion:** Fast Layer read/write is reliable. No further action.

---

### E2: Does the Archive correctly store, retrieve, and track access on entries?

**Question:** do `ArchiveDB.store()`, `mark_accessed()`, and the tag index (`get_all_tags()`) behave correctly?

**Method:** stored three `MemoryEntry` records with different tags/importance/emotional_weight values, using randomly generated fake embeddings (real embedding model unavailable in the test sandbox — see E7). Called `mark_accessed()` on one entry, then re-read all entries and the tag set.

**Result:** all three entries stored and read back correctly. `access_count` incremented as expected after `mark_accessed()`. `get_all_tags()` correctly returned the union of all stored tags, lower-cased.

**Conclusion:** Archive read/write path is reliable at the unit level.

---

### E3: Do the keyword-trigger retrieval rules fire correctly, in both languages?

**Question:** does `RetrievalEngine.should_retrieve()` correctly detect (a) time/context reference phrases in English and Arabic, (b) known-tag matches, and (c) correctly *not* fire on unrelated factual questions?

**Method:** ran `should_retrieve()` against a set of test messages:
- English time references ("last time we talked about my Django project", "remember when we built this?")
- Arabic time references ("من قبل كنا نتحدث عن هذا")
- A message containing a word matching a known archive tag ("workout")
- Plain factual questions with no memory relevance ("what is the capital of France?", "explain binary search")

**Result:** all English and Arabic time-reference cases correctly triggered `True`. The tag-match case correctly triggered `True`. Both plain factual questions correctly returned `False`.

**Conclusion:** Step 1 (keyword-only) retrieval logic works as specified. Known limitation (not a bug): messages that reference past context *without* using a known keyword/tag will not trigger retrieval — this is the documented gap that v4 (LLM-based judgment) is meant to close.

---

### E4: Does the forgetting cycle correctly delete low-importance old entries and protect emotionally significant ones?

**Question:** does `recompute_importance()` and `run_forgetting_cycle()` correctly implement the scoring formula and pruning rules?

**Method:**
- Constructed a `MemoryEntry` with `importance_score=0.1`, `emotional_weight=0.0`, backdated `timestamp`/`last_accessed` to 40 days ago. Ran `recompute_importance()` and confirmed the resulting score was below the deletion threshold (0.2).
- Constructed a second entry with `emotional_weight=1.0`, backdated 365 days, and confirmed its recomputed score reflected the emotional-weight contribution.
- In a fresh archive, stored one entry backdated 60 days with low importance, ran `run_forgetting_cycle()`, and checked the returned stats.

**Result:** the low-importance old entry scored below 0.2 as expected. The forgetting cycle correctly reported `{"deleted": 1}` for the backdated low-importance entry. The emotionally-weighted entry's score reflected its protected status (not deleted, per the `emotional_weight >= 1.0` short-circuit in `run_forgetting_cycle()`).

**Conclusion:** forgetting logic behaves as specified at the unit level.

---

### E5: Does the MCP server code compile and structurally hold together?

**Question:** are `mcp_server.py`, `main.py`, and the rest of the package free of syntax/import errors, and does the module structure hold together?

**Method:** `python -m py_compile` across all modules.

**Result:** clean compile, no errors.

**Conclusion:** structurally sound. **Not yet tested:** a live run against a real MCP client (Claude Desktop/Code) or a real embedding-model download — see "Known gaps" at the end of this document.

---

## v1.1 — Reliability fixes

### E6: Does the rule-based auto-extraction engine correctly classify messages?

**Question:** does `memory/auto_extract.py` correctly (a) skip trivial filler, (b) raise importance on identity/preference phrases, (c) set `emotional_weight = 1.0` on life-event phrases, and (d) do all of the above in both English and Arabic?

**Method:** ran `extract()` against a labeled set of test messages:

| Input | Expected |
|---|---|
| "ok" / "hi" | Skipped (too trivial) |
| "My name is Deeb and I live in Beirut" | Stored, importance ≥ 0.7 |
| "اسمي ديب وأعيش في بيروت" | Stored, importance ≥ 0.7 |
| "I got married last month" | Stored, `emotional_weight = 1.0` |
| "تزوجت الشهر الماضي" | Stored, `emotional_weight = 1.0` |
| "Can you explain how binary search works in more detail?" | Stored, default importance (0.4) |

**Result (first run):** all cases passed **except** the Arabic emotional-signal case — "تزوجت الشهر الماضي" was incorrectly skipped.

**Root cause found:** the initial `should_skip()` implementation used a flat character-length cutoff (`len(text) < 20`). The Arabic phrase is 19 characters — under the cutoff — despite being a complete, meaningful 3-word sentence. Arabic (and other non-Latin scripts) can express a full statement in fewer characters than English, so a flat character threshold unfairly penalized it.

**Fix applied:** changed `should_skip()` to a word-count-based check — skip only if the message is under 3 words *and* under the character threshold — rather than character count alone.

**Result (after fix):** all six cases passed, including the Arabic emotional-signal case, which now correctly returns `emotional_weight = 1.0`.

**Conclusion:** rule-based extraction works correctly in both languages after the fix. This is a real example of a bug caught by testing before release, not a hypothetical — recorded here and in `PROJECT_STATUS.md` §4.4 for traceability.

---

### E7: Does the forgetting-cycle startup catch-up correctly detect overdue status?

**Question:** does `scheduler._is_overdue()` correctly report overdue status based on the `system_state` table?

**Method:** tested three states against a fresh `ArchiveDB`:
1. No `last_forgetting_run` value set at all (never run).
2. `last_forgetting_run` set to the current timestamp (just ran).
3. `last_forgetting_run` set to 10 days ago, with the configured cycle length at 7 days (overdue).

**Result:**
1. Never-run → `_is_overdue()` returned `True`, as expected.
2. Just-ran → `_is_overdue()` returned `False`, as expected.
3. 10 days old (7-day cycle) → `_is_overdue()` returned `True`, as expected.

**Conclusion:** the catch-up logic correctly distinguishes all three states. This directly validates the fix described in `decisions/ADR-006-forgetting-cycle-startup-catchup.md` — the forgetting cycle will now reliably run on whichever short-lived session happens to start after it becomes due.

---

### E8: Does `MemoryGateway` actually guarantee unconditional context injection and storage?

**Question:** does `build_context()` return the Fast Layer regardless of message content, and does `auto_store_turn()` store both sides of a turn without any tool-call decision?

**Method:**
- Called `gateway.build_context("random unrelated question")` — a message with no relevance to any stored memory or identity fact — and checked that the Fast Layer was still present in the result.
- Called `gateway.auto_store_turn(user_message="My name is Deeb and I'm building a Django app", assistant_message="Great, I'll remember your Django project.")` and checked how many entries were stored.

**Result:** `build_context()` returned the Fast Layer (`name == "Deeb"`) even though the query was unrelated — confirming injection is unconditional, not relevance-gated. `auto_store_turn()` stored 2 entries (one from each side of the turn) with zero tool-call decisions involved.

**Conclusion:** this is the direct verification of the fix for the core problem raised as the priority for v1.1 (see `decisions/ADR-002-memory-gateway-for-reliability.md`) — the "always present / always stored" guarantee holds in code, not just in documentation, **for the Gateway path**. It does not and cannot verify the MCP-native-client path, which remains best-effort by protocol design (see E9 below and the "Known gaps" section).

---

## v2 — Warm Layer

### E13: Does the Rule Engine refactor preserve existing extraction behavior?

**Question:** does `auto_extract.py` behave exactly the same after refactoring into `Rule` classes? Do all v1.1 extraction tests still pass? And does the new high-signal exemption work?

**Method:** ported all v1.1 cases from E6 into a new test script (`test_phase0.py`). Added a test for the high-signal exemption (e.g. "اسمي ديب"). Tested new `extract_warm()` against expected EN/AR inputs (location, occupation).

**Result:** all original assertions passed. The new high-signal exemption correctly caught and preserved "اسمي ديب" which was previously at risk of being skipped by `FillerSkipRule`. `extract_warm()` accurately detected warm attributes and auto-generated `context_hint`. Dual extraction (where a warm attribute also returns an `ExtractedFact` for archiving) worked as expected.

**Conclusion:** the Rule Engine (`ADR-004`) successfully decoupled the logic without breaking the baseline.

---

### E14: Does Warm Layer storage implement upsert correctly?

**Question:** does `WarmLayerManager.upsert()` correctly replace old values instead of duplicating them? Does it leave the Fast Layer untouched?

**Method:** tested via `test_phase12.py`. Inserted "I live in Dubai" under key `location`. Checked retrieval. Then upserted "I moved to London" under key `location`. Checked retrieval. Checked if `fast_layer.json` was modified.

**Result:** the second upsert successfully overwrote the first (no duplicate keys). `fast_layer.json` was untouched.

**Conclusion:** Upsert semantics (`ADR-008`) work as specified for the Warm Layer table.

---

### E15: Does two-pass retrieval work without embeddings for keyword matches?

**Question:** does `WarmLayerManager.retrieve_relevant()` correctly return matches based purely on `context_hint` keywords, even without a query embedding?

**Method:** tested via `test_phase12.py`. Passed `query_embedding=None` with the message "I want to talk about travel".

**Result:** it correctly matched the `context_hint` "when discussing travel" and returned the `location` attribute without running cosine similarity.

**Conclusion:** the fast path of the two-pass retrieval is functional.

---

## Known gaps — not yet tested (as of v2)

These are explicitly *not* claims of failure — they simply have not been run yet, mostly due to the development sandbox lacking internet access to Hugging Face for the embedding model download. Recorded here so they aren't lost, and so `roadmap.md`'s "immediate next step" items map directly back to specific open experiments.

### E9 (pending): Live embedding model download and load

**Question:** does `paraphrase-multilingual-MiniLM-L12-v2` actually download and load correctly via `sentence-transformers` on a real machine with internet access?

**Status:** blocked in the current environment (Hugging Face unreachable). All retrieval/storage logic has been tested with either no embedder or randomly generated fake embeddings standing in for real ones — the *decision logic* (should we retrieve, should we store, what score) is verified; the *embedding quality itself* is not.

### E10 (pending): Live MCP client integration

**Question:** does a real MCP client (Claude Desktop or Claude Code) actually call `get_context` and `store_memory` with the frequency and reliability assumed by the v1.1 mitigations (strengthened `instructions` text, opportunistic auto-store inside `get_context`)?

**Status:** not yet run. This is the experiment that would validate or invalidate the "partial mitigation" claim in `decisions/ADR-002-memory-gateway-for-reliability.md` — currently that claim is reasoned from how MCP clients typically behave, not measured.

### E11 (pending): Real-world Arabic round-trip

**Question:** in a live session, does an Arabic emotional statement (e.g. "تزوجت الشهر الماضي") get correctly captured, embedded with the multilingual model, and later retrieved when referenced again in a different Arabic phrasing?

**Status:** not yet run — depends on E9 being unblocked first.

### E12 (pending): Retrieval/storage latency baseline

**Question:** what is the actual measured retrieval latency (linear-scan cosine similarity) at realistic archive sizes, and how close is it to the 150ms trigger threshold defined in `PROJECT_STATUS.md` §5.3?

**Status:** not yet measured — no real embedding model has been run in this environment yet, and the archive has not been populated at any meaningful scale. This is the first concrete metric that should be collected once v2 development begins, per the Metrics defined for each version in `PROJECT_STATUS.md` §6.
