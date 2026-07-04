# ADR-006: Forgetting-Cycle Catch-Up on Startup, Not Only a Live Timer

**Status:** Accepted

---

## Context

v1's forgetting cycle relied entirely on an in-process weekly interval timer. MCP server processes are typically short-lived (started per session by the client, stopped afterward) — a timer requiring continuous uptime for a full week essentially never fires in that deployment shape.

## Decision

Track `last_forgetting_run` in a `system_state` table in the archive, and run a catch-up check at every process startup — executing the forgetting cycle immediately if it's overdue, before the MCP server starts serving requests.

## Alternatives Considered

- **A separate always-on daemon process just for the forgetting cycle** — rejected: adds operational complexity for what should be a lightweight local tool, and contradicts the project's "no separate process needed" constraint.
- **Check overdue status on every `get_context`/`store_memory` call** — rejected: unnecessary overhead on the hot path for something that only needs to happen roughly weekly.

## Consequences

- The forgetting cycle reliably runs on whichever short-lived session happens to start after it becomes due, instead of requiring continuous uptime.
- The live in-process timer is kept as a secondary mechanism for the less common case of a genuinely long-running process — startup catch-up is now the primary guarantee.
