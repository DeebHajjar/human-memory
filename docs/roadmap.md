# Roadmap

*Human Memory System — current plan*

This is the condensed, actionable view of where the project is and what's next. For full detail behind any item here, see `PROJECT_STATUS.md` (the source of truth). For why decisions were made, see `decisions/` (one ADR per decision).

> **Note:** The version numbers in this document refer to the project's architectural roadmap and development milestones, not GitHub release versions. Public releases follow Semantic Versioning (e.g. v0.x.y until the first stable release, then v1.0.0)


---

## Where we are now

**v1.1 — shipped.** Fast Layer, Archive, keyword-only retrieval, forgetting system, MCP server, plus three v1.1 reliability fixes: multilingual embeddings, forgetting-cycle startup catch-up, and a deterministic Gateway with rule-based auto-extraction.

**Not yet verified against a live environment** (blocked on sandbox network access — see `experiments.md` E9–E12): real embedding-model download, live MCP client integration, real-world Arabic round-trip, and a real retrieval-latency baseline.

---

## Immediate next step (before any new version work starts)

These come directly from `PROJECT_STATUS.md` §8 and `experiments.md`'s "Known gaps" — nothing new should be built until these are confirmed:

1. Run `main.py` on a machine with real internet access; confirm `paraphrase-multilingual-MiniLM-L12-v2` downloads and loads. *(closes E9)*
2. Connect to a real MCP client (Claude Desktop/Code); observe whether the strengthened `instructions` text results in consistent `get_context` calls in practice. *(closes E10)*
3. If a custom API wrapper is planned, integrate `memory/gateway.py` and confirm `process_turn()` against a real model.
4. Run a real Arabic round-trip: store "تزوجت الشهر الماضي", confirm `emotional_weight = 1.0`, confirm later retrieval works. *(closes E11)*
5. Manually verify startup catch-up: backdate `last_forgetting_run` in `archive.db`, restart, confirm the cycle runs immediately.
6. Collect a first real retrieval-latency measurement once the archive has realistic content in it. *(closes E12 — this number matters for Section "Performance & Scalability" below.)*

---

## Prerequisite refactor: pluggable rule engine

**Must land before v2's Warm Layer detection logic is written.** `memory/auto_extract.py` is currently a flat set of regex pattern lists. Per [`ADR-004`](decisions/ADR-004-pluggable-rule-engine.md), this needs to become a small plugin architecture — each detection concern as its own Rule object (`IdentitySignalRule`, `EmotionalSignalRule`, `FillerSkipRule`, ...) registered in a list — *before* new pattern types accumulate further. Warm Layer's relevance-detection rules (v2) and eventually v7's contradiction/duplicate-detection rules should be written directly in this architecture, not bolted onto the old flat file and refactored later.

---

## Version plan

Each version adds exactly one capability. No version breaks the existing `get_context`/`store_memory` MCP interface — new tools are additive only. Starting at v2, every version defines explicit **Metrics** (retrieval latency, retrieval accuracy, useful-memory ratio, database size) — success is measured, not just claimed. Full detail for every item below lives in `PROJECT_STATUS.md` §6.

| # | Version | Adds | New MCP tools |
|---|---|---|---|
| — | *(prerequisite)* | Pluggable rule engine refactor | none |
| v2 | **Warm Layer** | Secondary attributes (biography, context-specific preferences), retrieved on semantic relevance, faster than full Archive search | `update_warm_attribute` (proposed) |
| v3 | **Task Layer** | One active project's working state; suspend/resume with compressed state | `set_active_task` |
| v4 | **LLM-based retrieval judgment** | Step 2 of the original two-step retrieval decision (small/fast model, binary yes/no, graceful fallback) | none (internal) |
| v5 | **AI internal thought memory** | `assistant_thought` source populated and retrievable; AI can resume its own prior reasoning | `get_thought_history` (proposed) |
| v6 | **Topic-switching buffer** | Conversation-scoped rolling working-memory buffer for in-session topic shifts | none (internal) |
| v7 | **Memory Consistency** | Contradiction detection, fact replacement (archived, not deleted), duplicate merging | none (automatic, inline) |

