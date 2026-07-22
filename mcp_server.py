"""
Human Memory System — MCP Server (v2)

Model-agnostic: any AI that speaks the MCP protocol connects to this server.
Exposes three tools:
  • get_context            — call BEFORE generating a response
  • store_memory           — call AFTER generating a response
  • update_warm_attribute  — explicit upsert of a stable personal fact (v2)

v2 changes vs v1.1:
  • get_context now includes a warm_attributes block in its response,
    populated when semantically relevant warm attributes exist.
  • update_warm_attribute is a new tool for explicit Warm Layer management.

get_context is READ-ONLY: it never writes to the Archive or Warm Layer.
An earlier opportunistic auto-store side effect on get_context was removed
(see docs/decisions/ADR-009-remove-opportunistic-auto-store-from-get-context.md) —
it caused exploratory/test queries to be stored verbatim as if they were
memories, polluting semantic retrieval. All writes now happen only through
explicit calls to store_memory / update_warm_attribute.

KNOWN LIMITATION (see PROJECT_STATUS.md §3.1 and §4.1):
MCP is a client-driven protocol — this server cannot force an MCP client
(e.g. Claude Desktop/Code) to call these tools on every turn. That
guarantee is only available to integrations using memory/gateway.py
directly (custom OpenAI/Gemini/Claude-API wrappers — see README.md).

Run directly:
    python mcp_server.py
Or via main.py (includes v2 startup catch-up + background scheduler):
    python main.py
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional

from mcp.server.fastmcp import FastMCP

from config import (
    ARCHIVE_DB_PATH,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    FAST_LAYER_PATH,
    RETRIEVAL_SIM_THRESHOLD,
    TOP_K_RESULTS,
    WARM_LAYER_SIM_THRESHOLD,
    WARM_LAYER_TOP_K,
)
from memory import auto_extract
from memory.archive import ArchiveDB
from memory.fast_layer import FastLayerManager
from memory.models import LayeredContext, MemoryEntry, WarmAttribute
from memory.retrieval import RetrievalEngine
from memory.warm_layer import WarmLayerManager

logger = logging.getLogger(__name__)

# ── Component initialisation ──────────────────────────────────────────────────

fast_layer_mgr   = FastLayerManager(FAST_LAYER_PATH)
archive          = ArchiveDB(ARCHIVE_DB_PATH, EMBEDDING_DIM)
retrieval_engine = RetrievalEngine(archive, embedder=None)
warm_layer_mgr   = WarmLayerManager(ARCHIVE_DB_PATH, EMBEDDING_DIM)   # v2

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


# ── Startup warm-up (server lifespan) ─────────────────────────────────────────


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """
    Runs once at server startup, BEFORE any JSON-RPC request is processed.

    The embedding model (sentence-transformers + torch) takes ~10s+ to import
    and load even when cached. If that happened lazily on the first tool call
    (via _get_embedder()), the first call would exceed the MCP client's
    tool-call timeout and appear to hang — while a retry, finding the model
    already loaded, would succeed. Loading it here shifts that one-time cost to
    server startup (before the first request is processed) so the first real
    tool call is fast.
    """
    logger.info("Warming up embedding model…")
    started = time.perf_counter()
    embedder = _get_embedder()
    # One dummy encode so any first-call lazy init inside the model is paid now.
    embedder.encode("warm up", show_progress_bar=False)
    logger.info(
        f"Embedding model ready in {time.perf_counter() - started:.1f}s — "
        "server accepting requests"
    )
    yield


# ── FastMCP server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="Human Memory System",
    lifespan=lifespan,
    instructions=(
        "You have access to a local memory system with three layers. "
        "Call get_context with EVERY user message BEFORE generating your response — "
        "do this even if the message seems unrelated to memory, since it surfaces "
        "the user's core identity (fast_layer) and any relevant personal attributes "
        "(warm_attributes) and past memories (retrieved_memories). "
        "get_context is READ-ONLY — it never stores anything, no matter what the "
        "message contains. "
        "You must explicitly call store_memory AFTER your response whenever you "
        "learn something worth remembering (facts, preferences, decisions, ongoing "
        "projects) — nothing is remembered unless you call store_memory yourself. "
        "Call update_warm_attribute whenever you learn a stable biographical fact "
        "such as location, occupation, birthdate, or a recurring habit — "
        "this replaces the previous value rather than adding a duplicate. "
        "The fast_layer block in get_context always contains the user's core identity. "
        "The warm_attributes block contains stable personal facts relevant to this message. "
        "The retrieved_memories block contains relevant past context (if any)."
    ),
)


# ── Tool: get_context ─────────────────────────────────────────────────────────

@mcp.tool()
def get_context(message: str) -> str:
    """
    Build a layered memory context for an incoming user message.

    READ-ONLY: this tool never writes to the Archive or Warm Layer, no matter
    what the message contains. Call store_memory / update_warm_attribute
    explicitly afterward for anything worth remembering.

    Always returns:
      fast_layer         — user's core identity (always present)
      warm_attributes    — stable biographical/preference facts relevant to
                           this message (v2, may be empty)
      retrieved_memories — relevant past memories from the archive (may be empty)
      retrieval_triggered         — whether relevant memories were found
                                    (v2.4: the archive is searched semantically
                                    on EVERY call; this no longer signals
                                    whether a search happened)
      warm_retrieval_triggered    — whether any warm attributes were found (v2)

    Call this BEFORE generating your response.
    """
    embedder = _get_embedder()
    retrieval_engine.embedder = embedder

    fast_layer = fast_layer_mgr.load()

    # Embed once, reuse for both Warm Layer and Archive
    query_emb = embedder.encode(message, show_progress_bar=False)

    # Warm Layer retrieval (v2)
    warm_attrs = warm_layer_mgr.retrieve_relevant(
        message,
        query_embedding=query_emb,
        top_k=WARM_LAYER_TOP_K,
        threshold=WARM_LAYER_SIM_THRESHOLD,
    )

    # Archive retrieval — semantic search on every message (v2.4, ADR-011)
    memories, triggered = retrieval_engine.get_context_memories(
        message,
        top_k=TOP_K_RESULTS,
        threshold=RETRIEVAL_SIM_THRESHOLD,
    )

    context = LayeredContext(
        fast_layer=fast_layer,
        retrieved_memories=memories,
        retrieval_triggered=triggered,
        warm_attributes=warm_attrs,
        warm_retrieval_triggered=bool(warm_attrs),
    )

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
    For stable biographical facts (location, job, etc.), prefer update_warm_attribute
    instead — it replaces the previous value rather than adding a duplicate.

    Args:
        content          — The text to remember.
        source           — 'user', 'assistant_speech', or 'assistant_thought'.
        importance       — Initial importance 0.0–1.0. If omitted, auto-extraction
                           estimates it from the content (rule-based).
        tags             — Keywords for future retrieval; a memory whose tag
                           appears in a later message gets a similarity boost,
                           so descriptive tags in BOTH the user's languages
                           (e.g. ["food", "طعام"]) retrieve best.
                           If omitted, auto-extracted.
        emotional_weight — Significance 0.0–1.0. Set to 1.0 for memories that
                           must NEVER be deleted (e.g. major life events).
                           If omitted, auto-extraction checks for life-event phrasing.

    Returns a JSON object: { "stored": true, "id": "<uuid>" }
    """
    if not content.strip():
        return json.dumps({"error": "content cannot be empty"})

    # v1.1: fall back to rule-based extraction for any field the caller
    # didn't explicitly provide, instead of a flat hardcoded default.
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


