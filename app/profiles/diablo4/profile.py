from __future__ import annotations

import numpy as np

from app.core.game_state import HUDState
from app.vision.opencv_sensor import OpenCVSensor
from app.profiles.base_game_profile import BaseGameProfile
from app.profiles.diablo4.health_sensor import DiabloOrbHealthSensor
from app.profiles.diablo4.buff_sensor import DiabloBuffSensor
from app.profiles.diablo4.hud_layout import (
    HUD_SEARCH_REGION,
    HP_ORB_HINT,
    MP_ORB_HINT,
    SP_ORB_HINT,
    HUD_REFERENCE_FILES,
)


class Diablo4Profile(BaseGameProfile):
    name = "diablo4"

    def __init__(self) -> None:
        super().__init__()
        self.profile_dir = __import__("pathlib").Path(__file__).resolve().parent
        self.health = DiabloOrbHealthSensor()
        self.hud_search_region = HUD_SEARCH_REGION
        self.hp_orb_hint = HP_ORB_HINT
        self.mp_orb_hint = MP_ORB_HINT
        self.sp_orb_hint = SP_ORB_HINT
        self.hud_reference_files = HUD_REFERENCE_FILES.copy()
        self.hud_rois = {
            "hud": HUD_SEARCH_REGION,
            "hp": HP_ORB_HINT,
            "mp": MP_ORB_HINT,
            "sp": SP_ORB_HINT,
        }
        self.buffs = DiabloBuffSensor()
        self._regions: dict = {}
        self.resources = OpenCVSensor()

    @staticmethod
    def _hint_bbox(roi) -> list[int]:
        return [
            round(roi.x1 * 1000),
            round(roi.y1 * 1000),
            round(roi.x2 * 1000),
            round(roi.y2 * 1000),
        ]

    @property
    def full_reference_path(self):
        # User-supplied profile screenshot including the complete game HUD.
        path = self.profile_dir / "assets" / "hud" / "profile_full.png"
        return (
            path if path.exists() else self.profile_dir / "assets" / "hud" / "hud_left_bottom.png"
        )

    @property
    def hud_calibration_context(self) -> str:
        return (
            "Diablo IV. The main player HUD is a persistent cluster near the lower-left/bottom area. "
            "Use the profile reference layout conceptually; return the entire HUD cluster, not a single orb or icon."
        )

    @property
    def hud_prompt_context(self) -> str:
        return (
            "Game profile: Diablo IV. Locate the PLAYER HUD meters; distinguish them from monster nameplates. "
            "Health is a large circular/orb HUD element whose red liquid drains vertically as health is lost. "
            "A blue overlay/fill on the same orb can represent barrier/shield. "
            "Locate mana/resource MP and SP meters only if actually visible; otherwise visible=false. "
            "Circular liquid meters use shape=orb, axis=y, fill_from=end. "
            "Also locate the compact row/area containing active buff icons if clearly visible. "
            "Avoid tiny red/blue decorative UI elements."
        )

    def set_hud_regions(self, regions: dict) -> None:
        self._regions = dict(regions or {})

        # The screenshot ROI is only a fallback hint.
        # health_sensor locates the real orb geometry in the current frame with OpenCV.
        health = self._regions.get("health", {})
        if health.get("visible") and health.get("bbox") != self.health.bbox:
            self.health._auto_locked = False
        if not self.health.auto_locked:
            self.health.set_bbox(
                health.get("bbox") if health.get("visible") else self._hint_bbox(self.hp_orb_hint)
            )

        self.resources.set_regions(self._regions)

        # Buff can still use Qwen as a coarse candidate because its row changes.
        buffs = self._regions.get("buffs") or {}
        self.buffs.set_bbox(buffs.get("bbox") if buffs.get("visible") else None)

    def validate_hud(self, frame: np.ndarray) -> bool:
        health_ok = self.health.validate(frame)
        if self.buffs.candidate_bbox and not self.buffs.locked_bbox:
            self.buffs.confirm_candidate(frame)
        self.resources.validate_regions(frame)
        return health_ok

    @property
    def buff_region_valid(self) -> bool:
        return self.buffs.locked_bbox is not None

    @property
    def buff_region(self) -> list[int] | None:
        return self.buffs.locked_bbox

    def analyze_hud(self, frame: np.ndarray) -> HUDState:
        hp, shield, valid = self.health.analyze(frame)
        resources = self.resources.analyze(frame)
        return HUDState(
            health=hp,
            mp=resources.mp,
            sp=resources.sp,
            mp_valid=resources.mp_valid,
            sp_valid=resources.sp_valid,
            health_valid=valid and hp is not None,
            shield_valid=valid and shield is not None,
            buffs=self.buffs.detect(frame),
            regions=self._regions.copy(),
        )

    def export_hud_regions(self):
        regions = self._regions.copy()
        if self.health.bbox:
            regions["health"] = {
                "visible": True,
                "bbox": list(self.health.bbox),
                "confidence": 1.0,
                "shape": "orb",
                "axis": "y",
                "fill_from": "end",
            }
        return regions


def create_profile() -> Diablo4Profile:
    return Diablo4Profile()
