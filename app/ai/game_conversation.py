"""Live dialogue stages runtime intents separately from durable profile patches."""

from __future__ import annotations
import copy
import json
import math
from app.ai.profile_chat import CHAT_SCHEMA, CHAT_PROMPT
from app.core.model_json import normalize_operations

GAME_CHAT_SCHEMA = copy.deepcopy(CHAT_SCHEMA)
GAME_CHAT_SCHEMA["properties"]["directive"] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": ["NONE", "STOP", "RESUME", "HUNT", "ATTACK", "TAKE", "INTERACT", "MOVE", "USE_SKILL", "CAST_BUFF"],
        },
        "track_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
        "direction": {
            "anyOf": [
                {
                    "type": "array",
                    "items": {"type": "number", "minimum": -1, "maximum": 1},
                    "minItems": 2,
                    "maxItems": 2,
                },
                {"type": "null"},
            ]
        },
        "ttl_seconds": {"type": "number", "minimum": 1, "maximum": 10},
    },
    "required": ["action", "track_id", "direction", "ttl_seconds"],
}
GAME_CHAT_SCHEMA["required"].append("directive")
GAME_PROMPT = """사용자와 현재 게임에 대해 대화합니다. 이미지와 최신 sensor facts만 근거로 삼으세요.
현재 실행 지시는 directive, 이후에도 유지할 설정 변경만 operations에 넣습니다.
방향은 화면 기준 오른쪽=[1,0], 왼쪽=[-1,0], 위=[0,-1], 아래=[0,1]. MOVE는 짧은 반복 이동이며 ttl_seconds 최대10.
사냥 시작/자동 사냥/맵을 돌아다니며 사냥 요청은 HUNT입니다. HUNT는 중단 명령까지 적 우선 공격하고 적이 없으면 짧은 탐색 이동을 반복합니다.
HUNT는 track_id=null,direction=null,ttl_seconds=1입니다. 실제 전체 맵 경로는 보장하지 않으며 장애물과 미니맵 설정으로 짧은 이동을 검증합니다.
스킬 사용은 USE_SKILL, 버프 사용은 CAST_BUFF입니다. input.json의 바인딩으로 입력하며 track_id=null,direction=null,ttl_seconds=1을 권장합니다. 스킬 발동이나 적중을 완료했다고 주장하지 마세요.
공격/수집/대화 대상은 facts에 있는 track_id만 사용합니다. 공격은 confirmed monster/hostile만, TAKE는 confirmed item만 가능합니다.
미확정 객체나 없는 대상이면 action=NONE으로 설명/질문하세요. 근거 없는 이동 경로/좌표/이름을 만들지 마세요.
아이템을 주우려면 hunting.json의 pickup_enabled 정책도 켜져 있어야 합니다. 사용자가 명시적으로 수집 허용을 요청한 경우에만 수정하세요.
임시 추적번호 track_id를 영구 몬스터/아이템 기록의 ID로 저장하지 마세요. 영구 기록의 memory_id를 연결할 수 있습니다.
이전 대화의 이미지는 현재 화면이 아닙니다. 답변에서 아직 실행하지 않은 동작을 완료했다고 주장하지 마세요.
HUD는 hud.json에 저장합니다. bbox=0~1000, axis=x/y, fill_from=start/end, shape=bar/orb, hsv_ranges=[[loHSV,hiHSV]].
navigation.json은 짧은 이동과 미니맵 설정입니다. 알 수 없는 미니맵 ROI/색상은 활성화하지 마세요.
input.json은 입력 바인딩입니다. require_foreground=true를 유지합니다.
object_memory.json의 status=provisional/confirmed를 수정할 수 있습니다. 사용자가 확정한 경우 locked=true로 보호하세요.
JSON만 반환합니다. directive=NONE도 track_id=null,direction=null,ttl_seconds=1을 포함합니다."""


