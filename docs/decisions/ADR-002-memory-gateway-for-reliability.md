# ADR-002: A Deterministic Gateway, Alongside MCP, Not Instead of It

**Status:** Accepted — the "partial mitigation" consequence below (opportunistic auto-store in `get_context`) was later removed; see [ADR-009](ADR-009-remove-opportunistic-auto-store-from-get-context.md).

---

## Context

Because MCP is client-driven (ADR-001), both context injection and memory storage only happen if the calling model chooses to invoke `get_context` / `store_memory`. In practice this means the Fast Layer can silently disappear in long conversations, and the archive can end up incomplete because `store_memory` is called inconsistently.

## Decision

Build `memory/gateway.py` (`MemoryGateway`) as a second entry point — plain Python code, not an MCP tool — for direct API integrations (custom wrappers around the OpenAI/Gemini/Claude API). It guarantees: the Fast Layer is always injected, and every turn is auto-stored via rule-based extraction, with no dependency on the model choosing to call a tool.

## Alternatives Considered

- **Force MCP clients to always call the tools** — not possible. Tool invocation is controlled by the client, not the server; no amount of prompting makes this a hard guarantee.
- **Drop MCP entirely, require a custom wrapper for everyone** — rejected: breaks integration with MCP-native clients, a core requirement.
- **Accept the unreliability** — rejected: this was the priority problem to fix.

## Consequences

- The reliability guarantee holds fully only for the Gateway path (direct API integrations), not for MCP-native clients — this limitation is documented, not hidden.
- Partial mitigation added for MCP-native clients: `get_context` (the tool most reliably called every turn) now also opportunistically auto-stores the incoming user message as a side effect. This does not capture the assistant's own reply — only the Gateway guarantees that.
- Both entry points share the same underlying `memory/` package; the Gateway changes *when* logic runs, not what the logic is.
