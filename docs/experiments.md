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

## Known gaps at v2 — since resolved by manual real-machine testing

These were originally recorded as untested (mostly due to the development sandbox lacking internet access to Hugging Face for the embedding model download). All were subsequently run manually on a real machine and are now resolved — see below.

### E9 (✅ resolved — v2.1): Live embedding model download and load

**Question:** does `paraphrase-multilingual-MiniLM-L12-v2` actually download and load correctly via `sentence-transformers` on a real machine with internet access?

**Result:** confirmed working. The model downloaded and loaded successfully on the first real-machine run. The startup catch-up forgetting cycle also ran and completed successfully (updated=6, deleted=0, compressed=0) — although this run also revealed the timezone-naive bug (E16 below), which was fixed before confirming the full run succeeded.

### E10 (✅ resolved): Live MCP client integration

**Question:** does a real MCP client (Claude Desktop or Claude Code) actually call `get_context` and `store_memory` with the frequency and reliability assumed by the v1.1 mitigations (strengthened `instructions` text, opportunistic auto-store inside `get_context`)?

**Method:** connected the server to Claude Desktop via `claude_desktop_config.json` and ran a real multi-turn conversation.

**Result:** `get_context` was called consistently on every turn, as instructed by the strengthened `instructions` text. This validates the "partial mitigation" claim in `decisions/ADR-002-memory-gateway-for-reliability.md` for Claude Desktop specifically — the model reliably called the tool without needing an explicit per-turn reminder.

**Conclusion:** the MCP-native mitigation path works in practice, at least for Claude Desktop. The underlying protocol limitation (a client *could* choose not to call the tool) still stands — this result confirms observed behavior, not a protocol-level guarantee.

### E11 (✅ resolved): Real-world Arabic round-trip

**Question:** in a live session, does an Arabic emotional statement (e.g. "تزوجت الشهر الماضي") get correctly captured, embedded with the multilingual model, and later retrieved when referenced again in a different Arabic phrasing?

**Method:** stored an Arabic emotional statement through a live session, then referenced it later in the same conversation using different phrasing.

**Result:** the memory was correctly retrieved despite the differing phrasing, confirming the multilingual embedding model (`ADR-005`) produces usable semantic similarity for Arabic content end-to-end, not just in isolated unit tests.

**Conclusion:** the Arabic semantic-search gap identified in `PROJECT_STATUS.md` §3.2 is confirmed fixed in real usage.

### E12 (✅ resolved): Retrieval/storage latency baseline

**Question:** what is the actual measured retrieval latency (linear-scan cosine similarity) at realistic archive sizes, and how close is it to the 150ms trigger threshold defined in `PROJECT_STATUS.md` §5.3?

**Method:** observed retrieval responsiveness during manual real-machine testing (no instrumented timing).

**Result:** retrieval felt responsive with no noticeable delay at the archive sizes exercised during testing. No precise latency figure or archive size was captured.

**Conclusion:** no regression or noticeable slowness at current (small) archive scale. This is a qualitative result, not a measurement — an instrumented figure against the 150ms threshold and a known archive size is still worth capturing before the linear-scan migration trigger in `PROJECT_STATUS.md` §5 becomes relevant at larger scale.

---

## v2.1 — Hotfix

### E16: Does the forgetting cycle survive a database with timezone-naive timestamps?

**Question:** can `run_forgetting_cycle()` process entries written by v1 / v1.1 (which stored `datetime.now()` without `timezone.utc`) without crashing?

**Method:** ran `main.py` on a real machine with an existing `archive.db` populated by v1.1. The startup catch-up triggered the forgetting cycle immediately.

**Result:** crashed with `TypeError: can't subtract offset-naive and offset-aware datetimes` in `_recency_score()` inside `memory/forgetting.py`. The cause was traced to `_row_to_entry()` in `memory/archive.py` returning a `datetime` without `tzinfo`, while `datetime.now(timezone.utc)` is offset-aware.

**Fix:** added a `tzinfo` guard in `_row_to_entry()`: if `dt.tzinfo is None`, replace it with `timezone.utc` before returning.

**Re-run result:** crash gone. Startup catch-up completed successfully: `updated=6, deleted=0, compressed=0`. Scheduler started and the live weekly timer was registered normally.

**Conclusion:** the fix is correct and backward-compatible. Any pre-existing database (v1 or v1.1) can be used with v2.1 without migration.

---

## v2.3 — Fix (first-call timeout)

### E17: Why does the first tool call after a cold server start hang, and does eager startup warm-up fix it?

**Question:** on a freshly started server, the first tool call (usually `get_context`) never returns and the client times out, while an immediate retry works and it only happens once per process. Where is the time going, and does warming the embedding model up at startup remove the first-call penalty?

**Method:** ran a read-only timing harness (via the project venv) that reproduces `mcp_server.py`'s exact init path, timing each step separately: project imports, `FastLayerManager()`, `ArchiveDB()` + schema, `RetrievalEngine()`, `WarmLayerManager()` + schema, then `import sentence_transformers`, `SentenceTransformer(model)` load, and first/second `.encode()`. Then verified the fix end-to-end with a small `mcp.client.stdio` client: launched `mcp_server.py`, ran `initialize`, and timed two consecutive `get_context` calls. Model was already cached locally for both measurements.

**Result (diagnosis, before fix):**

| Step | Time |
|---|---|
| import project modules | 0.120s |
| `FastLayerManager()` | 0.000s |
| `ArchiveDB()` + schema init (SQLite) | 0.014s |
| `RetrievalEngine()` | 0.000s |
| `WarmLayerManager()` + schema | 0.000s |
| **eager/cheap init subtotal** | **0.134s** |
| `import sentence_transformers` (pulls in torch) | 5.002s |
| `SentenceTransformer(model)` load | 6.571s |
| first `.encode()` | 0.219s |
| **first-tool-call one-time cost** | **≈ 11.8s** |
| second `.encode()` | 0.016s |

The eager init (SQLite schema, layer managers) is ~0.13s — not the bottleneck; lazy DB init and a stdio handshake race were ruled out. The entire cost is `import sentence_transformers` + model load (~11.8s, model already cached), confirmed one-time by the 16ms second encode. A network round-trip to the HF Hub also happens on load (observed "sending unauthenticated requests to the HF Hub" warning), which worsens the stall on a slow/unreachable network.

**Result (after fix — eager warm-up via FastMCP `lifespan`):** server stderr showed `Warming up embedding model…` → `Embedding model ready in 11.6s — server accepting requests` during `initialize`, i.e. before serving. First `get_context` call: **0.088s**; second: **0.036s** — effectively equal.

**Conclusion:** root cause confirmed as lazy first-use model loading, not DB init or transport lifecycle. Warming the model up at startup (`ADR-010`) shifts the one-time ~12s cost off the first tool call and onto the `initialize` handshake, so the first real request is fast. Symptom (first call hangs, retry works, once per process) is resolved.
