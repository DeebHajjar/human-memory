# ADR-004: The Rule Engine Must Be Pluggable, Not a Growing Flat File

**Status:** Accepted — not yet implemented

---

## Context

`memory/auto_extract.py` (ADR-003) currently holds all detection patterns as flat regex lists in a single file. This is manageable at the current size, but future work — Warm Layer detection, retrieval judgment, contradiction/duplicate detection — will all want to add more pattern types over time.

## Decision

Convert `auto_extract.py` from a flat pattern list into a small plugin architecture: each detection concern (e.g. `IdentitySignalRule`, `EmotionalSignalRule`, `FillerSkipRule`) becomes its own Rule object with a common interface, registered in a list the extraction engine iterates over.

## Alternatives Considered

- **Keep adding patterns to the flat file as needed** — rejected: the cost of untangling this compounds the longer it's deferred, and each new feature that touches extraction would make the file harder to review.
- **Defer this decision until it becomes a visible problem** — rejected: deciding now means new logic (e.g. Warm Layer's relevance rules) can be written directly in the target architecture instead of being added to the old structure and refactored afterward.

## Consequences

- Adding a new language or signal type becomes "add a new Rule class," not "edit a growing shared file."
- This is a prerequisite for Warm Layer development, not a parallel-track cleanup — Warm Layer's detection logic should be written as Rule objects from the start.
- Not yet implemented as of this writing; tracked as a blocking item in the roadmap.