### Standing item — not tied to a version number

**Vector index migration (linear scan → FAISS/HNSW).** Deliberately *not* scheduled to a specific version, and deliberately *not* an ADR — no choice has actually been made yet between FAISS and HNSW; that choice will get its own ADR once it's made. What exists now is only a trigger condition, tracked here in the roadmap:
- Archive exceeds **~20,000–50,000 entries**, **or**
- Measured p95 retrieval latency exceeds **~150ms**

Whichever comes first. SQLite stays the source of truth; the index becomes a rebuildable cache alongside it. Revisit only once real usage data exists (this is what step 6 above starts collecting). Full detail in `PROJECT_STATUS.md` §5.

---

## Version details (condensed)

### v2 — Warm Layer
Secondary, context-specific attributes (birthdate, location, occupation, situational preferences) — not always-loaded like the Fast Layer, not buried in full semantic search like the Archive. Must be retrieved faster than Archive search. **Depends on:** the rule-engine refactor above.

### v3 — Task Layer
One active task at a time; switching suspends the current task's compressed state and loads another's. Makes the Fast Layer's existing (currently unused) `active_task_id` field functional. Optional adapter for external tools like Graphify — not a hard dependency.

### v4 — LLM-based retrieval judgment
Closes the gap named in `PROJECT_STATUS.md` §3.5: keyword-only triggers (v1) will always under-fire on subtle references and can over-fire on generic tag words. Adds a small, fast, *configurable* model call as a fallback only when Step 1 is ambiguous — with a hard requirement to degrade gracefully (default to no-retrieval) on failure or timeout, never block the main response.

### v5 — AI internal thought memory
Activates the `source: assistant_thought` field that already exists in the `MemoryEntry` model but is unused. Lets the AI preserve reasoning, not just speech, across sessions. Needs a retrieval path that can be filtered to reasoning only, and a decision on whether unused thoughts decay faster than user-facing facts.

### v6 — Topic-switching buffer
A conversation-scoped (not persistent) rolling buffer holding the current topic in full and previous topics compressed to 1–2 sentences, so returning to an earlier topic *within the same conversation* doesn't require a full Archive search.

### v7 — Memory Consistency
Closes the gap named in `PROJECT_STATUS.md` §3.6: v1/v1.1 have no way to detect that a new fact contradicts an old one, or that two entries are near-duplicates. Adds a `status` (`active`/`superseded`/`merged`) + `superseded_by` schema addition, resolved automatically and inline during storage — no new tool required, to avoid reintroducing the reliability problem addressed in [`ADR-002`](decisions/ADR-002-memory-gateway-for-reliability.md). Contradiction detection stays rule-assisted (embedding similarity + tag overlap), not a full NLI model — consistent with the project's stated non-goal of chasing general reasoning capability (see `architecture.md` §1 and `PROJECT_STATUS.md` §1 Scope & Non-Goals). See also [`ADR-007`](decisions/ADR-007-archive-not-delete-superseded-facts.md) for the specific decision on how superseded facts are handled.

---

## Open questions to resolve during implementation (not blocking, but worth flagging early)

- **v3:** should the buffer/task-compression summary be produced with a rule-based approach (consistent with the v1.1 philosophy) or does task-state compression genuinely need an LLM call? Worth a small experiment before committing.
- **v6:** should the working-memory buffer ever persist across sessions, or stay strictly ephemeral? Current lean (per `PROJECT_STATUS.md` §6, v6) is ephemeral-by-default with optional archival on flush — not yet finalized.
- **v7:** what similarity/tag-overlap threshold correctly distinguishes "duplicate" from "related but distinct" facts? This will need empirical tuning once real archive data exists, similar to the retrieval-threshold tuning already noted for the vector-index migration.