# ── Tool: update_warm_attribute (v2) ─────────────────────────────────────────

@mcp.tool()
def update_warm_attribute(
    key: str,
    value: str,
    context_hint: Optional[str] = None,
    importance: Optional[float] = None,
) -> str:
    """
    Explicitly set or update a Warm Layer attribute.

    Use this for STABLE PERSONAL FACTS that should be remembered persistently
    and retrieved quickly when relevant — such as:
      • location       — "I live in Dubai"
      • occupation     — "I work as a software engineer at Acme"
      • birthdate      — "My birthday is March 15th"
      • recurring_habit — "I go to the gym every Monday and Thursday"
      • education      — "I studied Computer Science at AUB"
      • language_preference — "I prefer responding in Arabic"

    Unlike store_memory (which APPENDS a new entry), this REPLACES the
    previous value for the same key — so you won't end up with both
    "I live in Dubai" and "I live in London" at the same time.

    Args:
        key          — Semantic category key (e.g. "location", "occupation").
                       Use the examples above or any consistent snake_case key.
        value        — The full text of the fact as stated by the user.
        context_hint — Optional: when should this attribute be surfaced?
                       If omitted, a hint is auto-generated from the key.
        importance   — 0.0–1.0. If omitted, defaults to 0.6 (warm attributes
                       are generally more important than average archive entries).

    Returns a JSON object: { "upserted": true, "key": "<key>" }
    """
    if not key.strip() or not value.strip():
        return json.dumps({"error": "key and value cannot be empty"})

    # Auto-generate context_hint if not provided
    if not context_hint:
        _HINT_MAP = {
            "location":            "when discussing location, travel, or geography",
            "occupation":          "when discussing work, career, or professional topics",
            "birthdate":           "when discussing age, birthday, or time-sensitive info",
            "education":           "when discussing education, studies, or academic background",
            "recurring_habit":     "when discussing routines, schedules, or recurring activities",
            "language_preference": "when discussing language or communication preferences",
        }
        context_hint = _HINT_MAP.get(
            key.lower(),
            f"when discussing {key.replace('_', ' ')}",
        )

    if importance is None:
        importance = 0.6  # warm attributes are generally important

    importance = max(0.0, min(1.0, importance))

    embedder  = _get_embedder()
    embedding = embedder.encode(value, show_progress_bar=False)

    attr = WarmAttribute(
        key=key.lower().strip(),
        value=value.strip(),
        context_hint=context_hint,
        importance=importance,
    )
    warm_layer_mgr.upsert(attr, embedding=embedding)

    logger.debug(f"MCP explicit warm upsert: key='{attr.key}'")

    return json.dumps({"upserted": True, "key": attr.key}, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mcp.run()   # defaults to stdio transport
