"""
Warm Layer Manager — v2

Manages the Warm Layer: a table of stable personal biographical/preference
attributes stored in archive.db alongside the Archive memories table.

Unlike the Archive (which appends new entries), the Warm Layer uses upsert
semantics: storing a new value for an existing key replaces the old one.
This is intentional — "I moved to London" should update "location", not
create a conflicting second entry alongside the old "I live in Dubai".

Retrieval (v2.4 — see ADR-011) scores every row by raw cosine similarity:
  sim = cosine(query, value embedding) [+ WARM_LAYER_HINT_BOOST when a
        content word of context_hint appears in the message]
  include only if sim >= WARM_LAYER_SIM_THRESHOLD (importance never gates)
  rank passers by WARM_LAYER_SIM_WEIGHT × sim + WARM_LAYER_IMP_WEIGHT × importance

The hint match ignores stopwords (English + Arabic): hints are natural-language
sentences, and function words like "the"/"or"/"من" must not count as evidence —
the pre-v2.4 version auto-included any attribute whose hint shared ANY word
with the message, which false-positived on nearly every English message.

Scoring always runs over the full warm_layer table since it's expected to
stay small (< 100 rows). No FAISS index needed here.
"""

import logging
import re
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from config import (
    EMBEDDING_DIM,
    WARM_LAYER_HINT_BOOST,
    WARM_LAYER_IMP_WEIGHT,
    WARM_LAYER_SIM_THRESHOLD,
    WARM_LAYER_SIM_WEIGHT,
    WARM_LAYER_TOP_K,
)
from memory.models import WarmAttribute

logger = logging.getLogger(__name__)


# ── Stopwords (bilingual) ─────────────────────────────────────────────────────
# Only job: stop function words in context hints from counting as keyword
# evidence. Short and pragmatic, not exhaustive.

_STOPWORDS_EN = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "or", "and",
    "when", "this", "that", "these", "those", "for", "to", "of", "in", "on",
    "at", "with", "as", "by", "it", "its", "his", "her", "their", "my", "your",
    "should", "would", "could", "will", "can", "do", "does", "did", "not",
    "user", "user's", "users", "use", "about", "asks", "ask", "any", "if",
    "what", "who", "how", "why", "where", "which",
}

_STOPWORDS_AR = {
    "من", "في", "على", "إلى", "الى", "عن", "أن", "ان", "إن", "ما", "هو", "هي",
    "شو", "يلي", "اللي", "هذا", "هذه", "ذلك", "عند", "كل", "مع", "أو", "او",
    "و", "لا", "لم", "لن", "قد", "كان", "كانت", "يكون", "التي", "الذي",
}

_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_AR


def _content_words(text: str) -> set:
    """Lowercased word tokens minus stopwords (same \\b\\w+\\b tokenizer as retrieval.py)."""
    return {w.lower() for w in re.findall(r"\b\w+\b", text)} - _STOPWORDS


# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS warm_layer (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    context_hint TEXT DEFAULT '',
    embedding    BLOB,
    importance   REAL DEFAULT 0.5,
    last_updated TEXT
);
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _emb_to_bytes(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec.tolist())


