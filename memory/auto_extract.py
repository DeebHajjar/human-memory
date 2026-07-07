"""
Auto-Extraction Engine — v2

Refactored from v1.1's flat regex pattern lists into a pluggable Rule-Plugin
architecture (ADR-004). Each detection concern is now its own Rule class with
a common interface, registered in RuleEngine.

Public interface is unchanged — callers that used extract() / should_skip()
continue to work exactly as before. New rule types (e.g. WarmAttributeRule)
are added as new Rule subclasses, not by editing growing shared pattern lists.

Design note: this engine is intentionally rule-based, not LLM-based.
An LLM-based *retrieval* judgment (v4) can afford occasional misses.
An LLM-based *storage* judgment being wrong means silently losing information
forever — a much higher bar — so a predictable, auditable rule set is safer.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import (
    AUTO_STORE_DEFAULT_IMPORTANCE,
    AUTO_STORE_EMOTIONAL_WEIGHT,
    AUTO_STORE_HIGH_IMPORTANCE,
    AUTO_STORE_MIN_CHARS,
)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class RuleMatch:
    """
    What a single Rule returns when it fires.
    A Rule returns None to mean "no match / not applicable."
    """
    importance_boost: float = 0.0
    emotional_weight: float = 0.0
    tags: List[str] = field(default_factory=list)
    should_skip: bool = False
    category: str = ""   # "identity" | "emotional" | "filler" | "warm"
    # Warm Layer specific (only populated by WarmAttributeRule)
    warm_key: Optional[str] = None      # e.g. "location", "occupation"
    warm_value: Optional[str] = None    # extracted value text
    warm_context_hint: Optional[str] = None  # auto-generated context hint


@dataclass
class ExtractedFact:
    """Output of extract() — what gets stored in the Archive."""
    content: str
    importance: float
    emotional_weight: float
    tags: List[str]
    source: str   # "user" | "assistant_speech"


@dataclass
class WarmCandidate:
    """Output of extract_warm() — what gets upserted into the Warm Layer."""
    key: str
    value: str
    context_hint: str
    importance: float = 0.5


# ── Abstract base ─────────────────────────────────────────────────────────────

class Rule(ABC):
    """
    Base class for all extraction rules.
    Subclass and implement match(); return RuleMatch if the rule fires,
    None if it doesn't apply.
    """

    @abstractmethod
    def match(self, text: str) -> Optional[RuleMatch]:
        ...


# ── Concrete Rules ────────────────────────────────────────────────────────────

class FillerSkipRule(Rule):
    """
    Marks trivially short / filler messages as should_skip.

    Uses a word-count check rather than a raw character count: Arabic (and
    other non-Latin scripts) can express a complete meaningful statement in
    fewer characters than English, so a flat character threshold unfairly
    rejected short-but-meaningful Arabic sentences (e.g. "تزوجت الشهر الماضي"
    — 3 words, under the old 20-char cutoff). Fixed in v1.1; preserved here.

    Exception: never skips text that matches a high-signal or emotional
    pattern, even if the text is very short — e.g. "اسمي ديب" (my name is
    Deeb) is only 2 words but is clearly worth storing.
    """

    _FILLER_EN = re.compile(
        r"^\s*(ok|okay|thanks|thank you|hi|hello|hey|yes|no|sure)\s*[.!]?\s*$",
        re.IGNORECASE,
    )
    _FILLER_AR = re.compile(
        r"^\s*(\u062a\u0645\u0627\u0645|\u0634\u0643\u0631\u0627|\u0634\u0643\u0631\u0627\u064b|\u0645\u0631\u062d\u0628\u0627|\u0623\u0647\u0644\u0627|\u0646\u0639\u0645|\u0644\u0627|\u0623\u0648\u0643)\s*[.!]?\s*$"
    )
    # High-signal phrases that should never be skipped, even if text is short
    _HIGH_SIGNAL_EXEMPT_EN = [re.compile(p, re.IGNORECASE) for p in [
        r"\bmy name is\b", r"\bi am (a|an)\b", r"\bi work\b", r"\bi live\b",
        r"\bi prefer\b", r"\bi decided\b", r"\bi got married\b",
        r"\bi graduated\b", r"\bi lost my job\b",
    ]]
    _HIGH_SIGNAL_EXEMPT_AR = [re.compile(p) for p in [
        r"\u0627\u0633\u0645\u064a", r"\u0623\u0639\u0645\u0644", r"\u0623\u0639\u064a\u0634",
        r"\u062a\u0632\u0648\u062c\u062a", r"\u062a\u062e\u0631\u062c\u062a",
        r"\u0641\u0642\u062f\u062a", r"\u0642\u0631\u0631\u062a",
    ]]

    def match(self, text: str) -> Optional[RuleMatch]:
        stripped = text.strip()
        word_count = len(stripped.split())
        char_count = len(stripped)

        # Explicit filler pattern always skips regardless
        if self._FILLER_EN.match(stripped) or self._FILLER_AR.match(stripped):
            return RuleMatch(should_skip=True, category="filler")

        # Short text: only skip if no high-signal phrase overrides
        if word_count < 3 and char_count < AUTO_STORE_MIN_CHARS:
            exempt_patterns = self._HIGH_SIGNAL_EXEMPT_EN + self._HIGH_SIGNAL_EXEMPT_AR
            if not any(p.search(stripped) for p in exempt_patterns):
                return RuleMatch(should_skip=True, category="filler")

        return None


class IdentitySignalRule(Rule):
    """
    Detects phrasing that introduces a durable personal fact:
    identity, ongoing project, preference, decision — bilingual (EN/AR).
    Raises importance when fired.
    """

    _PATTERNS_EN = [re.compile(p, re.IGNORECASE) for p in [
        r"\bmy name is\b", r"\bi am (a|an)\b", r"\bi work (at|as|on)\b",
        r"\bi live in\b", r"\bi prefer\b", r"\bi always\b", r"\bi never\b",
        r"\bremember that\b", r"\bfor future reference\b", r"\bi'm building\b",
        r"\bi am building\b", r"\bmy project\b", r"\bi decided\b",
    ]]
    _PATTERNS_AR = [re.compile(p) for p in [
        r"اسمي", r"أنا أعمل", r"أعيش في", r"أفضل", r"دائما", r"أبدا",
        r"تذكر أن", r"للمستقبل", r"أبني", r"مشروعي", r"قررت",
    ]]

    def match(self, text: str) -> Optional[RuleMatch]:
        all_patterns = self._PATTERNS_EN + self._PATTERNS_AR
        if any(p.search(text) for p in all_patterns):
            return RuleMatch(
                importance_boost=AUTO_STORE_HIGH_IMPORTANCE - AUTO_STORE_DEFAULT_IMPORTANCE,
                category="identity",
            )
        return None


class EmotionalSignalRule(Rule):
    """
    Detects strong life-event phrasing that should always be protected
    from deletion (emotional_weight = 1.0) — bilingual (EN/AR).
    """

    _PATTERNS_EN = [re.compile(p, re.IGNORECASE) for p in [
        r"\bi got married\b", r"\bwe had a baby\b", r"\bmy .* died\b",
        r"\bi lost my job\b", r"\bi graduated\b", r"\bdiagnosed with\b",
        r"\bwe broke up\b", r"\bi got divorced\b",
    ]]
    _PATTERNS_AR = [re.compile(p) for p in [
        r"تزوجت", r"رزقت بمولود", r"توفي", r"فقدت وظيفتي", r"تخرجت", r"انفصلت",
    ]]

    def match(self, text: str) -> Optional[RuleMatch]:
        all_patterns = self._PATTERNS_EN + self._PATTERNS_AR
        if any(p.search(text) for p in all_patterns):
            return RuleMatch(
                importance_boost=AUTO_STORE_HIGH_IMPORTANCE - AUTO_STORE_DEFAULT_IMPORTANCE,
                emotional_weight=AUTO_STORE_EMOTIONAL_WEIGHT,
                category="emotional",
            )
        return None


class WarmAttributeRule(Rule):
    """
    v2: Detects stable personal biographical facts that belong in the
    Warm Layer (upsert/replace) rather than the Archive (append).

    Each entry in _CATEGORIES maps a semantic key to:
      (list_of_regex_patterns, context_hint_string)

    When fired, populates warm_key/warm_value/warm_context_hint in RuleMatch.
    The value is the full sentence — value extraction is kept simple on purpose;
    a more targeted NLP extraction step is deferred to v4/v5.
    """

    _CATEGORIES: List[Tuple[str, List[str], str]] = [
        (
            "location",
            [
                r"\bi (live|moved|relocated|am living) (to|in|at)\b",
                r"\bmy (home|city|country|address) is\b",
                r"\bأعيش في\b", r"\bانتقلت إلى\b", r"\bأسكن في\b",
            ],
            "when discussing location, travel, or geography",
        ),
        (
            "occupation",
            [
                r"\bi work (at|as|for|in)\b",
                r"\bmy (job|profession|career|role|position) is\b",
                r"\bi am (a|an) \w+\b",  # "I am a developer"
                r"\bأعمل (في|كـ|لدى)\b", r"\bوظيفتي\b", r"\bمهنتي\b",
            ],
            "when discussing work, career, or professional topics",
        ),
        (
            "birthdate",
            [
                r"\b(i was |i'm |i am )?(born|birthday)\b",
                r"\bmy (birthday|birth date|date of birth)\b",
                r"\bولدت في\b", r"\bعيد ميلادي\b", r"\bتاريخ ميلادي\b",
            ],
            "when discussing age, birthday, or time-sensitive personal info",
        ),
        (
            "education",
            [
                r"\bi (study|studied|am studying|graduate[d]?|attend[s]?) (at|in|from)?\b",
                r"\bmy (degree|major|university|college|school) is\b",
                r"\bأدرس\b", r"\bتخرجت من\b", r"\bجامعتي\b", r"\bتخصصي\b",
            ],
            "when discussing education, studies, or academic background",
        ),
        (
            "recurring_habit",
            [
                r"\bevery (monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|day|morning|evening)\b",
                r"\bi (usually|always|regularly|typically) \w+\b",
                r"\bكل (يوم|أسبوع|صباح|مساء|إثنين|ثلاثاء|أربعاء|خميس|جمعة|سبت|أحد)\b",
                r"\bعادةً\b", r"\bدائماً أ\w+\b",
            ],
            "when discussing routines, schedules, or recurring activities",
        ),
        (
            "language_preference",
            [
                r"\bi (prefer|like|want) (to (speak|communicate|talk) in|speaking|using)\b",
                r"\bmy (native|preferred|main) language\b",
                r"\bأفضل التحدث\b", r"\bلغتي الأم\b", r"\bلغتي المفضلة\b",
            ],
            "when discussing language or communication preferences",
        ),
    ]

    def __init__(self):
        # Pre-compile all patterns
        self._compiled: List[Tuple[str, List[re.Pattern], str]] = []
        for key, patterns, hint in self._CATEGORIES:
            compiled_patterns = [
                re.compile(p, re.IGNORECASE) if re.search(r'[a-zA-Z]', p) else re.compile(p)
                for p in patterns
            ]
            self._compiled.append((key, compiled_patterns, hint))

    def match(self, text: str) -> Optional[RuleMatch]:
        for key, patterns, hint in self._compiled:
            if any(p.search(text) for p in patterns):
                return RuleMatch(
                    importance_boost=0.1,  # slight boost — warm facts are important
                    category="warm",
                    warm_key=key,
                    warm_value=text.strip(),
                    warm_context_hint=hint,
                )
        return None


# ── Tag extraction helper ──────────────────────────────────────────────────────

def _naive_tags(text: str, max_tags: int = 5) -> List[str]:
    """
    Extract capitalized words / recognizable nouns as candidate tags.
    Deliberately simple — this is a heuristic, not NLP. Good enough to seed
    the keyword-trigger retrieval; v4's LLM judgment layer compensates for
    what this misses.
    """
    candidates = re.findall(r"\b[A-Z][a-zA-Z0-9_+#.]{2,}\b", text)
    seen = []
    for c in candidates:
        cl = c.strip(".,!?")
        if cl and cl.lower() not in [s.lower() for s in seen]:
            seen.append(cl)
        if len(seen) >= max_tags:
            break
    return seen


# ── Rule Engine ───────────────────────────────────────────────────────────────

class RuleEngine:
    """
    Iterates over registered rules and aggregates their matches.
    Rules are evaluated in order; all firing rules contribute to the result.
    A single should_skip=True from any rule short-circuits the rest.
    """

    def __init__(self, rules: Optional[List[Rule]] = None):
        self.rules: List[Rule] = rules or []

    def register(self, rule: Rule) -> None:
        self.rules.append(rule)

    def run(self, text: str) -> Dict:
        """
        Returns an aggregated result dict with:
          should_skip, importance, emotional_weight, tags,
          warm_key, warm_value, warm_context_hint
        """
        result = {
            "should_skip": False,
            "importance": AUTO_STORE_DEFAULT_IMPORTANCE,
            "emotional_weight": 0.0,
            "tags": [],
            "warm_key": None,
            "warm_value": None,
            "warm_context_hint": None,
        }

        for rule in self.rules:
            match = rule.match(text)
            if match is None:
                continue

            if match.should_skip:
                result["should_skip"] = True
                return result  # short-circuit

            result["importance"] = min(
                1.0,
                result["importance"] + match.importance_boost,
            )
            if match.emotional_weight > result["emotional_weight"]:
                result["emotional_weight"] = match.emotional_weight

            result["tags"].extend(match.tags)

            # Warm attribute (first match wins for key uniqueness)
            if match.warm_key and result["warm_key"] is None:
                result["warm_key"] = match.warm_key
                result["warm_value"] = match.warm_value
                result["warm_context_hint"] = match.warm_context_hint

        # Add naive tag extraction on top of rule-provided tags
        result["tags"] = list({t: None for t in result["tags"] + _naive_tags(text)}.keys())[:5]
        return result


# ── Default engine instance ───────────────────────────────────────────────────

_default_engine = RuleEngine([
    FillerSkipRule(),
    WarmAttributeRule(),   # check warm before identity — warm is more specific
    IdentitySignalRule(),
    EmotionalSignalRule(),
])


# ── Public API (unchanged from v1.1) ─────────────────────────────────────────

def should_skip(text: str) -> bool:
    """
    True if this text is too trivial to ever store.
    Delegates to FillerSkipRule via the default engine.
    """
    # Fast path: run only the filler rule
    match = FillerSkipRule().match(text)
    return match is not None and match.should_skip


def extract(text: str, source: str = "user") -> Optional[ExtractedFact]:
    """
    Main entry point for Archive storage. Returns an ExtractedFact ready
    for storage, or None if the text isn't worth storing at all.

    Note: if the text matches a Warm Layer attribute pattern, it will ALSO
    be returned here as an ExtractedFact for archive storage — whether to
    ALSO upsert it into the Warm Layer is a separate decision made by the
    caller (see extract_warm() and gateway.py).
    """
    result = _default_engine.run(text)

    if result["should_skip"]:
        return None

    return ExtractedFact(
        content=text.strip(),
        importance=result["importance"],
        emotional_weight=result["emotional_weight"],
        tags=result["tags"],
        source=source,
    )


def extract_warm(text: str) -> Optional[WarmCandidate]:
    """
    v2: Returns a WarmCandidate if the text matches a Warm Layer attribute
    pattern, or None otherwise.

    This is called in addition to extract() — not instead of it. The Gateway
    and MCP server decide whether to route to warm upsert vs archive store
    based on whether this returns a result.
    """
    result = _default_engine.run(text)

    if result["should_skip"] or result["warm_key"] is None:
        return None

    return WarmCandidate(
        key=result["warm_key"],
        value=result["warm_value"],
        context_hint=result["warm_context_hint"],
        importance=result["importance"],
    )
