"""
Retrieval Engine
Decides when to search the archive and ranks results.

Step 1 — Keyword triggers (fast, no LLM call):
  • Time/context reference phrases
  • Any known tag appearing in the message

Step 2 — Semantic search (sentence-transformers, local):
  • Embed the query
  • Cosine similarity against all archive embeddings
  • Rank by combined score = 0.7 × similarity + 0.3 × importance
"""

import logging
import re
from typing import List, Optional, Tuple

import numpy as np

from .archive import ArchiveDB
from .models import MemoryEntry

logger = logging.getLogger(__name__)


# ── Keyword trigger lists ─────────────────────────────────────────────────────

# English time / context references
_TIME_REFS_EN = [
    "last time", "remember when", "we discussed", "you mentioned",
    "earlier", "previously", "last week", "yesterday", "you said",
    "we talked", "i told you", "as i said", "like before",
    "same as before", "like last", "again like",
]

# Arabic time / context references
_TIME_REFS_AR = [
    "من قبل", "تذكر", "ذكرت", "قلت", "سبق",
    "المرة الماضية", "كما قلت", "كنا نتحدث", "ذكرنا",
    "كما ذكرت", "تحدثنا", "أخبرتك",
]

TIME_REFS = _TIME_REFS_EN + _TIME_REFS_AR


# ── Scoring ───────────────────────────────────────────────────────────────────

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _combined_score(
    similarity: float,
    importance: float,
    w_sim: float = 0.7,
    w_imp: float = 0.3,
) -> float:
    return w_sim * similarity + w_imp * importance


# ── Engine ────────────────────────────────────────────────────────────────────

class RetrievalEngine:
    def __init__(self, archive: ArchiveDB, embedder=None):
        self.archive  = archive
        self.embedder = embedder  # sentence_transformers.SentenceTransformer or None

    # ── Step 1: keyword triggers ──────────────────────────────────────────────

    def should_retrieve(self, message: str) -> bool:
        """
        Fast decision — no embedding needed.
        Returns True if the archive should be searched.
        """
        msg_lower = message.lower()

        # Time / context reference phrases
        if any(ref in msg_lower for ref in TIME_REFS):
            logger.debug("Retrieval triggered by time reference keyword")
            return True

        # Any known tag word appears in the message
        known_tags = self.archive.get_all_tags()
        if known_tags:
            words = {w.lower() for w in re.findall(r"\b\w+\b", msg_lower)}
            if known_tags & words:
                logger.debug("Retrieval triggered by matching tag in message")
                return True

        return False

    # ── Step 2: semantic search ───────────────────────────────────────────────

    def retrieve(
        self,
        message: str,
        top_k: int = 5,
        threshold: float = 0.30,
    ) -> List[MemoryEntry]:
        """
        Embed the message, compute similarity against all archive entries,
        rank by combined score, return top_k above threshold.
        """
        if self.embedder is None:
            logger.warning("No embedder loaded — returning empty results")
            return []

        query_emb = self.embedder.encode(message, show_progress_bar=False)
        all_entries = self.archive.get_all_with_embeddings()

        if not all_entries:
            return []

        scored: List[Tuple[float, MemoryEntry]] = []
        for entry, emb in all_entries:
            if emb is None:
                continue
            sim   = _cosine_similarity(query_emb, emb)
            score = _combined_score(sim, entry.importance_score)
            if score >= threshold:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [entry for _, entry in scored[:top_k]]

        # Update access statistics for returned memories
        for entry in results:
            self.archive.mark_accessed(entry.id)

        logger.debug(f"Retrieved {len(results)} memories (from {len(all_entries)} total)")
        return results

    # ── Combined entry point ──────────────────────────────────────────────────

    def get_context_memories(
        self,
        message: str,
        top_k: int = 5,
        threshold: float = 0.30,
    ) -> Tuple[List[MemoryEntry], bool]:
        """
        Main method called by the MCP server.
        Returns (memories, retrieval_was_triggered).
        """
        triggered = self.should_retrieve(message)
        if not triggered:
            return [], False
        memories = self.retrieve(message, top_k=top_k, threshold=threshold)
        return memories, True
