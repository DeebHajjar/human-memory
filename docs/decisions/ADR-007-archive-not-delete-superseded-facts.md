# ADR-007: Superseded Facts Are Archived, Not Deleted

**Status:** Accepted — not yet implemented (planned for v7, Memory Consistency)

---

## Context

When a newer fact contradicts an older stored one (e.g. the user reports a new city after a different city was previously stored), the system needs a defined behavior for what happens to the old entry.

## Decision

Mark the superseded entry with `status = superseded` and a `superseded_by` pointer to the new entry, rather than deleting it outright. Normal retrieval only surfaces `active` entries; superseded ones remain inspectable in the database.

## Alternatives Considered

- **Delete the old fact immediately on contradiction** — rejected: a false-positive contradiction detection would make the deletion unrecoverable, with no audit trail.
- **Keep both facts active and let retrieval return both** — rejected: this is the exact problem being solved (conflicting facts surfaced together confuse the responding model).

## Consequences

- Mirrors the existing compress-rather-than-delete philosophy already used by the forgetting system, for consistency across the codebase.
- Preserves history for audit purposes while ensuring only the current fact is surfaced during normal use.
- Requires a schema addition (`status`, `superseded_by` columns) not yet implemented, since the contradiction-detection feature itself (v7) has not been built yet.
