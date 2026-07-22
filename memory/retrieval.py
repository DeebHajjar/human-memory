"""
Retrieval Engine
Semantic search over the Archive, run on EVERY message (v2.4 — ADR-011).

The pre-v2.4 keyword trigger gate (time-reference phrases / stored-tag words
deciding WHETHER to search) was removed: the query embedding is computed
unconditionally anyway for the Warm Layer, and the gate silently dropped
high-confidence semantic matches whenever the message didn't happen to
contain a stored tag word (experiments.md E17).

Scoring per entry:
  sim = cosine(query, content embedding)
      + RETRIEVAL_TAG_BOOST if one of the entry's own tags appears
        as a word in the message (keyword evidence, not a gate)
  include only if sim >= threshold (raw similarity — importance never
  decides inclusion), then rank passers by
  0.7 × sim + 0.3 × importance, capped at top_k.
"""

import logging
import re
from typing import List, Tuple

import numpy as np

from config import RETRIEVAL_SIM_THRESHOLD, RETRIEVAL_TAG_BOOST

from .archive import ArchiveDB
from .models import MemoryEntry

logger = logging.getLogger(__name__)


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

    # ── Semantic search ───────────────────────────────────────────────────────

    def retrieve(
        self,
        message: str,
        top_k: int = 5,
        threshold: float = RETRIEVAL_SIM_THRESHOLD,
    ) -> List[MemoryEntry]:
        """
        Embed the message, score every archive entry by raw cosine similarity
        (plus a tag boost when one of the entry's own tags appears in the
        message), keep entries with sim >= threshold, rank by combined score
        (0.7 × sim + 0.3 × importance), return top_k.
        """
        if self.embedder is None:
            logger.warning("No embedder loaded — returning empty results")
            return []

        query_emb = self.embedder.encode(message, show_progress_bar=False)
        all_entries = self.archive.get_all_with_embeddings()

        if not all_entries:
            return []

        msg_words = {w.lower() for w in re.findall(r"\b\w+\b", message.lower())}

        scored: List[Tuple[float, MemoryEntry]] = []
        for entry, emb in all_entries:
            if emb is None:
                continue
            sim = _cosine_similarity(query_emb, emb)
            if any(t.lower() in msg_words for t in entry.tags):
                sim += RETRIEVAL_TAG_BOOST
            if sim >= threshold:
                scored.append((_combined_score(sim, entry.importance_score), entry))

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
        threshold: float = RETRIEVAL_SIM_THRESHOLD,
    ) -> Tuple[List[MemoryEntry], bool]:
        """
        Main method called by the MCP server and the Gateway.
        Search always runs; the returned bool means "relevant memories found"
        (pre-v2.4 it meant "search was attempted at all").
        """
        memories = self.retrieve(message, top_k=top_k, threshold=threshold)
        return memories, bool(memories)
