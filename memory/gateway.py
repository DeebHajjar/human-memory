"""
Memory Gateway — v1.1

Solves the core problem: in v1, both retrieval and storage only happened
if the conversational model *chose* to call get_context / store_memory.
Over a long conversation, or with a weaker model, that choice gets
skipped — and the Fast Layer (meant to be always-present) silently
disappears, and new information silently never gets saved.

The Gateway removes that choice from the model entirely for any
integration that runs through it:

  BEFORE the model runs  → build_context() is called deterministically,
                            every single turn, no exceptions.
  AFTER the model runs   → auto_store_turn() is called deterministically,
                            every single turn, using rule-based extraction
                            (memory/auto_extract.py) instead of waiting for
                            the model to decide to call store_memory.

Important scope note (see PROJECT_STATUS.md §3.1 and §4.1):
This guarantee only holds for integrations that call this Gateway directly
— i.e. custom wrappers around the OpenAI/Gemini/Claude API, as documented
in README.md. When Claude Desktop/Code (or any MCP-native client) is used,
the client — not this code — decides when to call `get_context` /
`store_memory` as MCP tools. MCP's protocol does not allow a server to
force a client to call a tool. For that scenario, the MCP tools remain
best-effort, exactly as in v1, and the model's system instructions are the
only lever we have.

The MCP server tools (get_context / store_memory) are still exposed
unchanged for MCP-native clients, and now internally delegate their
storage-decision logic to the same auto_extract engine used here for
consistency — but the *guarantee* of "always injected / always stored"
is only provided by this Gateway.
"""

import logging
from typing import List, Optional

from memory import auto_extract
from memory.archive import ArchiveDB
from memory.fast_layer import FastLayerManager
from memory.models import LayeredContext, MemoryEntry
from memory.retrieval import RetrievalEngine

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
        embedder=None,
    ):
        self.fast_layer_mgr  = fast_layer_mgr
        self.archive         = archive
        self.retrieval       = retrieval_engine
        self.embedder        = embedder

    # ── Before the model runs ────────────────────────────────────────────────

    def build_context(self, message: str, top_k: int = 5, threshold: float = 0.30) -> LayeredContext:
        """
        Always returns the Fast Layer. Always runs the retrieval trigger
        check. This is called unconditionally — never gated on a tool-call
        decision from the model.
        """
        fast_layer = self.fast_layer_mgr.load()
        memories, triggered = self.retrieval.get_context_memories(
            message, top_k=top_k, threshold=threshold
        )
        return LayeredContext(
            fast_layer=fast_layer,
            retrieved_memories=memories,
            retrieval_triggered=triggered,
        )

    def build_system_prompt_block(self, context: LayeredContext) -> str:
        """
        Convenience helper: renders the LayeredContext as a text block
        ready to prepend/append to a system prompt.
        """
        fl = context.fast_layer
        lines = ["[Memory Context]"]

        lines.append(f"User identity: name={fl.name or 'unknown'}, "
                     f"language={fl.language}")
        if fl.personality_traits:
            lines.append(f"Personality traits: {', '.join(fl.personality_traits)}")
        if fl.key_preferences:
            lines.append(f"Preferences: {', '.join(fl.key_preferences)}")
        if fl.values:
            lines.append(f"Values: {', '.join(fl.values)}")

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
        Always called after a turn completes. Runs rule-based extraction
        (memory/auto_extract.py) on both the user's message and the
        assistant's reply, and stores whatever clears the bar — without
        waiting for the model to explicitly request storage.

        Returns the list of memory ids that were stored (may be empty).
        """
        stored_ids: List[str] = []

        for text, source in (
            (user_message, "user"),
            (assistant_message, "assistant_speech"),
        ):
            if not text:
                continue

            fact = auto_extract.extract(text, source=source)
            if fact is None:
                continue  # too trivial to store — this is expected, not an error

            embedding = None
            if self.embedder is not None:
                embedding = self.embedder.encode(fact.content, show_progress_bar=False)

            entry = MemoryEntry(
                content=fact.content,
                source=fact.source,
                importance_score=fact.importance,
                emotional_weight=fact.emotional_weight,
                tags=fact.tags,
            )
            memory_id = self.archive.store(entry, embedding=embedding)
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
        threshold: float = 0.30,
    ) -> str:
        """
        Full deterministic loop for a single turn:
          1. build_context()          — always
          2. call_model_fn(context)   — caller-supplied function that
                                         actually calls the LLM and
                                         returns its text response
          3. auto_store_turn()        — always

        `call_model_fn` receives the rendered system-prompt block (str)
        and the raw user_message (str), and must return the assistant's
        reply as a str.

        This is the recommended integration point for any non-MCP-native
        usage (see README.md "Any LLM (generic HTTP pattern)" section).
        """
        context = self.build_context(user_message, top_k=top_k, threshold=threshold)
        memory_block = self.build_system_prompt_block(context)

        assistant_reply = call_model_fn(memory_block, user_message)

        self.auto_store_turn(user_message, assistant_reply)

        return assistant_reply
