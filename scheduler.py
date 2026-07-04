"""
Background Scheduler — v1.1

Problem (v1): MCP servers are usually short-lived — the client (e.g. Claude
Desktop) starts the process per session and stops it afterward. A weekly
in-process timer often never fires because the process never stays alive
that long.

Fix (v1.1): on every startup, check how long it has been since the last
forgetting cycle (stored in the archive's system_state table). If overdue,
run it immediately ("catch-up"). The live weekly timer is kept as a
secondary mechanism for the (less common) case where the process does stay
running for a long time.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    ARCHIVE_DB_PATH,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    FORGETTING_CYCLE_WEEKS,
    FORGET_LOW_SCORE,
    FORGET_LOW_DAYS,
    FORGET_MID_SCORE,
    FORGET_MID_DAYS,
    RECENCY_HALF_LIFE_DAYS,
    WEIGHT_EMOTIONAL,
    WEIGHT_FREQUENCY,
    WEIGHT_RECENCY,
)
from memory.archive import ArchiveDB
from memory.forgetting import run_forgetting_cycle

logger = logging.getLogger(__name__)

_STATE_KEY = "last_forgetting_run"

_forgetting_cfg = {
    "weight_frequency":  WEIGHT_FREQUENCY,
    "weight_recency":    WEIGHT_RECENCY,
    "weight_emotional":  WEIGHT_EMOTIONAL,
    "recency_half_life": RECENCY_HALF_LIFE_DAYS,
    "low_score":         FORGET_LOW_SCORE,
    "low_days":          FORGET_LOW_DAYS,
    "mid_score":         FORGET_MID_SCORE,
    "mid_days":          FORGET_MID_DAYS,
    "compress_max_chars": 300,
}

_embedder = None


def _load_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Scheduler: loading embedding model for compression…")
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def _run_forgetting_job(db: ArchiveDB) -> dict:
    try:
        embedder = _load_embedder()
    except Exception as exc:
        logger.warning(f"Could not load embedder ({exc}); compression will be skipped")
        embedder = None

    stats = run_forgetting_cycle(db, embedder=embedder, cfg=_forgetting_cfg)
    db.set_state(_STATE_KEY, datetime.now(timezone.utc).isoformat())
    return stats


def _is_overdue(db: ArchiveDB) -> bool:
    """True if the forgetting cycle has never run, or is past its due date."""
    last_run_raw = db.get_state(_STATE_KEY)
    if last_run_raw is None:
        logger.info("No forgetting cycle has ever run — due now")
        return True

    last_run = datetime.fromisoformat(last_run_raw)
    due_at   = last_run + timedelta(weeks=FORGETTING_CYCLE_WEEKS)
    overdue  = datetime.now(timezone.utc) >= due_at

    if overdue:
        logger.info(f"Forgetting cycle overdue (last ran {last_run.isoformat()})")
    return overdue


def run_startup_catchup() -> dict:
    """
    Call once at process startup, BEFORE the MCP server starts serving
    requests. Runs the forgetting cycle immediately if it's overdue.
    Cheap no-op if it already ran recently.
    """
    db = ArchiveDB(ARCHIVE_DB_PATH, EMBEDDING_DIM)
    if not _is_overdue(db):
        logger.info("Forgetting cycle is up to date — skipping catch-up")
        return {"ran": False}

    logger.info("Running startup catch-up forgetting cycle…")
    stats = _run_forgetting_job(db)
    return {"ran": True, **stats}


def start_scheduler() -> BackgroundScheduler:
    """
    Starts the live in-process weekly timer, for deployments where the
    server process does stay running long-term. This is now a secondary
    mechanism — run_startup_catchup() is what guarantees the cycle actually
    happens for typical short-lived MCP sessions.
    """
    scheduler = BackgroundScheduler()

    def _job():
        logger.info("Live weekly timer fired")
        db = ArchiveDB(ARCHIVE_DB_PATH, EMBEDDING_DIM)
        _run_forgetting_job(db)

    scheduler.add_job(
        _job,
        trigger=IntervalTrigger(weeks=FORGETTING_CYCLE_WEEKS),
        id="forgetting_cycle",
        name="Weekly forgetting cycle (live timer)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info(
        f"Live scheduler started — forgetting cycle every "
        f"{FORGETTING_CYCLE_WEEKS} week(s) (secondary to startup catch-up)"
    )
    return scheduler
