"""
Fast Layer Manager — Layer 1
Reads and writes the fast_layer.json file.
This layer is always loaded on every request; never retrieved.
"""

import json
import logging
from pathlib import Path

from .models import FastLayer

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE = {
    "name": "",
    "age": None,
    "language": "en",
    "personality_traits": [],
    "key_preferences": [],
    "values": [],
    "active_task_id": None,
}


class FastLayerManager:
    def __init__(self, path: Path):
        self.path = path
        self._ensure_exists()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ensure_exists(self) -> None:
        """Create the file with a blank template if it doesn't exist yet."""
        if not self.path.exists():
            self.path.write_text(
                json.dumps(_DEFAULT_TEMPLATE, indent=2, ensure_ascii=False)
            )
            logger.info(f"Created blank fast layer at {self.path}")

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> FastLayer:
        """Read the JSON file and return a FastLayer instance."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return FastLayer.from_dict(raw)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"Could not parse fast layer ({exc}); returning defaults")
            return FastLayer()

    def save(self, fast_layer: FastLayer) -> None:
        """Write a FastLayer back to disk."""
        self.path.write_text(
            json.dumps(fast_layer.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update(self, **kwargs) -> FastLayer:
        """Patch specific fields and save."""
        fl = self.load()
        for key, value in kwargs.items():
            if hasattr(fl, key):
                setattr(fl, key, value)
            else:
                logger.warning(f"FastLayer has no field '{key}' — skipped")
        self.save(fl)
        return fl