def validate_directive(value, objects):
    if not isinstance(value, dict) or set(value) != {
        "action",
        "track_id",
        "direction",
        "ttl_seconds",
    }:
        raise ValueError("directive 형식 오류")
    action = value["action"]
    ttl = value["ttl_seconds"]
    if (
        action not in {"NONE", "STOP", "RESUME", "HUNT", "ATTACK", "TAKE", "INTERACT", "MOVE", "USE_SKILL", "CAST_BUFF"}
        or type(ttl) not in (int, float)
        or not math.isfinite(ttl)
        or not 1 <= ttl <= 10
    ):
        raise ValueError("directive action/TTL 오류")
    if action in {"ATTACK", "TAKE", "INTERACT"}:
        tid = value["track_id"]
        obj = next((o for o in objects if o.track_id == tid), None) if type(tid) is int else None
        if obj is None or obj.status != "confirmed":
            raise ValueError("확정된 현재 track_id 필요")
        if action == "ATTACK" and (obj.object_type != "monster" or obj.relation != "hostile"):
            raise ValueError("적대 몬스터만 공격할 수 있습니다.")
        if action == "TAKE" and obj.object_type != "item":
            raise ValueError("아이템만 수집할 수 있습니다.")
        if action == "INTERACT" and (
            obj.object_type != "npc" or obj.relation not in {"friendly", "neutral"}
        ):
            raise ValueError("우호/중립 NPC만 대화할 수 있습니다.")
        if value["direction"] is not None:
            raise ValueError("대상 지시의 direction은 null 필요")
    elif action == "MOVE":
        d = value["direction"]
        if (
            value["track_id"] is not None
            or not isinstance(d, list)
            or len(d) != 2
            or not all(type(v) in (int, float) and math.isfinite(v) and -1 <= v <= 1 for v in d)
            or math.hypot(*d) < 0.01
        ):
            raise ValueError("유효한 이동 방향 필요")
    elif value["track_id"] is not None or value["direction"] is not None:
        raise ValueError("대상/방향이 불필요한 지시입니다.")
    return value


class GameConversation:
    def __init__(self, client, store):
        self.client = client
        self.store = store
        self.history = []

    def propose(self, message, image, objects, hud):
        if not message.strip() or len(message) > 6000:
            raise ValueError("질문은 1~6000자 필요")
        docs, revisions = self.store.snapshot()
        context = copy.deepcopy(docs)
        if "object_memory.json" in context:
            context["object_memory.json"]["objects"] = [
                {k: v for k, v in o.items() if k != "appearances"}
                for o in context["object_memory.json"]["objects"]
            ]
        facts = {
            "hud": {
                "health": hud.health if hud.health_valid else None,
                "mp": hud.mp if hud.mp_valid else None,
                "sp": hud.sp if hud.sp_valid else None,
            },
            "objects": [
                {
                    "track_id": o.track_id,
                    "memory_id": o.memory_id,
                    "semantic": o.object_type,
                    "relation": o.relation,
                    "status": o.status,
                    "name": o.name,
                    "bbox": o.bbox,
                }
                for o in objects
            ],
        }
        prompt = (
            CHAT_PROMPT
            + "\n"
            + GAME_PROMPT
            + "\nCURRENT PROFILE:\n"
            + json.dumps(context, ensure_ascii=False)
            + "\nCURRENT SENSOR FACTS:\n"
            + json.dumps(facts, ensure_ascii=False)
        )
        # Override the profile-only prompt's execution scope: this live mode stages inputs.
        prompt = prompt.replace(
            "게임 입력을 실행하거나 실제 화면을 봤다고 주장하지 마세요. 첨부 이미지가 있을 때만 참고하세요.",
            "게임 입력을 직접 실행하지 마세요. 첨부된 현재 이미지와 sensor facts를 참고하여 실행 지시를 제안할 수 있습니다.",
        )
        result = self.client.chat_profile(
            message, prompt, list(self.history), GAME_CHAT_SCHEMA, image
        )
        data = result.data
        if not isinstance(data.get("reply"), str) or not isinstance(data.get("operations"), list):
            raise ValueError("대화 응답 형식 오류")
        validate_directive(data.get("directive"), objects)
        data = {**data, "operations": normalize_operations(data["operations"])}
        self.history.extend(
            [
                {"role": "user", "content": message},
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
            ]
        )
        self.history = self.history[-12:]
        return {**data, "revisions": revisions, "elapsed_ms": getattr(result, "elapsed_ms", None)}
