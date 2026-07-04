# ADR-001: Use MCP as the Integration Protocol

**Status:** Accepted

---

## Context

The system must work with "any AI model" — Claude, GPT, Gemini, or others — without maintaining a separate integration for each one.

## Decision

Expose the memory system as an MCP (Model Context Protocol) server, with two tools: `get_context` and `store_memory`.

## Alternatives Considered

- **Custom REST API** — would require every integration to write bespoke HTTP client code, with no shared standard.
- **Python SDK only** — would exclude non-Python integrations and any MCP-native client (Claude Desktop/Code) entirely.

MCP was chosen because it's an open, model-agnostic protocol with native support in MCP-native clients, and its tool-call model maps directly onto the two operations the system needs.

## Consequences

- Integrates with MCP-native clients (Claude Desktop/Code) with no custom glue code.
- **MCP is client-driven**: the server cannot force a client to call its tools on a given turn. This is a protocol property, not a bug, and directly motivates ADR-002.
