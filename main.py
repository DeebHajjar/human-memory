"""
Human Memory System v1.1 — Entry Point

Starts:
  1. Startup catch-up check (v1.1) — runs the forgetting cycle immediately
     if it's overdue, since MCP server processes are usually short-lived
     and a live weekly timer alone often never fires (see PROJECT_STATUS.md).
  2. Background scheduler — kept as a secondary mechanism for long-running
     deployments.
  3. MCP server (stdio transport — model-agnostic)

Usage:
    python main.py
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,   # keep stdout clean for MCP stdio protocol
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Human Memory System v1.1 starting…")

    # v1.1: catch up on the forgetting cycle immediately if overdue, rather
    # than relying on the process staying alive for a full week.
    from scheduler import run_startup_catchup, start_scheduler
    catchup_result = run_startup_catchup()
    if catchup_result.get("ran"):
        logger.info(f"Startup catch-up ran: {catchup_result}")
    else:
        logger.info("Startup catch-up: nothing overdue")

    # Secondary mechanism for long-running deployments
    scheduler = start_scheduler()

    # Import the FastMCP app and run it (blocks until Ctrl-C)
    from mcp_server import mcp

    try:
        mcp.run()   # stdio transport — reads MCP JSON-RPC from stdin
    except KeyboardInterrupt:
        logger.info("Shutting down…")
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped — bye")


if __name__ == "__main__":
    main()
