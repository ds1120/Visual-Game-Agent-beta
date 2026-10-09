"""Profile-local settings and strict calibration/input validation."""

from __future__ import annotations
import copy
import math
from app.profiles.profile_store import atomic_json, profile_lock


def validate_visual_reference(config, *, relative=False):
    if not isinstance(config, dict):
        raise ValueError('Visual reference must be an object')
    roi = config.get('roi')
    if (not isinstance(roi, list) or len(roi) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in roi)
            or not roi[0] < roi[2] or not roi[1] < roi[3]
            or not all((-2 <= v <= 3) if relative else (0 <= v <= 1) for v in roi)):
        raise ValueError('Invalid visual reference ROI')
    path = config.get('template')
    if (not isinstance(path, str) or not path or path.startswith('/')
            or ':' in path or '\\' in path or '..' in path.split('/')):
        raise ValueError('Visual template must be a project-relative path')
    threshold = config.get('threshold', .95)
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not .8 <= threshold <= 1:
        raise ValueError('Visual reference threshold must be .8..1')

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
    "attack_skills": [],
    "attack_max_seconds": 60,
    "attack_recheck_seconds": 8,
    "attack_lost_grace_ms": 2000,
    "pointer_smoothing": True,
    "pointer_duration_ms": 120,
    "movement_skill": {"enabled":False,"key":"SPACE"},
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
        if data.get("layout", "classic") not in {"classic", "bars_hp_sp_mp"}:
            raise ValueError("HUD layout 오류")
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
            if "pixel_bbox" in r:
                res=r.get("pixel_resolution")
                box=r["pixel_bbox"]
                if not isinstance(res,list) or len(res)!=2 or not all(type(v) is int and 1<=v<=32768 for v in res) or not isinstance(box,list) or len(box)!=4 or not all(type(v) is int and v>=0 for v in box) or not (box[0]<box[2]<=res[0] and box[1]<box[3]<=res[1]):
                    raise ValueError("HUD 픽셀 영역 오류")
            for flag in ("allow_empty","tracked_stack"):
                if flag in r and type(r[flag]) is not bool: raise ValueError("HUD 프레임 검증 형식 오류")
            if "confidence" in r and not number(r["confidence"], 0, 1):
                raise ValueError("HUD confidence 범위")
            if r.get("axis", "x") not in {"x", "y"} or r.get(
                "fill_from", "start"
            ) not in {
                "start",
                "end",
            }:
                raise ValueError("HUD meter 방향 오류")
            if "inset_fraction" in r and (not isinstance(r["inset_fraction"], list) or len(r["inset_fraction"]) != 2 or not all(number(v, 0, .4) for v in r["inset_fraction"])):
                raise ValueError("HUD 내부 여백 오류")
            if "hsv_ranges" in r and not hsv(r["hsv_ranges"]):
                raise ValueError("HSV 범위 오류")
    elif filename == "navigation.json":
        if (
            not number(data.get("step_ms"), 40, 2000)
            or not number(data.get("step_fraction"), 0.03, 0.45)
            or not number(data.get("obstacle_margin"), 0, 0.1)
        ):
            raise ValueError("이동 설정 오류: 유지 시간 40~2000ms, 클릭 거리 3~45% 필요")
        m = data.get("minimap")
        if (
            not isinstance(m, dict)
            or type(m.get("enabled")) is not bool
            or not bbox(m.get("bbox"))
        ):
            raise ValueError("minimap 설정 오류")
        if not number(m.get("stuck_seconds",3),1,15) or not number(m.get("stuck_threshold",1.5),.1,20):
            raise ValueError("이동 정체 감지 설정 오류")
        mapping=m.get('mapping')
        if mapping is not None and (not isinstance(mapping,dict) or type(mapping.get('enabled')) is not bool):
            raise ValueError("미니맵 맵핑 enabled 형식 오류")
        if mapping:
            if mapping.get('mode','bright_floor') not in {'bright_floor','hsv','wall_lines','diablo4_auto'}:raise ValueError('미니맵 지형 모드 오류')
            for k,lo,hi in (('center_weight',0,15),('preferred_clearance_px',2,30),('wall_margin_px',0,5),('screen_pixels_per_map_pixel',2,40)):
                if k in mapping and not number(mapping[k],lo,hi):raise ValueError('미니맵 중앙 경로 설정 오류')
            if type(mapping.get('analysis_width',192)) is not int or not 192<=mapping.get('analysis_width',192)<=512:raise ValueError('지도 분석 너비 192~512 정수 필요')
            if type(mapping.get('grid_cell_px',4)) is not int or mapping.get('grid_cell_px',4) not in (2,4):raise ValueError('지도 격자 2 또는 4px 필요')
            if 'wall_margin_px' in mapping and type(mapping['wall_margin_px']) is not int:raise ValueError('벽 여백은 정수 필요')
            if 'pin_direction_priority' in mapping and type(mapping['pin_direction_priority']) is not bool:raise ValueError('핀 방향 우선은 boolean 필요')
            if 'explore_without_guide' in mapping and type(mapping['explore_without_guide']) is not bool:raise ValueError('안내선 없는 지도 탐색은 boolean 필요')
            if 'exclude_entity_icons' in mapping and type(mapping['exclude_entity_icons']) is not bool:raise ValueError('미니맵 아이콘 제외는 boolean 필요')
            if 'follow_centerline' in mapping and type(mapping['follow_centerline']) is not bool:raise ValueError('통로 중앙 우선은 boolean 필요')
            if 'calibrated' in mapping and type(mapping['calibrated']) is not bool:raise ValueError('미니맵 보정 상태 오류')
            if 'crop_to_viewport' in mapping and type(mapping['crop_to_viewport']) is not bool:raise ValueError('미니맵 검은 여백 제외 설정 오류')
        if m.get('wall_hsv') and not hsv(m['wall_hsv']):raise ValueError('미니맵 벽 HSV 오류')
        if mapping is not None and not number(mapping.get('pin_search_margin',0),0,.3):raise ValueError('핀 검색 확장 비율 0~0.3 필요')
        steering=data.get('steering',{})
        if not isinstance(steering,dict) or steering.get('projection','normalized') not in {'normalized','isotropic'}:raise ValueError('이동 투영 설정 오류')
        if not number(steering.get('move_interval_ms',400),100,2000):raise ValueError('이동 간격 오류')
        for flag in ('require_minimap','infer_player_from_hud'):
            if flag in steering and type(steering[flag]) is not bool:raise ValueError('이동 검증 설정 오류')
        player=steering.get('player_screen',[.5,.5])
        if not isinstance(player,list) or len(player)!=2 or not all(number(v,.1,.9) for v in player):raise ValueError('플레이어 화면 위치 오류')
        if not isinstance(data.get('excluded_regions',[]),list):raise ValueError('입력 제외 영역 목록 오류')
        for box in data.get('excluded_regions',[]):
            if not isinstance(box,list) or len(box)!=4 or not all(number(v,0,1) for v in box) or not (box[0]<box[2] and box[1]<box[3]):raise ValueError('입력 제외 영역 오류')
        if 'dark_floor' in m and not number(m['dark_floor'],30,190):
            raise ValueError("미니맵 어두운 지형 기준 오류")
        if m.get("player_hsv") and not hsv(m["player_hsv"]):
            raise ValueError("미니맵 플레이어 색상 오류")
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
        red_bar = data.get('red_enemy_bar')
        if red_bar is not None:
            if not isinstance(red_bar, dict) or type(red_bar.get('enabled')) is not bool:
                raise ValueError('Invalid red enemy bar configuration')
            validate_visual_reference({'roi': red_bar.get('roi', [.15, .15, .85, .8]), 'template': 'unused.png'})
            if (not number(red_bar.get('min_width', 20), 5, 1000)
                    or not number(red_bar.get('max_width', 220), 5, 1000)
                    or red_bar.get('min_width', 20) > red_bar.get('max_width', 220)
                    or not number(red_bar.get('max_height', 12), 2, 40)
                    or ('hsv' in red_bar and not hsv(red_bar['hsv']))):
                raise ValueError('Invalid red enemy bar geometry/color')
        bar = data.get('enemy_health_bar')
        if bar is not None:
            if not isinstance(bar, dict) or type(bar.get('enabled')) is not bool:
                raise ValueError('Invalid enemy health bar configuration')
            if bar['enabled']:
                validate_visual_reference(bar.get('presence'), relative=True)
                validate_visual_reference({'roi': bar.get('fill_roi'), 'template': 'unused.png'}, relative=True)
                if not hsv(bar.get('fill_hsv')):
                    raise ValueError('Invalid enemy health fill HSV')
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
            if isinstance(rule, dict) and type(rule.get('require_semantic', False)) is not bool:
                raise ValueError('require_semantic must be boolean')
            if (
                not isinstance(label, str)
                or not label
                or not isinstance(rule, dict)
                or rule.get("type") not in {"monster", "item", "npc", "obstacle"}
                or rule.get("relation")
                not in {"hostile", "friendly", "neutral", "unknown"}
                or not number(rule.get("min_confidence"), 0.25, 1)
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
        skill=data.get('movement_skill')
        if skill is not None and (not isinstance(skill,dict) or type(skill.get('enabled')) is not bool or not key(skill.get('key'))):
            raise ValueError('이동 스킬 enabled/key 설정 오류')
        disabled=data.get('disabled_actions',[])
        if not isinstance(disabled,list) or any(not isinstance(a,str) or a not in {'USE_SKILL','CAST_BUFF','DODGE','TAKE','INTERACT','ATTACK'} for a in disabled):raise ValueError('미확인 입력 차단 설정 오류')
        combat=data.get('combat',{})
        if not isinstance(combat,dict) or combat.get('stance','stationary') not in {'stationary','ranged','melee'} or combat.get('dodge_mode','key') not in {'key','move'}:raise ValueError('게임별 전투 설정 오류')
        for field in ('minimum_distance','attack_distance','ranged_attack_distance'):
            if field in combat and not number(combat[field],.05,.6):raise ValueError('전투 거리 오류')
        if combat.get('minimum_distance',.18)+.06>=combat.get('ranged_attack_distance',.45):raise ValueError('원거리 최대 거리는 최소 거리보다 6% 이상 커야 합니다')
        if data.get('basic_attack_mode', 'tap') not in {'tap', 'hold_right'}:
            raise ValueError('basic_attack_mode must be tap or hold_right')
        for field in ('attack_verify_hostility', 'attack_require_health_feedback'):
            if type(data.get(field, False)) is not bool:
                raise ValueError('Hostility verification flags must be boolean')
        if data.get("require_foreground") is not True or not number(
            data.get("tap_ms"), 10, 150
        ):
            raise ValueError("foreground=true 및 tap_ms 10~150 필요")
        if type(data.get("pointer_smoothing", True)) is not bool or not number(data.get("pointer_duration_ms", 120), 40, 400):
            raise ValueError("마우스 보간 활성 여부 및 시간(40~400ms)을 확인하세요.")
        if not number(data.get("attack_max_seconds",60),10,300) or not number(data.get("attack_recheck_seconds",8),2,30) or data.get("attack_recheck_seconds",8)>=data.get("attack_max_seconds",60):
            raise ValueError("지속 공격 시간/재판정 간격 오류")
        skills = data.get("attack_skills", [])
        if not isinstance(skills, list) or len(skills) > 12:
            raise ValueError("공격 스킬은 0~12개 필요")
        ids = set()
        for s in skills:
            if isinstance(s, dict) and 'visual_ready' in s:
                validate_visual_reference(s['visual_ready'])
            if (not isinstance(s, dict) or not isinstance(s.get("id"), str)
                or not s["id"] or len(s["id"]) > 64 or s["id"] in ids
                or not isinstance(s.get("name"), str) or not 1 <= len(s["name"]) <= 80
                or not key(s.get("key")) or type(s.get("enabled")) is not bool
                or not number(s.get("cooldown_ms"), 150, 60000)):
                raise ValueError("공격 스킬 id·이름·키·활성 여부·사용 간격(150~60000ms)을 확인하세요.")
            ids.add(s["id"])
            if data.get('basic_attack_mode') == 'hold_right' and s['enabled'] and s['key'] == 'mouse_right':
                raise ValueError('우클릭은 기본 공격 유지 전용입니다. 추가 스킬에는 다른 키를 지정하세요.')
        if not number(data.get("attack_lost_grace_ms",2000),500,5000):
            raise ValueError("대상 미탐지 대기 시간은 500~5000ms입니다.")
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
        if data.get('basic_attack_mode') == 'hold_right' and (bindings['ATTACK'] != 'mouse_right' or bindings['MOVE'] == 'mouse_right'):
            raise ValueError('우클릭 유지 모드에서는 ATTACK=mouse_right이며 이동 키는 달라야 합니다.')
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
        import json
        if directory.name=='diablo4':
            nav_path=directory/'navigation.json'
            nav=json.loads(nav_path.read_text(encoding='utf-8'))
            minimap=nav['minimap']
            if 'mapping' not in minimap and minimap.get('bbox') in (None,[0,0,250,250]):
                minimap.update(enabled=True,bbox=[820,42,988,237],player=[.5,.5],dark_floor=85,mapping={'enabled':True})
                atomic_json(nav_path,nav)
        hud_path = directory / "hud.json"
        hud = json.loads(hud_path.read_text(encoding="utf-8"))
        if "layout" not in hud:
            hud["layout"] = "bars_hp_sp_mp" if directory.name == "diablo4" else "classic"
            if hud["layout"] == "bars_hp_sp_mp":
                for meter in ("health", "sp", "mp"): hud["regions"].pop(meter, None)
                hud["hud_bbox"] = None
                hud["calibration"]["validated"] = False
                hud["calibration"]["source"] = "bar-layout-migration"
            atomic_json(hud_path, hud)
        if hud.get("layout")=="bars_hp_sp_mp" and hud.get("bar_locator_version")!=1:
            hud["bar_locator_version"]=1
            hud["calibration"]["validated"]=False
            hud["calibration"]["source"]="opencv-player-bars-migration"
            for meter in ("health","sp","mp"):hud["regions"].pop(meter,None)
            atomic_json(hud_path,hud)
        # Add missing input fields only; existing game key maps remain unchanged.
        import json
        input_path = directory / "input.json"
        input_value = json.loads(input_path.read_text(encoding="utf-8"))
        input_changed = False
        for field in ("attack_skills", "pointer_smoothing", "pointer_duration_ms", "attack_max_seconds", "attack_recheck_seconds", "attack_lost_grace_ms"):
            if field not in input_value:
                input_value[field] = copy.deepcopy(INPUT_DEFAULT[field])
                input_changed = True
        if input_changed:
            atomic_json(input_path, input_value)
        from app.profiles.gameplay_presets import ensure_gameplay_defaults
        ensure_gameplay_defaults(directory)
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