def _bytes_to_emb(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ── Manager ───────────────────────────────────────────────────────────────────

class WarmLayerManager:
    """
    CRUD + retrieval for the warm_layer table in archive.db.

    The db_path should point to the same archive.db file used by ArchiveDB —
    both live in the same SQLite file in separate tables, sharing connection
    infrastructure without coupling their logic.
    """

    def __init__(self, db_path: Path, embedding_dim: int = EMBEDDING_DIM):
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self._ensure_schema()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_TABLE)
            conn.commit()
        logger.debug("Warm layer schema ready")

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert(self, attribute: WarmAttribute, embedding: Optional[np.ndarray] = None) -> None:
        """
        Insert or replace a warm attribute. If a row with the same key
        already exists, it is fully replaced — intentional upsert semantics.
        """
        emb_blob: Optional[bytes] = None
        if embedding is not None:
            emb_blob = _emb_to_bytes(embedding)
        elif attribute.embedding is not None:
            emb_blob = attribute.embedding

        now = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO warm_layer (key, value, context_hint, embedding, importance, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value        = excluded.value,
                    context_hint = excluded.context_hint,
                    embedding    = excluded.embedding,
                    importance   = excluded.importance,
                    last_updated = excluded.last_updated
                """,
                (
                    attribute.key,
                    attribute.value,
                    attribute.context_hint,
                    emb_blob,
                    max(0.0, min(1.0, attribute.importance)),
                    now,
                ),
            )
            conn.commit()
        logger.debug(f"Warm layer upsert: key='{attribute.key}'")

    def delete(self, key: str) -> bool:
        """Delete a warm attribute by key. Returns True if a row was deleted."""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM warm_layer WHERE key = ?", (key,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_all(self) -> List[WarmAttribute]:
        """Return all warm attributes (no embeddings — for display only)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value, context_hint, importance, last_updated FROM warm_layer"
            ).fetchall()
        return [self._row_to_attr(r) for r in rows]

    def get_by_key(self, key: str) -> Optional[WarmAttribute]:
        """Return a single attribute by key, or None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT key, value, context_hint, importance, last_updated FROM warm_layer WHERE key = ?",
                (key,),
            ).fetchone()
        return self._row_to_attr(row) if row else None

    def _get_all_with_embeddings(self) -> List[Tuple[WarmAttribute, Optional[np.ndarray]]]:
        """Internal: fetch all rows including embedding BLOBs for retrieval."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value, context_hint, embedding, importance, last_updated FROM warm_layer"
            ).fetchall()
        result = []
        for row in rows:
            attr = self._row_to_attr(row)
            emb = _bytes_to_emb(row["embedding"]) if row["embedding"] else None
            result.append((attr, emb))
        return result

    @staticmethod
    def _row_to_attr(row) -> WarmAttribute:
        last_updated = None
        if row["last_updated"]:
            try:
                last_updated = datetime.fromisoformat(row["last_updated"])
            except ValueError:
                pass
        return WarmAttribute(
            key=row["key"],
            value=row["value"],
            context_hint=row["context_hint"] or "",
            importance=float(row["importance"]),
            last_updated=last_updated,
        )

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def retrieve_relevant(
        self,
        message: str,
        query_embedding: Optional[np.ndarray] = None,
        top_k: int = WARM_LAYER_TOP_K,
        threshold: float = WARM_LAYER_SIM_THRESHOLD,
    ) -> List[WarmAttribute]:
        """
        Return warm attributes relevant to `message`.

        v2.4 scoring (ADR-011):
          sim = cosine(query, value embedding)
              + WARM_LAYER_HINT_BOOST if a content word (stopwords excluded)
                of context_hint appears in the message
          include only if sim >= threshold (raw similarity — importance
          never decides inclusion), then rank passers by
          SIM_WEIGHT × sim + IMP_WEIGHT × importance, capped at top_k.
        """
        if query_embedding is None:
            return []

        all_rows = self._get_all_with_embeddings()
        if not all_rows:
            return []

        msg_words = _content_words(message)

        passed: List[Tuple[float, WarmAttribute]] = []
        for attr, emb in all_rows:
            if emb is None:
                continue
            sim = _cosine(query_embedding, emb)
            if _content_words(attr.context_hint) & msg_words:
                sim += WARM_LAYER_HINT_BOOST
            if sim >= threshold:
                rank = WARM_LAYER_SIM_WEIGHT * sim + WARM_LAYER_IMP_WEIGHT * attr.importance
                passed.append((rank, attr))

        passed.sort(key=lambda x: x[0], reverse=True)
        results = [attr for _, attr in passed[:top_k]]
        logger.debug(
            f"Warm layer retrieved {len(results)} attributes "
            f"(of {len(all_rows)} rows, sim threshold {threshold})"
        )
        return results
