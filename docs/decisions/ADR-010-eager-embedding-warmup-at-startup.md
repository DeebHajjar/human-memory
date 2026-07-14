# ADR-010: Warm Up the Embedding Model Eagerly at Startup, Not Lazily on First Request

**Status:** Accepted

---

## Context

The embedding model (`sentence-transformers` + `torch`) takes ~10–12s to import and load even when the model is already cached on disk, plus a network round-trip to the Hugging Face Hub on load. Through v2.2, `mcp_server.py` loaded it **lazily** inside `_get_embedder()` on first use. Every tool handler (`get_context`, `store_memory`, `update_warm_attribute`) calls `_get_embedder()`, so the *first* tool call after a cold start absorbed the entire load cost — exceeding the MCP client's per-tool-call timeout, so that first call appeared to hang. An immediate retry, finding the model already cached in the process-global `_embedder`, succeeded instantly. The symptom therefore occurred exactly once per server process.

Neither entry point avoided this: `main.py`'s `run_startup_catchup()` loads the model only when the forgetting cycle is overdue and via a *separate* `_embedder` global in `scheduler.py`, so it never warmed `mcp_server.py`'s; and `python mcp_server.py` skips catch-up entirely.

## Decision

Load the embedding model eagerly at server startup, **before the server processes any request**, using FastMCP's documented `lifespan` async context-manager hook. The lifespan calls the existing `_get_embedder()` and performs one dummy `encode("warm up")` so any first-call lazy initialisation inside the model is paid up front. Because both entry points ultimately call `mcp.run()`, the warm-up covers both. `_get_embedder()` is left in place as an idempotent fallback, so behaviour is unchanged (correct, just slow once) if the warm-up is ever skipped.

## Alternatives Considered

- **Explicit `warm_up()` call before `mcp.run()` in both `main.py` and `mcp_server`'s `__main__`** — works and loads the model before the transport starts, but must be duplicated in two places to cover both entry points and isn't the SDK-blessed lifecycle mechanism.
- **Eager load at module import** — covers both entry points, but makes *any* import of `mcp_server` (e.g. from a test) pay the full model-load cost as an import side effect.
- **Raise the client's tool-call timeout** — not controllable from the server, client-dependent, and doesn't address the root cause.
- **Keep lazy loading** — rejected: the reported "first call hangs, retry works" symptom is its direct consequence.

## Consequences

- The one-time ~12s model-load cost moves from the first tool call to server startup (paid during the `initialize` handshake). Measured after the fix: first `get_context` call ≈ 0.088s vs 0.036s for the second — effectively equal, confirming the model is fully warm before the first request. `initialize` itself now takes ~12s, but clients wait on `initialize` and its timeout is far more tolerant than the per-tool-call timeout.
- Same spirit as [ADR-006](ADR-006-forgetting-cycle-startup-catchup.md): startup is where necessary-but-expensive work happens before serving. Startup is now the single point at which the embedder is guaranteed ready.
- Optional further hardening (not adopted): setting `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` after the first download removes the HF Hub round-trip on load. Left out of the fix because it must not be set for the very first-ever download, so it can't be forced unconditionally in code.
