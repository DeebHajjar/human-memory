"""
Human Memory System — MCP Server (v1.1)

Model-agnostic: any AI that speaks the MCP protocol connects to this server.
Exposes two tools:
  • get_context   — call BEFORE generating a response
  • store_memory  — call AFTER generating a response

KNOWN LIMITATION (see PROJECT_STATUS.md §3.1 and §4.1):
MCP is a client-driven protocol — this server cannot force an MCP client
(e.g. Claude Desktop/Code) to call these tools on every turn. That
guarantee is only available to integrations using memory/gateway.py
directly (custom OpenAI/Gemini/Claude-API wrappers — see README.md).

v1.1 mitigation for MCP-native clients: get_context now also opportunistically
auto-stores the incoming user message (rule-based, via memory/auto_extract.py)
as a side effect, since get_context is the tool most reliably called every
turn. This does NOT capture the assistant's own reply — only the Gateway
path guarantees that. store_memory also now falls back to auto-extracted
importance/tags when the calling model omits them.

Run directly:
    python mcp_server.py
Or via main.py (includes v1.1 startup catch-up + background scheduler):
    python main.py
"""

import json
import logging
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from config import (
    ARCHIVE_DB_PATH,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    FAST_LAYER_PATH,
    RETRIEVAL_SCORE_THRESHOLD,
    TOP_K_RESULTS,
)
from memory import auto_extract
from memory.archive import ArchiveDB
from memory.fast_layer import FastLayerManager
from memory.models import LayeredContext, MemoryEntry
from memory.retrieval import RetrievalEngine

logger = logging.getLogger(__name__)

# ── Component initialisation ──────────────────────────────────────────────────

fast_layer_mgr   = FastLayerManager(FAST_LAYER_PATH)
archive          = ArchiveDB(ARCHIVE_DB_PATH, EMBEDDING_DIM)
retrieval_engine = RetrievalEngine(archive, embedder=None)

_embedder = None


def _get_embedder():
    """Lazy-load the embedding model on first use."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model '{EMBEDDING_MODEL}' (first use only)…")
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
        retrieval_engine.embedder = _embedder
        logger.info("Embedding model ready")
    return _embedder


# ── FastMCP server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="Human Memory System",
    instructions=(
        "You have access to a local memory system. "
        "Call get_context with EVERY user message BEFORE generating your response — "
        "do this even if the message seems unrelated to memory, since it also keeps "
        "your core identity context (fast_layer) up to date. "
        "Call store_memory AFTER your response whenever you learn something worth "
        "remembering (facts, preferences, decisions, ongoing projects). "
        "The fast_layer block in get_context always contains the user's core identity. "
        "The retrieved_memories block contains relevant past context (if any)."
    ),
)


# ── Tool: get_context ─────────────────────────────────────────────────────────

@mcp.tool()
def get_context(message: str) -> str:
    """
    Build a layered memory context for an incoming user message.

    Always returns the fast layer (user's core identity).
    Searches the archive only when the message references past context —
    detected by keyword triggers (time refs, known tags). No LLM call needed.

    Call this BEFORE generating your response.

    Returns a JSON object with:
      fast_layer          — always present (core identity)
      retrieval_triggered — whether the archive was searched
      retrieved_memories  — list of relevant past memories (may be empty)
    """
    embedder = _get_embedder()
    retrieval_engine.embedder = embedder

    fast_layer = fast_layer_mgr.load()
    memories, triggered = retrieval_engine.get_context_memories(
        message,
        top_k=TOP_K_RESULTS,
        threshold=RETRIEVAL_SCORE_THRESHOLD,
    )

    context = LayeredContext(
        fast_layer=fast_layer,
        retrieved_memories=memories,
        retrieval_triggered=triggered,
    )

    # v1.1 mitigation: opportunistically auto-store the user's own message
    # here, since get_context is the tool most reliably called every turn
    # by MCP-native clients. This does not replace store_memory — it only
    # provides a safety net for the user-message side of a turn.
    try:
        fact = auto_extract.extract(message, source="user")
        if fact is not None:
            embedding = embedder.encode(fact.content, show_progress_bar=False)
            entry = MemoryEntry(
                content=fact.content,
                source=fact.source,
                importance_score=fact.importance,
                emotional_weight=fact.emotional_weight,
                tags=fact.tags,
            )
            archive.store(entry, embedding=embedding)
    except Exception as exc:
        # Never let auto-store break the primary get_context response
        logger.warning(f"Opportunistic auto-store failed (non-fatal): {exc}")

    return json.dumps(context.to_dict(), indent=2, ensure_ascii=False)


# ── Tool: store_memory ────────────────────────────────────────────────────────

@mcp.tool()
def store_memory(
    content: str,
    source: str = "user",
    importance: Optional[float] = None,
    tags: Optional[List[str]] = None,
    emotional_weight: Optional[float] = None,
) -> str:
    """
    Store a new memory in the local archive.

    Call this AFTER generating your response to preserve important information.

    Args:
        content          — The text to remember.
        source           — 'user', 'assistant_speech', or 'assistant_thought'.
        importance       — Initial importance 0.0–1.0. If omitted, v1.1
                           auto-extraction estimates it from the content
                           (rule-based — looks for identity/preference/
                           decision phrasing) instead of a flat default.
        tags             — Keywords (names, topics, project names) that improve
                           future retrieval. If omitted, auto-extracted from
                           capitalized words in the content.
        emotional_weight — Significance 0.0–1.0. Set to 1.0 for memories that
                           must NEVER be deleted (e.g. major life events).
                           If omitted, auto-extraction checks for strong
                           life-event phrasing (e.g. "got married").

    Returns a JSON object: { "stored": true, "id": "<uuid>" }
    """
    if not content.strip():
        return json.dumps({"error": "content cannot be empty"})

    # v1.1: fall back to rule-based extraction for any field the caller
    # didn't explicitly provide, instead of a flat hardcoded default.
    # This keeps scoring consistent regardless of which model is calling.
    auto_fact = auto_extract.extract(content, source=source)

    if importance is None:
        importance = auto_fact.importance if auto_fact else 0.4
    if tags is None:
        tags = auto_fact.tags if auto_fact else []
    if emotional_weight is None:
        emotional_weight = auto_fact.emotional_weight if auto_fact else 0.0

    # Clamp scores to valid range
    importance       = max(0.0, min(1.0, importance))
    emotional_weight = max(0.0, min(1.0, emotional_weight))

    embedder  = _get_embedder()
    embedding = embedder.encode(content, show_progress_bar=False)

    entry = MemoryEntry(
        content          = content,
        source           = source,
        importance_score  = importance,
        emotional_weight  = emotional_weight,
        tags             = tags,
        frequency_score  = 0.0,
        recency_score    = 1.0,
    )

    memory_id = archive.store(entry, embedding=embedding)
    logger.debug(f"Stored memory {memory_id[:8]}… source={source}")

    return json.dumps({"stored": True, "id": memory_id}, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mcp.run()   # defaults to stdio transport
