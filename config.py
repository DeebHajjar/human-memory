"""
Human Memory System — v1 Configuration
All paths and tunable constants live here.
"""

from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR       = Path(__file__).parent
DATA_DIR       = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

FAST_LAYER_PATH = DATA_DIR / "fast_layer.json"
ARCHIVE_DB_PATH = DATA_DIR / "archive.db"

# ── Embedding model ───────────────────────────────────────────────────────────
# fully local — no API calls, downloads once (~470 MB)
#
# v1.1: switched from all-MiniLM-L6-v2 (English-only) to a multilingual
# model, since Arabic-language queries need semantic matching too.
# Same interface (.encode()), only the model name/dim changed.

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM   = 384

# ── Retrieval ─────────────────────────────────────────────────────────────────

TOP_K_RESULTS            = 5
RETRIEVAL_SCORE_THRESHOLD = 0.30   # combined (similarity + importance) minimum

# ── Importance scoring weights ────────────────────────────────────────────────

WEIGHT_FREQUENCY  = 0.4
WEIGHT_RECENCY    = 0.3
WEIGHT_EMOTIONAL  = 0.3

# ── Forgetting rules ──────────────────────────────────────────────────────────

FORGET_LOW_SCORE = 0.2
FORGET_LOW_DAYS  = 30      # delete if score < 0.2 AND older than 30 days

FORGET_MID_SCORE = 0.4
FORGET_MID_DAYS  = 90      # compress to summary if score < 0.4 AND older than 90 days

RECENCY_HALF_LIFE_DAYS = 60  # exponential decay half-life

# ── Scheduler ────────────────────────────────────────────────────────────────

FORGETTING_CYCLE_WEEKS = 1   # run forgetting job every N weeks
#
# v1.1: MCP servers are typically short-lived (started per session by the
# client, not a long-running daemon), so a live in-process weekly timer
# often never fires. On every startup we now check how long it's been
# since the last run and catch up immediately if overdue — see
# memory/schedule_state.py and scheduler.start_scheduler().

# ── Auto-store (Gateway) ──────────────────────────────────────────────────────
#
# v1.1: thresholds used by memory/auto_extract.py to decide, without any
# LLM call, whether an exchange is worth storing and how important it is.

AUTO_STORE_MIN_CHARS      = 20     # ignore trivially short exchanges
AUTO_STORE_DEFAULT_IMPORTANCE = 0.4
AUTO_STORE_HIGH_IMPORTANCE    = 0.7
AUTO_STORE_EMOTIONAL_WEIGHT   = 1.0  # applied when a strong-signal phrase is found
