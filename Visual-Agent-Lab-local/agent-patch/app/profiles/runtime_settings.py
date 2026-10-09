"""Profile-local settings and strict calibration/input validation."""

from __future__ import annotations
import copy
import math
from app.profiles.profile_store import atomic_json, profile_lock

HUD_DEFAULT = {
    "version": 1,
    "hud_bbox": None,
    "regions": {},
    "calibration": {"resolution": [], "validated": False, "source": "unset"},
}
NAV_DEFAULT = {
    "version": 1,
    "step_ms": 180,
    "step_fraction": 0.16,
    "obstacle_margin": 0.025,
    "minimap": {
        "enabled": False,
        "bbox": None,
        "player": [0.5, 0.5],
        "rotation_degrees": 0,
        "walkable_hsv": [[[0, 0, 100], [179, 100, 255]]],
    },
}
INPUT_DEFAULT = {
    "version": 1,
    "require_foreground": True,
    "tap_ms": 40,
    "bindings": {
        "ATTACK": "mouse_left",
        "TAKE": "E",
        "USE_POTION": "Q",
        "DODGE": "SPACE",
        "INTERACT": "E",
        "CAST_BUFF": "1",
        "USE_SKILL": "2",
        "MOVE": "mouse_left",
    },
    "movement": {"mode": "keys", "up": "W", "down": "S", "left": "A", "right": "D"},
}

VISION_DEFAULT = {
    "version": 1,
    "capture_fps": 60,
    "learning_interval": 1,
    "motion_gating": False,
    "semantic_gating": True,
    "class_rules": {},
    "window_titles": [],
    "yolo": {
        "enabled": False,
        "model_path": "yolo/models/best.onnx",
        "confidence": 0.4,
        "device": "0",
        "image_size": 320,
        "interval": 0.35,
        "max_detections": 10,
        "roi_x": 0.05,
        "roi_y": 0.05,
        "roi_width": 0.9,
        "roi_height": 0.85,
    },
}


def number(v, lo, hi):
    return type(v) in (int, float) and math.isfinite(v) and lo <= v <= hi


def bbox(v, nullable=True):
    return (v is None and nullable) or (
        isinstance(v, list)
        and len(v) == 4
        and all(number(x, 0, 1000) for x in v)
        and v[0] < v[2]
        and v[1] < v[3]
    )


def hsv(v):
    return (
        isinstance(v, list)
        and 1 <= len(v) <= 6
        and all(
            isinstance(r, list)
            and len(r) == 2
            and all(isinstance(c, list) and len(c) == 3 for c in r)
            and all(
                number(r[k][j], 0, 179 if j == 0 else 255)
                for k in (0, 1)
                for j in range(3)
            )
            and all(r[0][j] <= r[1][j] for j in range(3))
            for r in v
        )
    )


def key(v):
    return isinstance(v, str) and (
        len(v) == 1
        and v.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        or v
        in {
            "SPACE",
            "SHIFT",
            "CTRL",
            "ALT",
            "UP",
            "DOWN",
            "LEFT",
            "RIGHT",
            "mouse_left",
            "mouse_right",
        }
    )


