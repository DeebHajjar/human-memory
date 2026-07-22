"""
Memory Gateway — v2

v2 changes vs v1.1:
  • build_context() now also queries the Warm Layer and includes any
    relevant warm attributes in the returned LayeredContext.
  • auto_store_turn() now detects Warm Layer candidates via extract_warm()
    and routes them to WarmLayerManager.upsert() instead of (or in addition
    to) ArchiveDB.store(). A text that is a warm attribute is also stored in
    the Archive for long-term history — upsert only affects the "current
    value" shortcut in the Warm Layer.
  • build_system_prompt_block() renders the new warm_attributes block.

All v1.1 guarantees are preserved:
  • build_context() is called unconditionally before every model call.
  • auto_store_turn() is called unconditionally after every model call.
  • No LLM call is involved in any decision here.

Important scope note (see PROJECT_STATUS.md §3.1 and §4.1):
This guarantee only holds for integrations that call this Gateway directly
— i.e. custom wrappers around the OpenAI/Gemini/Claude API, as documented
in README.md. When Claude Desktop/Code (or any MCP-native client) is used,
the client — not this code — decides when to call `get_context` /
`store_memory` as MCP tools. MCP's protocol does not allow a server to
force a client to call a tool. For that scenario, the MCP tools remain
best-effort, and the model's system instructions are the only lever we have.
"""

import logging
from typing import List, Optional

import numpy as np

from config import RETRIEVAL_SIM_THRESHOLD
from memory import auto_extract
from memory.archive import ArchiveDB
from memory.fast_layer import FastLayerManager
from memory.models import LayeredContext, MemoryEntry, WarmAttribute
from memory.retrieval import RetrievalEngine
from memory.warm_layer import WarmLayerManager

logger = logging.getLogger(__name__)


