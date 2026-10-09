from __future__ import annotations

from app.profiles.base_game_profile import NormalizedROI


# Learned from the user-provided 1717x1073 Diablo IV screenshot.
# These are profile hints, not hard requirements; later calibration can refine them.
HUD_SEARCH_REGION = NormalizedROI(0.00, 0.60, 0.43, 1.00)
HP_ORB_HINT = NormalizedROI(0.032, 0.643, 0.143, 0.848)
MP_ORB_HINT = NormalizedROI(0.102, 0.736, 0.201, 0.951)
SP_ORB_HINT = NormalizedROI(0.192, 0.862, 0.419, 0.918)

HUD_REFERENCE_FILES = {
    "hud": "assets/hud/hud_left_bottom.png",
    "hp": "assets/hud/hp_orb.png",
    "mp": "assets/hud/mp_orb.png",
    "sp": "assets/hud/sp_orb.png",
    "shield_100": "assets/hud/hp_shield_100.png",
    "shield_0": "assets/hud/hp_shield_0.png",
}