def validate_settings(filename, data):
    if data.get("version") != 1:
        raise ValueError("settings version=1 필요")
    if filename == "hud.json":
        if not bbox(data.get("hud_bbox")):
            raise ValueError("HUD bbox 0~1000 필요")
        regions = data.get("regions")
        cal = data.get("calibration")
        if (
            not isinstance(regions, dict)
            or not isinstance(cal, dict)
            or type(cal.get("validated")) is not bool
        ):
            raise ValueError("HUD regions/calibration 형식 오류")
        size = cal.get("resolution", [])
        if (
            not isinstance(size, list)
            or len(size) not in (0, 2)
            or not all(number(x, 1, 32768) for x in size)
        ):
            raise ValueError("resolution 형식 오류")
        for name, r in regions.items():
            if (
                name not in {"health", "mp", "sp", "buffs"}
                or not isinstance(r, dict)
                or type(r.get("visible")) is not bool
                or not bbox(r.get("bbox"))
            ):
                raise ValueError("HUD 영역 형식 오류")
            if r["visible"] and r["bbox"] is None:
                raise ValueError("visible 영역은 bbox 필요")
            if "confidence" in r and not number(r["confidence"], 0, 1):
                raise ValueError("HUD confidence 범위")
            if r.get("axis", "x") not in {"x", "y"} or r.get(
                "fill_from", "start"
            ) not in {
                "start",
                "end",
            }:
                raise ValueError("HUD meter 방향 오류")
            if "hsv_ranges" in r and not hsv(r["hsv_ranges"]):
                raise ValueError("HSV 범위 오류")
    elif filename == "navigation.json":
        if (
            not number(data.get("step_ms"), 40, 400)
            or not number(data.get("step_fraction"), 0.03, 0.3)
            or not number(data.get("obstacle_margin"), 0, 0.1)
        ):
            raise ValueError("짧은 이동 설정 범위 오류")
        m = data.get("minimap")
        if (
            not isinstance(m, dict)
            or type(m.get("enabled")) is not bool
            or not bbox(m.get("bbox"))
        ):
            raise ValueError("minimap 설정 오류")
        if m["enabled"] and m["bbox"] is None:
            raise ValueError("미니맵 활성화 전 ROI 필요")
        if (
            not isinstance(m.get("player"), list)
            or len(m["player"]) != 2
            or not all(number(x, 0.1, 0.9) for x in m["player"])
        ):
            raise ValueError("미니맵 player 0.1~0.9 필요")
        if not number(m.get("rotation_degrees"), -180, 180) or not hsv(
            m.get("walkable_hsv")
        ):
            raise ValueError("미니맵 회전/HSV 오류")
    elif filename == "vision.json":
        if (
            not number(data.get("capture_fps", 60), 15, 120)
            or not number(data.get("learning_interval", 1), 0.5, 10)
            or type(data.get("motion_gating", False)) is not bool
            or type(data.get("semantic_gating", True)) is not bool
        ):
            raise ValueError("캡처/학습 주기/모션 게이팅 범위 오류")
        rules = data.get("class_rules", {})
        if not isinstance(rules, dict) or len(rules) > 100:
            raise ValueError("class_rules 형식 오류")
        for label, rule in rules.items():
            if (
                not isinstance(label, str)
                or not label
                or not isinstance(rule, dict)
                or rule.get("type") not in {"monster", "item", "npc", "obstacle"}
                or rule.get("relation")
                not in {"hostile", "friendly", "neutral", "unknown"}
                or not number(rule.get("min_confidence"), 0.6, 1)
            ):
                raise ValueError("클래스 매핑 type/relation/min_confidence 오류")
        titles = data.get("window_titles")
        y = data.get("yolo")
        if (
            not isinstance(titles, list)
            or len(titles) > 10
            or not all(isinstance(t, str) and 1 <= len(t) <= 200 for t in titles)
            or not isinstance(y, dict)
        ):
            raise ValueError("vision window/yolo 형식 오류")
        path = y.get("model_path", "")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ":" in path
            or "\\" in path
            or ".." in path.split("/")
        ):
            raise ValueError("프로젝트 내부 상대 model_path 필요")
        if (
            type(y.get("enabled")) is not bool
            or y.get("image_size") not in {320, 448, 640, 960}
            or not number(y.get("interval"), 0.05, 10)
            or not number(y.get("confidence"), 0.01, 1)
            or type(y.get("max_detections")) is not int
            or not 1 <= y["max_detections"] <= 100
        ):
            raise ValueError("YOLO 설정 범위 오류")
        if not isinstance(y.get("device"), str) or y["device"] not in {
            "0",
            "1",
            "cpu",
            "auto",
        }:
            raise ValueError("YOLO device 오류")
        if (
            not all(
                number(y.get(k), 0, 1)
                for k in ("roi_x", "roi_y", "roi_width", "roi_height")
            )
            or y["roi_width"] <= 0
            or y["roi_height"] <= 0
            or y["roi_x"] + y["roi_width"] > 1
            or y["roi_y"] + y["roi_height"] > 1
        ):
            raise ValueError("YOLO ROI 오류")
    elif filename == "input.json":
        if data.get("require_foreground") is not True or not number(
            data.get("tap_ms"), 10, 150
        ):
            raise ValueError("foreground=true 및 tap_ms 10~150 필요")
        bindings = data.get("bindings")
        movement = data.get("movement")
        allowed = {
            "ATTACK",
            "TAKE",
            "USE_POTION",
            "DODGE",
            "INTERACT",
            "CAST_BUFF",
            "USE_SKILL",
            "MOVE",
        }
        if (
            not isinstance(bindings, dict)
            or set(bindings) != allowed
            or not all(key(v) for v in bindings.values())
        ):
            raise ValueError("입력 바인딩 오류")
        if (
            not isinstance(movement, dict)
            or movement.get("mode") not in {"keys", "click"}
            or not all(
                key(movement.get(k)) and not movement[k].startswith("mouse")
                for k in ("up", "down", "left", "right")
            )
        ):
            raise ValueError("이동 바인딩 오류")


def ensure_runtime_settings(directory):
    with profile_lock(directory):
        for name, default in [
            ("hud.json", HUD_DEFAULT),
            ("navigation.json", NAV_DEFAULT),
            ("input.json", INPUT_DEFAULT),
            ("vision.json", VISION_DEFAULT),
        ]:
            path = directory / name
            if not path.exists():
                value = copy.deepcopy(default)
                if directory.name == "diablo4":
                    if name == "vision.json":
                        value["window_titles"] = [
                            "Diablo IV",
                            "Diablo 4",
                            "디아블로 IV",
                        ]
                        value["yolo"]["enabled"] = True
                        value["yolo"]["confidence"] = 0.2
                        value["class_rules"] = {
                            "monster": {
                                "type": "monster",
                                "relation": "hostile",
                                "min_confidence": 0.6,
                            },
                            "item": {
                                "type": "item",
                                "relation": "neutral",
                                "min_confidence": 0.7,
                            },
                            "obstacle": {
                                "type": "obstacle",
                                "relation": "neutral",
                                "min_confidence": 0.6,
                            },
                        }
                    if name == "input.json":
                        value["movement"]["mode"] = "click"
                        value["bindings"]["TAKE"] = "mouse_left"
                        value["bindings"]["INTERACT"] = "F"
                atomic_json(path, value)
        # Add missing migration fields only; never replace user tuning or memory.
        path = directory / "vision.json"
        import json

        value = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for field, default in (("semantic_gating", True), ("class_rules", {})):
            if field not in value:
                value[field] = copy.deepcopy(default)
                if (
                    field == "class_rules"
                    and directory.name == "diablo4"
                    and value["yolo"]["model_path"]
                    in {"yolo/models/best.pt", "yolo/models/best.onnx"}
                ):
                    value[field] = {
                        "monster": {
                            "type": "monster",
                            "relation": "hostile",
                            "min_confidence": 0.6,
                        },
                        "item": {
                            "type": "item",
                            "relation": "neutral",
                            "min_confidence": 0.7,
                        },
                        "obstacle": {
                            "type": "obstacle",
                            "relation": "neutral",
                            "min_confidence": 0.6,
                        },
                    }
                changed = True
        if changed:
            atomic_json(path, value)
