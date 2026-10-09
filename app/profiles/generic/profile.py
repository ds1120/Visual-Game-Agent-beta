from __future__ import annotations
from pathlib import Path
from app.profiles.base_game_profile import BaseGameProfile
from app.vision.opencv_sensor import OpenCVSensor


class GenericProfile(BaseGameProfile):
    """Default horizontal red HP / blue MP HUD. Specialized games override sensors."""

    name = "generic"

    def __init__(self):
        super().__init__()
        self.profile_dir = Path(__file__).resolve().parent
        self.sensor = OpenCVSensor()

    @property
    def hud_prompt_context(self):
        return (
            "Locate player health, mana/resource and SP meter candidates if visually clear. "
            "Only return regions; OpenCV measures the numeric values. Return invisible for uncertain regions."
        )

    def set_hud_regions(self, regions):
        self.sensor.set_regions(regions)

    def validate_hud(self, frame):
        return self.sensor.validate_regions(frame)[0]

    def analyze_hud(self, frame):
        return self.sensor.analyze(frame)

    def export_hud_regions(self):
        return self.sensor._regions.copy()


def create_profile():
    return GenericProfile()
