"""
Retrieval replay benchmark.

Replays a fixed bilingual query set against the LIVE data/archive.db through
the real retrieval code paths (RetrievalEngine + WarmLayerManager) and prints,
for every query, the raw cosine similarity, tag/hint boosts, and pass/fail
against the configured thresholds.

Used as the acceptance benchmark for retrieval changes — first for the v2.4
scoring overhaul (ADR-011, experiments.md E18), and intended to be re-run as
the before/after comparison for any future embedding-model change.

Run (needs the venv so sentence-transformers resolves):
    .venv/Scripts/python.exe tools/replay_retrieval.py

Notes:
  • Read-only: monkey-patches ArchiveDB.mark_accessed so replaying queries
    does not inflate access statistics.
  • Output is UTF-8 regardless of console codepage (Arabic content).
"""

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    ARCHIVE_DB_PATH,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    RETRIEVAL_SIM_THRESHOLD,
    RETRIEVAL_TAG_BOOST,
    TOP_K_RESULTS,
    WARM_LAYER_HINT_BOOST,
    WARM_LAYER_SIM_THRESHOLD,
    WARM_LAYER_TOP_K,
)
from memory.archive import ArchiveDB
from memory.retrieval import RetrievalEngine, _cosine_similarity
from memory.warm_layer import WarmLayerManager, _content_words, _cosine

# Fixed bilingual query set: direct recall questions, cross-lingual pairs,
# and probes for previously observed false positives.
QUERIES = [
    ("AR cat",        "شو اسم قطتي؟"),
    ("EN cat",        "What's my cat's name?"),
    ("AR food",       "ما هو طعام ديب المفضل؟"),
    ("EN food",       "What is Deeb's favorite food?"),
    ("EN running",    "How should I plan my training for the half-marathon?"),
    ("EN late night", "I stayed at work late last night again."),
    ("AR guitar",     "شو الهواية الجديدة يلي بلشت فيها؟"),
    ("EN guitar",     "What new hobby did I start recently?"),
    ("EN warm lang",  "What's Deeb's favorite programming language?"),
    ("AR warm loc",   "وين ساكن ديب حالياً؟"),
]


def main() -> None:
    print(f"Model: {EMBEDDING_MODEL}", file=sys.stderr)
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL)

    archive = ArchiveDB(ARCHIVE_DB_PATH, EMBEDDING_DIM)
    archive.mark_accessed = lambda _id: None  # keep the replay side-effect-free
    engine = RetrievalEngine(archive, embedder=model)
    warm = WarmLayerManager(ARCHIVE_DB_PATH, EMBEDDING_DIM)

    all_entries = archive.get_all_with_embeddings()
    warm_rows = warm._get_all_with_embeddings()

    for label, query in QUERIES:
        print("=" * 100)
        print(f"QUERY [{label}]: {query}")
        q_emb = model.encode(query, show_progress_bar=False)
        msg_words = {w.lower() for w in re.findall(r"\b\w+\b", query.lower())}

        # ── Archive: full score table (diagnostic view of engine.retrieve) ──
        print(f"  ARCHIVE (sim floor {RETRIEVAL_SIM_THRESHOLD}, tag boost +{RETRIEVAL_TAG_BOOST}; pass marked *):")
        rows = []
        for entry, emb in all_entries:
            if emb is None:
                continue
            sim = _cosine_similarity(q_emb, emb)
            boosted = sim + (RETRIEVAL_TAG_BOOST if any(t.lower() in msg_words for t in entry.tags) else 0.0)
            rows.append((boosted, sim, entry))
        rows.sort(key=lambda x: x[0], reverse=True)
        for boosted, sim, entry in rows:
            mark = "*" if boosted >= RETRIEVAL_SIM_THRESHOLD else " "
            boost_note = f" (+{boosted - sim:.2f} tag)" if boosted > sim else ""
            print(f"   {mark} sim={sim:.3f}{boost_note}  imp={entry.importance_score:.2f}  {entry.id[:8]}  {entry.content[:70]}")

        returned = engine.retrieve(query, top_k=TOP_K_RESULTS, threshold=RETRIEVAL_SIM_THRESHOLD)
        print(f"  -> engine.retrieve returned {len(returned)}: {[e.id[:8] for e in returned]}")

        # ── Warm layer: full score table (diagnostic view of retrieve_relevant) ──
        print(f"  WARM LAYER (sim floor {WARM_LAYER_SIM_THRESHOLD}, hint boost +{WARM_LAYER_HINT_BOOST}):")
        q_content_words = _content_words(query)
        for attr, emb in warm_rows:
            if emb is None:
                continue
            sim = _cosine(q_emb, emb)
            boosted = sim + (WARM_LAYER_HINT_BOOST if _content_words(attr.context_hint) & q_content_words else 0.0)
            mark = "*" if boosted >= WARM_LAYER_SIM_THRESHOLD else " "
            boost_note = f" (+{boosted - sim:.2f} hint)" if boosted > sim else ""
            print(f"   {mark} sim={sim:.3f}{boost_note}  key={attr.key}")

        returned_warm = warm.retrieve_relevant(query, query_embedding=q_emb, top_k=WARM_LAYER_TOP_K)
        print(f"  -> retrieve_relevant returned {len(returned_warm)}: {[a.key for a in returned_warm]}")

    print("=" * 100)


if __name__ == "__main__":
    main()
