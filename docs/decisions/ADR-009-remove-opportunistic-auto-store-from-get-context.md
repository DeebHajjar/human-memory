# ADR-009: Remove Opportunistic Auto-Store from `get_context`

**Status:** Accepted — partially supersedes ADR-002

---

## Context

ADR-002 added a partial mitigation for MCP-native clients: `get_context` opportunistically ran rule-based extraction (`auto_extract.extract_warm()` / `auto_extract.extract()`) on the incoming message and wrote the result into the Warm Layer and/or Archive as a side effect, on the theory that `get_context` is the tool most reliably called every turn.

In real use this caused a concrete problem: `get_context` is also the natural tool to call for test/exploratory queries (e.g. "Where do I live?") that are questions *about* stored memory, not new facts to remember. Because the auto-store side effect could not distinguish a question from a statement, these queries were being stored verbatim into the Archive and/or upserted into the Warm Layer as if they were memories. Later semantic retrieval then matched against this stored query text instead of the actual facts, degrading retrieval quality — the opposite of what the mitigation was meant to achieve.

This is a separation-of-concerns failure: `get_context` had two responsibilities (retrieval, and opportunistic storage) bundled into one call, with no way for the caller to get one without the other.

## Decision

Remove the auto-store side effect from `get_context` entirely. `get_context` is now a pure read/retrieval function: it loads the Fast Layer, runs Warm Layer retrieval, runs Archive retrieval, and returns the assembled `LayeredContext` — nothing else. It never calls `archive.store()`, `warm_layer_mgr.upsert()`, or any `auto_extract.extract*()` function.

All writes to memory now happen only through the explicit tools that already existed for this purpose: `store_memory` (Archive) and `update_warm_attribute` (Warm Layer). The `auto_extract` rule-based extraction engine itself is unchanged — it is still used by `store_memory` to fill in `importance`/`tags`/`emotional_weight` when the caller omits them, and by `memory/gateway.py`'s `auto_store_turn()` for the Gateway integration path (ADR-002, unaffected by this decision).

## Alternatives Considered

- **Try to distinguish questions from statements before auto-storing** — rejected: this would require exactly the kind of NLP judgment call the project deliberately avoids for storage decisions (see ADR-003 — storage decisions are rule-based specifically because a wrong storage judgment is unrecoverable, and reliably classifying "question vs. statement" bilingually is not a rule-based problem).
- **Keep auto-store but exclude interrogative-looking messages** — rejected: pattern-matching questions (e.g. "?", "من", "what", "هل") is brittle and bilingual question detection has the same false-negative/false-positive problems as the retrieval triggers this project already documents as a known limitation (`PROJECT_STATUS.md` §3.5); it would trade one silent-corruption failure mode for another.
- **Leave it as-is and document the risk** — rejected: this was an active, observed data-quality bug (stored query text polluting retrieval), not a theoretical edge case; a known limitation is acceptable, a self-inflicted retrieval-quality regression is not.

## Consequences

- `get_context` no longer contributes to the "reliability" problem's mitigation described in ADR-002 — the archive/Warm Layer will no longer accumulate entries just because a client happened to call `get_context`.
- The reliability gap ADR-001/ADR-002 describe is **not resolved by this change** and is not meant to be — it returns to being fully open for MCP-native clients: nothing is stored unless the calling model explicitly calls `store_memory` or `update_warm_attribute`. This is now purely a matter of the model's own judgment and the strength of the server's `instructions` text, same as it was before the v1.1 mitigation was added. The Gateway path (`memory/gateway.py`) is unaffected and remains the only integration with an unconditional storage guarantee.
- Any Archive/Warm Layer entries written by the old opportunistic path (raw stored queries) are not automatically cleaned up by this change — they are ordinary entries subject to the existing forgetting cycle like anything else.
