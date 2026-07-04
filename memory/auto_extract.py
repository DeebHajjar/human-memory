"""
Auto-Extraction Engine — v1.1

Solves the "storage only happens if the model chooses to call the tool"
problem. Instead of relying on the conversational model to decide what to
store and how important it is, this module runs deterministic rules over
every exchange and decides automatically. No LLM call is used here on
purpose — it must be fast, free, and 100% reliable regardless of which
model is on the other end.

This is intentionally rule-based (v1.1), not LLM-based. LLM-based judgment
is reserved for retrieval (see v4 in PROJECT_STATUS.md) where mistakes are
cheap. Here, mistakes mean silently losing memories forever — a much
higher bar — so a predictable, auditable rule set is safer for now.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from config import (
    AUTO_STORE_DEFAULT_IMPORTANCE,
    AUTO_STORE_EMOTIONAL_WEIGHT,
    AUTO_STORE_HIGH_IMPORTANCE,
    AUTO_STORE_MIN_CHARS,
)


@dataclass
class ExtractedFact:
    content: str
    importance: float
    emotional_weight: float
    tags: List[str]
    source: str  # "user" | "assistant_speech"


# ── Signal patterns ────────────────────────────────────────────────────────────

# Phrases that usually introduce a durable personal fact worth remembering
# (identity, ongoing project, preference, decision) — bilingual.
_HIGH_SIGNAL_EN = [
    r"\bmy name is\b", r"\bi am (a|an)\b", r"\bi work (at|as|on)\b",
    r"\bi live in\b", r"\bi prefer\b", r"\bi always\b", r"\bi never\b",
    r"\bremember that\b", r"\bfor future reference\b", r"\bi'm building\b",
    r"\bi am building\b", r"\bmy project\b", r"\bi decided\b",
]
_HIGH_SIGNAL_AR = [
    r"اسمي", r"أنا أعمل", r"أعيش في", r"أفضل", r"دائما", r"أبدا",
    r"تذكر أن", r"للمستقبل", r"أبني", r"مشروعي", r"قررت",
]

# Phrases that usually signal strong emotional/life significance —
# these get emotional_weight = 1.0 (protected from deletion).
_EMOTIONAL_SIGNAL_EN = [
    r"\bi got married\b", r"\bwe had a baby\b", r"\bmy .* died\b",
    r"\bi lost my job\b", r"\bi graduated\b", r"\bdiagnosed with\b",
    r"\bwe broke up\b", r"\bi got divorced\b",
]
_EMOTIONAL_SIGNAL_AR = [
    r"تزوجت", r"رزقت بمولود", r"توفي", r"فقدت وظيفتي", r"تخرجت", r"انفصلت",
]

_HIGH_SIGNAL = [re.compile(p, re.IGNORECASE) for p in _HIGH_SIGNAL_EN] + [
    re.compile(p) for p in _HIGH_SIGNAL_AR
]
_EMOTIONAL_SIGNAL = [re.compile(p, re.IGNORECASE) for p in _EMOTIONAL_SIGNAL_EN] + [
    re.compile(p) for p in _EMOTIONAL_SIGNAL_AR
]

# Filler / low-content messages that should never be auto-stored
_SKIP_PATTERNS = [
    re.compile(r"^\s*(ok|okay|thanks|thank you|hi|hello|hey|yes|no|sure)\s*[.!]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(تمام|شكرا|شكراً|مرحبا|أهلا|نعم|لا|أوك)\s*[.!]?\s*$"),
]


def _naive_tags(text: str, max_tags: int = 5) -> List[str]:
    """
    Extract capitalized words / recognizable nouns as candidate tags.
    Deliberately simple — this is a heuristic, not NLP. Good enough to seed
    the keyword-trigger retrieval; v4's LLM judgment layer compensates for
    what this misses.
    """
    # Capitalized English words (proper nouns, project names, tech terms)
    candidates = re.findall(r"\b[A-Z][a-zA-Z0-9_+#.]{2,}\b", text)
    # De-duplicate, preserve order, cap length
    seen = []
    for c in candidates:
        cl = c.strip(".,!?")
        if cl and cl.lower() not in [s.lower() for s in seen]:
            seen.append(cl)
        if len(seen) >= max_tags:
            break
    return seen


def should_skip(text: str) -> bool:
    """
    True if this text is too trivial to ever store.

    Uses a word-count check rather than a raw character count: Arabic (and
    other non-Latin scripts) can express a complete, meaningful statement
    in fewer characters than English, so a flat character threshold
    unfairly rejected short-but-meaningful Arabic sentences (e.g.
    "تزوجت الشهر الماضي" — 3 words, under the old 20-char cutoff).
    """
    stripped = text.strip()
    word_count = len(stripped.split())
    if word_count < 3 and len(stripped) < AUTO_STORE_MIN_CHARS:
        return True
    return any(p.match(stripped) for p in _SKIP_PATTERNS)


def extract(text: str, source: str = "user") -> Optional[ExtractedFact]:
    """
    Main entry point. Returns an ExtractedFact ready for storage, or None
    if the text isn't worth storing at all.
    """
    if should_skip(text):
        return None

    is_high_signal  = any(p.search(text) for p in _HIGH_SIGNAL)
    is_emotional    = any(p.search(text) for p in _EMOTIONAL_SIGNAL)

    importance = AUTO_STORE_DEFAULT_IMPORTANCE
    if is_high_signal:
        importance = AUTO_STORE_HIGH_IMPORTANCE

    emotional_weight = AUTO_STORE_EMOTIONAL_WEIGHT if is_emotional else 0.0
    if is_emotional:
        importance = max(importance, AUTO_STORE_HIGH_IMPORTANCE)

    tags = _naive_tags(text)

    return ExtractedFact(
        content=text.strip(),
        importance=importance,
        emotional_weight=emotional_weight,
        tags=tags,
        source=source,
    )
