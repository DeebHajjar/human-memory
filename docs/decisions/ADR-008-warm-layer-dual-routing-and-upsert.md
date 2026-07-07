# ADR-008: Warm Layer Dual Routing and Upsert Semantics

**Status:** Accepted

---

## Context

With the introduction of the Warm Layer (v2) to handle secondary, context-specific biographical attributes (e.g., location, occupation, recurring habits), we needed a way to manage facts that change over time. For example, if a user says "I live in Dubai" and later says "I moved to London," the system must surface "London" as the current location without the AI being confused by a conflicting "Dubai" fact in the same context block.

## Decision

We decided to implement **upsert semantics** for the Warm Layer and **dual routing** for auto-extraction:
1. **Upsert Semantics:** In the `warm_layer` SQLite table, storing a new value for an existing key (e.g., `location`) replaces the old value outright. 
2. **Dual Routing:** When a warm candidate is detected via `auto_extract.extract_warm()`, it is upserted into the Warm Layer (to update the "current value") AND it is additionally passed through standard extraction (`auto_extract.extract()`) to be appended to the Archive.

## Alternatives Considered

- **Store only in the Warm Layer (no dual routing):** Rejected. This would overwrite history, making it impossible for the AI to recall where the user used to live or work. 
- **Append-only in the Warm Layer:** Rejected. This would defeat the primary purpose of the Warm Layer, which is to provide a fast, contradiction-free lookup for current biographical state. It would require complex contradiction-resolution logic (planned for v7) just to know the user's current city.

## Consequences

- The Warm Layer table remains extremely small (typically < 100 rows), ensuring two-pass retrieval (keyword match + semantic fallback) stays fast.
- The Archive maintains a complete, chronological record of the user's life, preserving the ability to answer historical questions.
- `MemoryGateway` must orchestrate the dual routing, ensuring that a single message can result in both a Warm Layer upsert and an Archive insert without errors.
