"""
Data models for the Human Memory System.
All layers are represented as plain Python dataclasses so they serialize
cleanly to JSON and stay dependency-free.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# ── Archive entry (Layer 4) ───────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """One record stored in the SQLite archive."""

    content: str

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Optional short version (populated after compression)
    summary: Optional[str] = None

    # Embedding is stored as raw bytes in the DB; never exposed in to_dict()
    embedding: Optional[bytes] = None

    # Scoring
    importance_score: float = 0.5
    frequency_score:  float = 0.0
    recency_score:    float = 1.0
    emotional_weight: float = 0.0   # 1.0 = never delete

    # Metadata
    tags:         List[str]        = field(default_factory=list)
    source:       str              = "user"   # user | assistant_speech | assistant_thought
    timestamp:    Optional[datetime] = field(default_factory=datetime.utcnow)
    last_accessed: Optional[datetime] = None
    access_count: int              = 0

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "content":          self.content,
            "summary":          self.summary,
            "importance_score": round(self.importance_score, 4),
            "emotional_weight": self.emotional_weight,
            "tags":             self.tags,
            "source":           self.source,
            "timestamp":        self.timestamp.isoformat() if self.timestamp else None,
            "last_accessed":    self.last_accessed.isoformat() if self.last_accessed else None,
            "access_count":     self.access_count,
        }


# ── Fast layer (Layer 1) ──────────────────────────────────────────────────────

@dataclass
class FastLayer:
    """
    Core identity — always injected into every conversation.
    Kept small and stable; edit fast_layer.json directly to update.
    """

    name:               str            = ""
    age:                Optional[int]  = None
    language:           str            = "en"
    personality_traits: List[str]      = field(default_factory=list)
    key_preferences:    List[str]      = field(default_factory=list)
    values:             List[str]      = field(default_factory=list)
    active_task_id:     Optional[str]  = None

    def to_dict(self) -> dict:
        return {
            "name":               self.name,
            "age":                self.age,
            "language":           self.language,
            "personality_traits": self.personality_traits,
            "key_preferences":    self.key_preferences,
            "values":             self.values,
            "active_task_id":     self.active_task_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FastLayer":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ── Warm layer (Layer 2 — v2) ──────────────────────────────────────────────────

@dataclass
class WarmAttribute:
    """
    A single secondary biographical/preference attribute stored in the
    Warm Layer. Unlike Archive entries (which accumulate), warm attributes
    are upserted by key — a new value replaces the old one.

    Examples: location, occupation, birthdate, recurring_habit.
    """

    key: str                           # semantic key (e.g. "location")
    value: str                         # full text of the fact
    context_hint: str = ""            # auto-generated hint for when to surface this

    # Embedding is stored as raw bytes in the DB; never exposed in to_dict()
    embedding: Optional[bytes] = None

    importance: float = 0.5
    last_updated: Optional[datetime] = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "key":          self.key,
            "value":        self.value,
            "context_hint": self.context_hint,
            "importance":   round(self.importance, 4),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


# ── Layered context (output of get_context) ───────────────────────────────────

@dataclass
class LayeredContext:
    """What the AI receives before generating its response."""

    fast_layer:               FastLayer
    retrieved_memories:       List[MemoryEntry]    = field(default_factory=list)
    retrieval_triggered:      bool                 = False
    # v2 additions
    warm_attributes:          List[WarmAttribute]  = field(default_factory=list)
    warm_retrieval_triggered: bool                 = False

    def to_dict(self) -> dict:
        return {
            "fast_layer":               self.fast_layer.to_dict(),
            "retrieval_triggered":      self.retrieval_triggered,
            "retrieved_memories":       [m.to_dict() for m in self.retrieved_memories],
            # v2 additions
            "warm_retrieval_triggered": self.warm_retrieval_triggered,
            "warm_attributes":          [a.to_dict() for a in self.warm_attributes],
        }
