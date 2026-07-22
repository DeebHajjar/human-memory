# ADR-011: Always-On Semantic Retrieval with a Raw-Similarity Floor

**Status:** Accepted — supersedes the keyword-trigger retrieval design (v1, described in `architecture.md` and validated in E3)

---

## Context

Manual testing against a bilingual archive (~11 memories, mixed English/Levantine-Arabic) showed retrieval failing in ways that first looked like an embedding-quality problem but, once measured directly (experiments.md E18), turned out to be mostly architectural:

1. **The keyword gate silently discarded high-confidence matches.** `RetrievalEngine.should_retrieve()` only allowed semantic search when the message contained a time-reference phrase or an exact stored-tag word. "What's my cat's name?" / "شو اسم قطتي؟" both scored cosine **0.547** against the stored cat memory — a decisive match — and both returned nothing, because the memory's tags (`["test", "pets"]`) shared no exact word with either query. The gate was also the entire explanation for the "Arabic tags fix Arabic retrieval" observation: tags are never embedded; adding the tag `طعام` simply made a query word match a tag and opened the gate. Exact-word tag matching is morphology-fragile in both languages (`قطتي` ≠ `قطة`, "cat" ≠ "pets") and cannot be patched into reliability.
2. **The gate saved nothing.** `get_context` already embeds every incoming message unconditionally for the Warm Layer, and the archive search is a full scan either way. The gate's only real effect was discarding an embedding that had already been paid for.
3. **Pass/fail mixed importance into the threshold.** `0.7 × sim + 0.3 × importance ≥ 0.30` let entries with similarity as low as ~0.26 through. Measured on "What is Deeb's favorite food?": 7 of 12 memories passed, with the cat memory ranked above the actual answer. Same-person memories cluster at sim 0.25–0.40 in this model, and the effective floor sat below that noise band.
4. **The Warm Layer keyword pass matched stopwords.** Any message word appearing in `context_hint` auto-included the attribute with a fabricated score of 0.92 (sim treated as 1.0). Hints are natural-language sentences, so words like "the"/"for"/"should" matched nearly every English message — one running-training query returned **all three** stored warm attributes as keyword hits. The semantic pass had the same importance-in-the-threshold problem (effective sim floor ~0.41, with measured false positives at 0.42–0.43).

## Decision

**Semantic search runs on every message, for both the Archive and the Warm Layer. Inclusion is decided by raw cosine similarity only. Keyword signals are score boosts, never gates or auto-includes.**

- `should_retrieve()`, the time-reference phrase lists, and the tag-word gate are removed. `get_context_memories()` always searches; its returned boolean (surfaced as `retrieval_triggered`) now means "relevant memories found", not "a search was attempted".
- **Archive scoring:** `sim = cosine(query, content)`, plus `RETRIEVAL_TAG_BOOST` (0.15) when one of the *entry's own* tags appears as a word in the message. Include only if boosted sim ≥ `RETRIEVAL_SIM_THRESHOLD` (0.35). Rank passers by `0.7 × sim + 0.3 × importance` — importance orders results but never admits them.
- **Warm Layer scoring:** same shape — `sim = cosine(query, value)`, plus `WARM_LAYER_HINT_BOOST` (0.15) when a *content word* of `context_hint` (bilingual stopwords excluded) appears in the message; include only if ≥ `WARM_LAYER_SIM_THRESHOLD` (0.55); rank by `0.8 × sim + 0.2 × importance`. The fabricated-score keyword auto-include is deleted.
- Thresholds are calibrated from measured data (E18): archive true matches measured 0.36–0.55 vs a noise band topping out ~0.40; the warm floor 0.55 sits between a measured phrasing-only false positive (0.511) and the true match (0.583 raw). Boosts mean keyword-corroborated matches face an effective floor of ~0.20/0.40, which is what rescues cross-lingual true matches (e.g. the Arabic coffee memory at raw sim 0.223 + tag boost).
- Embedded text is unchanged (Archive: `content`; Warm Layer: `value`), so **no data migration** is required.

`tools/replay_retrieval.py` is committed as the acceptance benchmark: it replays a fixed bilingual query set against the live DB through the real code paths and prints raw sims, boosts, and pass/fail. Re-run it before/after any future change to scoring or the embedding model.

## Alternatives Considered

- **Keep the gate, expand its patterns** (mandatory bilingual tags, stemming, question-word triggers) — rejected: whack-a-mole against morphology in two languages, and the gate protects nothing (the embedding is already computed). Any fixed word list re-creates the same silent failure for the next unanticipated phrasing.
- **Keep combined-score thresholding and just raise 0.30** — rejected: importance in the pass/fail decision means the effective similarity floor shifts per entry; a high-importance irrelevant memory outcompetes a low-importance relevant one at the margin. Importance is a *ranking* signal.
- **Per-language thresholds** (lower floor for cross-lingual queries) — rejected for now: needs language detection plus calibration data per pair, and the measured cross-lingual gap is better addressed by the tag boost today and a stronger embedding model later.
- **Hybrid BM25 + embedding fusion** — deferred: strictly more robust (exact names always surface), but significantly more code; revisit if precision on rare terms still disappoints after a model upgrade.

## Consequences

- Direct recall questions now work without magic phrasing or tag luck; the E18 acceptance table (cat EN+AR, food EN+AR, running, warm-attribute probes) passes fully.
- Warm-layer stopword false positives are eliminated; warm attributes only surface on genuine semantic relevance (or content-word hint corroboration).
- `retrieval_triggered` changed meaning. No schema change — but any client logic that treated `false` as "the archive was not even searched" now reads it as "nothing relevant found".
- Every message now pays the full-scan similarity loop (it already paid the embedding). At the intended scale (thousands of rows) this is sub-millisecond-per-row NumPy work; an index (FAISS) only becomes worth it far beyond that.
- Tags gained a precise, principled role: per-entry evidence boost. Descriptive tags in **both** of the user's languages remain the best practice (`store_memory` docstring now says so) — but their absence no longer makes a memory unfindable.
- **Residual, explicitly out of scope:** dialectal-Arabic embedding quality. A Levantine query against its own Levantine memory measured sim **0.031** (E18) — no threshold or boost design can recover that; it requires a stronger multilingual embedding model (candidate follow-up to ADR-005, tracked as the "model upgrade" option in the retrieval-quality diagnosis).