class MemoryGateway:
    """
    Deterministic wrapper around the memory system for direct API
    integrations (OpenAI, Gemini, custom Claude API usage). Call
    build_context() before every model call and auto_store_turn() after
    every model call — no tool-call decision required from the model.
    """

    def __init__(
        self,
        fast_layer_mgr: FastLayerManager,
        archive: ArchiveDB,
        retrieval_engine: RetrievalEngine,
        warm_layer_mgr: WarmLayerManager,
        embedder=None,
    ):
        self.fast_layer_mgr  = fast_layer_mgr
        self.archive         = archive
        self.retrieval       = retrieval_engine
        self.warm_layer      = warm_layer_mgr
        self.embedder        = embedder

    # ── Embedding helper ─────────────────────────────────────────────────────

    def _embed(self, text: str) -> Optional[np.ndarray]:
        if self.embedder is None:
            return None
        return self.embedder.encode(text, show_progress_bar=False)

    # ── Before the model runs ────────────────────────────────────────────────

    def build_context(
        self,
        message: str,
        top_k: int = 5,
        threshold: float = RETRIEVAL_SIM_THRESHOLD,
    ) -> LayeredContext:
        """
        Always returns the Fast Layer. Always runs the Warm Layer retrieval.
        Always runs Archive semantic retrieval (v2.4, ADR-011 — the keyword
        trigger gate was removed; `retrieval_triggered` in the returned
        context now means "relevant memories found").

        This is called unconditionally — never gated on a tool-call decision
        from the model.
        """
        fast_layer = self.fast_layer_mgr.load()

        # Embed once, reuse for both Warm Layer and Archive retrieval
        query_emb = self._embed(message)

        # Warm Layer: always attempted (lightweight — tens of rows at most)
        warm_attrs = self.warm_layer.retrieve_relevant(message, query_embedding=query_emb)

        # Archive: semantic search on every message
        memories, triggered = self.retrieval.get_context_memories(
            message, top_k=top_k, threshold=threshold
        )

        return LayeredContext(
            fast_layer=fast_layer,
            retrieved_memories=memories,
            retrieval_triggered=triggered,
            warm_attributes=warm_attrs,
            warm_retrieval_triggered=bool(warm_attrs),
        )

    def build_system_prompt_block(self, context: LayeredContext) -> str:
        """
        Convenience helper: renders the LayeredContext as a text block
        ready to prepend/append to a system prompt.
        """
        fl = context.fast_layer
        lines = ["[Memory Context]"]

        lines.append(
            f"User identity: name={fl.name or 'unknown'}, language={fl.language}"
        )
        if fl.personality_traits:
            lines.append(f"Personality traits: {', '.join(fl.personality_traits)}")
        if fl.key_preferences:
            lines.append(f"Preferences: {', '.join(fl.key_preferences)}")
        if fl.values:
            lines.append(f"Values: {', '.join(fl.values)}")

        # v2: Warm Layer block
        if context.warm_attributes:
            lines.append("\nContextually relevant personal attributes:")
            for attr in context.warm_attributes:
                lines.append(f"  [{attr.key}] {attr.value}")

        if context.retrieval_triggered and context.retrieved_memories:
            lines.append("\nRelevant past memories:")
            for m in context.retrieved_memories:
                lines.append(f"- {m.content}")
        else:
            lines.append("\n(No relevant past memories retrieved for this message.)")

        return "\n".join(lines)

    # ── After the model runs ─────────────────────────────────────────────────

    def auto_store_turn(
        self,
        user_message: str,
        assistant_message: Optional[str] = None,
    ) -> List[str]:
        """
        Always called after a turn completes. Runs rule-based extraction on
        both the user's message and the assistant's reply.

        For each piece of text:
          1. extract_warm() — if it's a Warm Layer attribute, upsert it.
          2. extract()      — if it clears the bar, store it in the Archive.

        Both can fire for the same text (warm attributes also go to the Archive
        for long-term history, so the "current value" shortcut doesn't erase
        the fact that the user mentioned it multiple times).

        Returns the list of Archive memory ids that were stored (may be empty).
        """
        stored_ids: List[str] = []

        for text, source in (
            (user_message, "user"),
            (assistant_message, "assistant_speech"),
        ):
            if not text:
                continue

            # ── Warm Layer routing ────────────────────────────────────────────
            warm_candidate = auto_extract.extract_warm(text)
            if warm_candidate is not None:
                embedding = self._embed(warm_candidate.value)
                attr = WarmAttribute(
                    key=warm_candidate.key,
                    value=warm_candidate.value,
                    context_hint=warm_candidate.context_hint,
                    importance=warm_candidate.importance,
                )
                self.warm_layer.upsert(attr, embedding=embedding)
                logger.debug(
                    f"Auto-upserted warm attribute [{warm_candidate.key}] source={source}"
                )

            # ── Archive routing ───────────────────────────────────────────────
            fact = auto_extract.extract(text, source=source)
            if fact is None:
                continue  # too trivial to store — expected, not an error

            emb = self._embed(fact.content)
            emb_bytes: Optional[bytes] = None
            if emb is not None:
                import struct
                emb_bytes = struct.pack(f"{len(emb)}f", *emb.tolist())

            entry = MemoryEntry(
                content=fact.content,
                source=fact.source,
                importance_score=fact.importance,
                emotional_weight=fact.emotional_weight,
                tags=fact.tags,
            )
            memory_id = self.archive.store(entry, embedding=emb)
            stored_ids.append(memory_id)
            logger.debug(
                f"Auto-stored [{source}] importance={fact.importance:.2f} "
                f"emotional={fact.emotional_weight:.1f} id={memory_id[:8]}…"
            )

        return stored_ids

    # ── Full turn convenience wrapper ────────────────────────────────────────

    def process_turn(
        self,
        user_message: str,
        call_model_fn,
        top_k: int = 5,
        threshold: float = RETRIEVAL_SIM_THRESHOLD,
    ) -> str:
        """
        Full deterministic loop for a single turn:
          1. build_context()          — always (Fast + Warm + Archive semantic search)
          2. call_model_fn(context)   — caller-supplied function that calls the
                                        LLM and returns its text response
          3. auto_store_turn()        — always (Warm upsert + Archive store)

        `call_model_fn` receives the rendered system-prompt block (str) and
        the raw user_message (str), and must return the assistant's reply (str).

        This is the recommended integration point for any non-MCP-native usage
        (see README.md "Any LLM (generic HTTP pattern)" section).
        """
        context = self.build_context(user_message, top_k=top_k, threshold=threshold)
        memory_block = self.build_system_prompt_block(context)

        assistant_reply = call_model_fn(memory_block, user_message)

        self.auto_store_turn(user_message, assistant_reply)

        return assistant_reply
