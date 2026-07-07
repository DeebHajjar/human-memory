# Architecture Decision Records

One file per decision. Each follows: **Context → Decision → Alternatives Considered → Consequences**.

If a decision is later reversed or replaced, a new ADR is added noting it supersedes the old one — existing ADRs are not rewritten after acceptance.

| ADR | Title | Status |
|---|---|---|
| [001](ADR-001-use-mcp-protocol.md) | Use MCP as the integration protocol | Accepted |
| [002](ADR-002-memory-gateway-for-reliability.md) | A deterministic Gateway, alongside MCP, not instead of it | Accepted |
| [003](ADR-003-rule-based-auto-extraction.md) | Rule-based extraction for storage decisions, not an LLM call | Accepted |
| [004](ADR-004-pluggable-rule-engine.md) | The rule engine must be pluggable, not a growing flat file | Accepted — not yet implemented |
| [005](ADR-005-multilingual-embeddings.md) | Multilingual embeddings, not English-only | Accepted |
| [006](ADR-006-forgetting-cycle-startup-catchup.md) | Forgetting-cycle catch-up on startup, not only a live timer | Accepted |
| [007](ADR-007-archive-not-delete-superseded-facts.md) | Superseded facts are archived, not deleted | Accepted — not yet implemented (v7) |
| [008](ADR-008-warm-layer-dual-routing-and-upsert.md) | Warm Layer uses upsert semantics but dual-routes to Archive | Accepted |
