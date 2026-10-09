from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np

from app.core.game_state import HUDState


@dataclass(frozen=True)
class NormalizedROI:
    x1: float
    y1: float
    x2: float
    y2: float

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            round(width * self.x1),
            round(height * self.y1),
            round(width * self.x2),
            round(height * self.y2),
        )


class BaseGameProfile(ABC):
    """A game profile owns HUD references and game-specific measurement rules."""

    name = "generic"

    def __init__(self) -> None:
        self.profile_dir = Path(__file__).resolve().parent
        self.hud_reference_files: dict[str, str] = {}
        self.hud_rois: dict[str, NormalizedROI] = {}

    @property
    def semantic_knowledge(self) -> dict:
        """Optional profile-local semantic rules consumed by Qwen-VL."""
        from app.profiles.profile_store import ProfileStore

        docs, _ = ProfileStore(self.profile_dir).snapshot()
        return {
            name.removesuffix(".json"): value
            for name, value in docs.items()
            if name != "object_memory.json"
        }

    @property
    def semantic_knowledge_prompt(self) -> str:
        knowledge = self.semantic_knowledge
        if not knowledge:
            return ""
        return json.dumps(knowledge, ensure_ascii=False, separators=(",", ":"))

    @property
    def full_reference_path(self):
        path = self.profile_dir / "assets" / "hud" / "profile_full.png"
        return path if path.exists() else self.profile_dir / "assets" / "hud" / "hud.png"

    @property
    def hud_calibration_context(self) -> str:
        return f"Game profile: {self.name}. Locate only the persistent player HUD cluster."

    @property
    def buff_region_valid(self) -> bool:
        return False

    @property
    def buff_region(self):
        return None

    def reference_path(self, key: str) -> Path | None:
        rel = self.hud_reference_files.get(key)
        return (self.profile_dir / rel) if rel else None

    def hud_roi(self, key: str) -> NormalizedROI | None:
        return self.hud_rois.get(key)

    def export_hud_regions(self):
        return {}

    @property
    @abstractmethod
    def hud_prompt_context(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def set_hud_regions(self, regions: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def validate_hud(self, frame: np.ndarray) -> bool:
        raise NotImplementedError

    @abstractmethod
    def analyze_hud(self, frame: np.ndarray) -> HUDState:
        raise NotImplementedError
