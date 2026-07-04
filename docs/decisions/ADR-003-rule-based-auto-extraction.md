# ADR-003: Rule-Based Extraction for Storage Decisions, Not an LLM Call

**Status:** Accepted

---

## Context

The Gateway (ADR-002) needs to decide automatically what to store and how important it is. The obvious alternative to hand-written rules is a small LLM call, similar to what's planned for retrieval judgment in a later version.

## Decision

`memory/auto_extract.py` uses hand-written, bilingual (Arabic/English) pattern matching — no LLM call — to decide whether a message is worth storing, its importance score, whether it deserves maximum emotional weight, and its tags.

## Alternatives Considered

- **LLM-based storage judgment** — rejected for this specific job. The asymmetry matters: a wrong *retrieval* judgment just means one less relevant memory surfaced (low cost). A wrong *storage* judgment can mean silently losing information forever — a much higher-stakes, harder-to-notice failure.
- **No automatic scoring — require the calling model to always specify importance/tags** — rejected: reintroduces inconsistency (one model defaults everything to `0.5`, another marks everything maximally important), undermining the forgetting system.

## Consequences

- Storage decisions are consistent regardless of which model is calling — the same input always produces the same importance/tags.
- Rule-based extraction is less nuanced than an LLM judgment: it will miss patterns that don't match a known phrase. Accepted as a v1.1 tradeoff, not a permanent ceiling — see ADR-004 for how new patterns get added without the file becoming unmanageable.
