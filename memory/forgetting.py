"""
Forgetting System
Runs weekly as a background job.

Steps:
  1. Recompute importance scores for every entry.
  2. Delete entries below LOW threshold after N days.
  3. Compress entries below MID threshold after N days (truncation in v1;
     LLM summarisation planned for v2).

Entries with emotional_weight == 1.0 are NEVER deleted.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from .archive import ArchiveDB
from .models import MemoryEntry

logger = logging.getLogger(__name__)

# Imported from config at call-time to stay flexible
_DEFAULTS = {
    "weight_frequency":      0.4,
    "weight_recency":        0.3,
    "weight_emotional":      0.3,
    "recency_half_life":     60,    # days
    "low_score":             0.2,
    "low_days":              30,
    "mid_score":             0.4,
    "mid_days":              90,
    "compress_max_chars":    300,   # v1 compression: plain truncation
}


# ── Score helpers ─────────────────────────────────────────────────────────────

def _recency_score(
    last_accessed: Optional[datetime],
    timestamp: Optional[datetime],
    half_life_days: int = 60,
) -> float:
    """Exponential decay from the most recent touch."""
    reference = last_accessed or timestamp
    if reference is None:
        return 0.0
    days_since = (datetime.now(timezone.utc) - reference).days
    return math.exp(-days_since / half_life_days)


def _frequency_score(access_count: int, max_count: int) -> float:
    """Normalised access frequency relative to the most-accessed entry."""
    if max_count <= 0:
        return 0.0
    return min(1.0, access_count / max_count)


def recompute_importance(
    entry: MemoryEntry,
    max_access_count: int,
    cfg: Optional[dict] = None,
) -> float:
    cfg = cfg or _DEFAULTS
    freq = _frequency_score(entry.access_count, max_access_count)
    rec  = _recency_score(
        entry.last_accessed, entry.timestamp, cfg["recency_half_life"]
    )
    return (
        cfg["weight_frequency"] * freq
        + cfg["weight_recency"]  * rec
        + cfg["weight_emotional"] * entry.emotional_weight
    )


# ── Main job ──────────────────────────────────────────────────────────────────

def run_forgetting_cycle(
    archive: ArchiveDB,
    embedder=None,
    cfg: Optional[dict] = None,
) -> dict:
    """
    Full forgetting pass. Returns a summary dict.
    Pass `embedder` if you want compressed entries to be re-embedded.
    """
    cfg = cfg or _DEFAULTS
    now = datetime.now(timezone.utc)

    logger.info("Forgetting cycle started")
    entries = archive.get_all_for_forgetting()

    if not entries:
        logger.info("No memories to process — done")
        return {"updated": 0, "deleted": 0, "compressed": 0}

    max_access = max((e.access_count for e in entries), default=1) or 1
    stats = {"updated": 0, "deleted": 0, "compressed": 0}

    for entry in entries:

        # ── Protected memories ────────────────────────────────────────────────
        if entry.emotional_weight >= 1.0:
            continue

        # ── Recompute importance ──────────────────────────────────────────────
        freq = _frequency_score(entry.access_count, max_access)
        rec  = _recency_score(
            entry.last_accessed, entry.timestamp, cfg["recency_half_life"]
        )
        new_score = (
            cfg["weight_frequency"] * freq
            + cfg["weight_recency"]  * rec
            + cfg["weight_emotional"] * entry.emotional_weight
        )
        archive.update_scores(entry.id, freq, rec, new_score)
        stats["updated"] += 1

        # ── Age of entry ──────────────────────────────────────────────────────
        ref_date = entry.last_accessed or entry.timestamp
        if ref_date is None:
            continue
        age_days = (now - ref_date).days

        # ── Rule 1: Delete ────────────────────────────────────────────────────
        if new_score < cfg["low_score"] and age_days >= cfg["low_days"]:
            archive.delete(entry.id)
            stats["deleted"] += 1
            logger.debug(
                f"Deleted {entry.id[:8]}… "
                f"(score={new_score:.3f}, age={age_days}d)"
            )
            continue

        # ── Rule 2: Compress ──────────────────────────────────────────────────
        if (
            new_score < cfg["mid_score"]
            and age_days >= cfg["mid_days"]
            and entry.summary is None         # already compressed → skip
        ):
            max_chars = cfg["compress_max_chars"]
            summary   = (
                entry.content[:max_chars] + "…"
                if len(entry.content) > max_chars
                else entry.content
            )
            archive.compress(entry.id, summary)

            if embedder is not None:
                emb = embedder.encode(summary, show_progress_bar=False)
                archive.update_embedding(entry.id, emb)

            stats["compressed"] += 1
            logger.debug(
                f"Compressed {entry.id[:8]}… "
                f"(score={new_score:.3f}, age={age_days}d)"
            )

    logger.info(
        f"Forgetting cycle done — "
        f"updated={stats['updated']}, "
        f"deleted={stats['deleted']}, "
        f"compressed={stats['compressed']}"
    )
    return stats
