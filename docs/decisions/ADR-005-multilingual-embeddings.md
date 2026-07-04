# ADR-005: Multilingual Embeddings, Not English-Only

**Status:** Accepted

---

## Context

v1 shipped with `all-MiniLM-L6-v2`, an English-oriented embedding model. Real usage involves both Arabic and English, and semantic search quality on Arabic input with an English-only model is unreliable.

## Decision

Switch the default embedding model to `paraphrase-multilingual-MiniLM-L12-v2` (same 384-dimension output, same `.encode()` interface).

## Alternatives Considered

- **Keep the English model, add a separate Arabic model and route by detected language** — rejected as unnecessary complexity; a single multilingual model covers both languages adequately without a language-detection/routing layer.
- **Defer the fix to a later version** — rejected: this is a correctness gap in a core requirement (bilingual usage), not a new feature to schedule.

## Consequences

- Arabic and English messages are embedded in the same vector space with comparable quality.
- Larger one-time model download (~470 MB vs. ~90 MB); still fully local/offline afterward.
- No code changes required beyond the model name in `config.py` — the interface is identical.
