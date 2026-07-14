"""
Archive DB — Layer 4
SQLite database with float32 embedding BLOBs.
All heavy lifting (similarity search) happens in memory/retrieval.py.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import numpy as np

from .models import MemoryEntry

logger = logging.getLogger(__name__)


class ArchiveDB:
    def __init__(self, db_path: Path, embedding_dim: int = 384):
        self.db_path    = db_path
        self.embedding_dim = embedding_dim
        self._init_db()

    # ── Connection helper ─────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id              TEXT    PRIMARY KEY,
                    content         TEXT    NOT NULL,
                    summary         TEXT,
                    embedding       BLOB,
                    importance_score REAL   DEFAULT 0.5,
                    frequency_score REAL    DEFAULT 0.0,
                    recency_score   REAL    DEFAULT 1.0,
                    emotional_weight REAL   DEFAULT 0.0,
                    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_accessed   DATETIME,
                    access_count    INTEGER  DEFAULT 0,
                    tags            TEXT     DEFAULT '[]',
                    source          TEXT     DEFAULT 'user'
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_importance  ON memories(importance_score)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp   ON memories(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotional   ON memories(emotional_weight)"
            )
            # v1.1: tracks when maintenance jobs (like the forgetting cycle)
            # last ran, so a short-lived process can catch up on startup
            # instead of relying on staying alive for a full week.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    # ── System state (v1.1) ───────────────────────────────────────────────────

    def get_state(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO system_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    # ── Write operations ──────────────────────────────────────────────────────

    def store(self, entry: MemoryEntry, embedding: Optional[np.ndarray] = None) -> str:
        """Insert a new memory. Returns the memory id."""
        emb_bytes = (
            embedding.astype(np.float32).tobytes() if embedding is not None else None
        )
        ts = (
            entry.timestamp.isoformat()
            if entry.timestamp
            else datetime.now(timezone.utc).isoformat()
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO memories
                    (id, content, summary, embedding,
                     importance_score, frequency_score, recency_score, emotional_weight,
                     timestamp, last_accessed, access_count, tags, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    entry.id,
                    entry.content,
                    entry.summary,
                    emb_bytes,
                    entry.importance_score,
                    entry.frequency_score,
                    entry.recency_score,
                    entry.emotional_weight,
                    ts,
                    entry.last_accessed.isoformat() if entry.last_accessed else None,
                    entry.access_count,
                    json.dumps(entry.tags, ensure_ascii=False),
                    entry.source,
                ),
            )
        logger.debug(f"Stored memory {entry.id}")
        return entry.id

    def mark_accessed(self, memory_id: str) -> None:
        """Increment access counter and update last_accessed timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE memories
                SET last_accessed = ?, access_count = access_count + 1
                WHERE id = ?
                """,
                (now, memory_id),
            )

    def update_importance(self, memory_id: str, delta: float) -> None:
        """Add delta to importance_score, clamped to [0, 1]."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE memories
                SET importance_score = MAX(0.0, MIN(1.0, importance_score + ?))
                WHERE id = ?
                """,
                (delta, memory_id),
            )

    def update_scores(
        self,
        memory_id: str,
        frequency_score: float,
        recency_score: float,
        importance_score: float,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE memories
                SET frequency_score = ?, recency_score = ?, importance_score = ?
                WHERE id = ?
                """,
                (frequency_score, recency_score, importance_score, memory_id),
            )

    def compress(self, memory_id: str, summary: str) -> None:
        """Replace content with summary (compression step of forgetting system)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE memories SET content = ?, summary = ?, embedding = NULL WHERE id = ?",
                (summary, summary, memory_id),
            )

    def update_embedding(self, memory_id: str, embedding: np.ndarray) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                (embedding.astype(np.float32).tobytes(), memory_id),
            )

    def delete(self, memory_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    # ── Read operations ───────────────────────────────────────────────────────

    def get_all_with_embeddings(self) -> List[Tuple[MemoryEntry, Optional[np.ndarray]]]:
        """Load every entry + its embedding for similarity search."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, content, summary, embedding, importance_score,
                       frequency_score, recency_score, emotional_weight,
                       timestamp, last_accessed, access_count, tags, source
                FROM memories
                """
            ).fetchall()

        result: List[Tuple[MemoryEntry, Optional[np.ndarray]]] = []
        for row in rows:
            entry = self._row_to_entry(row)
            emb: Optional[np.ndarray] = None
            if row["embedding"]:
                emb = np.frombuffer(row["embedding"], dtype=np.float32).copy()
            result.append((entry, emb))
        return result

    def get_all_for_forgetting(self) -> List[MemoryEntry]:
        """Load every entry (no embeddings needed for forgetting cycle)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, content, summary, embedding, importance_score,
                       frequency_score, recency_score, emotional_weight,
                       timestamp, last_accessed, access_count, tags, source
                FROM memories
                """
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_all_tags(self) -> set:
        """Return a flat set of all tags across all entries (used for keyword trigger)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT tags FROM memories").fetchall()
        tags: set = set()
        for row in rows:
            tags.update(json.loads(row["tags"] or "[]"))
        return {t.lower() for t in tags}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        def _dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return MemoryEntry(
            id               = row["id"],
            content          = row["content"],
            summary          = row["summary"],
            importance_score = row["importance_score"],
            frequency_score  = row["frequency_score"],
            recency_score    = row["recency_score"],
            emotional_weight = row["emotional_weight"],
            timestamp        = _dt(row["timestamp"]),
            last_accessed    = _dt(row["last_accessed"]),
            access_count     = row["access_count"] or 0,
            tags             = json.loads(row["tags"] or "[]"),
            source           = row["source"] or "user",
        )
