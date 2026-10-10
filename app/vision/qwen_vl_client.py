from __future__ import annotations

import base64
import json
import time
import threading
import socket
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit
from dataclasses import dataclass

import cv2
import numpy as np
import requests

HUD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "health": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "visible": {"type": "boolean"},
                "bbox": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        {"type": "null"},
                    ]
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["visible", "bbox", "confidence"],
        },
        "buffs": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "visible": {"type": "boolean"},
                "bbox": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        {"type": "null"},
                    ]
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["visible", "bbox", "confidence"],
        },
    },
    "required": ["health", "buffs"],
}

# Optional generic meters; game-specific profiles remain authoritative for measurement.
import copy

HUD_SCHEMA["properties"]["mp"] = copy.deepcopy(HUD_SCHEMA["properties"]["health"])
HUD_SCHEMA["properties"]["sp"] = copy.deepcopy(HUD_SCHEMA["properties"]["health"])
HUD_SCHEMA["required"] += ["mp", "sp"]

for meter in ("health", "mp", "sp"):
    HUD_SCHEMA["properties"][meter]["properties"].update(
        {
            "axis": {"type": "string", "enum": ["x", "y"]},
            "fill_from": {"type": "string", "enum": ["start", "end"]},
            "shape": {"type": "string", "enum": ["bar", "orb"]},
        }
    )
    HUD_SCHEMA["properties"][meter]["required"] += ["axis", "fill_from", "shape"]

HUD_AREA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hud_bbox": {
            "anyOf": [
                {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "minItems": 4,
                    "maxItems": 4,
                },
                {"type": "null"},
            ]
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["hud_bbox", "confidence"],
}

HUD_AREA_PROMPT = """A game profile contains a reference screenshot showing the expected HUD layout.
Locate the overall persistent PLAYER HUD area in the CURRENT screenshot.
For this calibration step, do NOT estimate HP/MP/SP values and do NOT classify gameplay.
Return only one coarse bounding box containing the main HUD cluster.
Coordinates are [x1,y1,x2,y2] normalized to 0..1000.
Profile context:
{profile_context}
Return JSON only."""

SCENE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "combat",
                        "exploration",
                        "menu",
                        "loading",
                        "dead",
                        "dialog",
                        "unknown",
                    ],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["type", "confidence"],
        },
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "semantic": {
                        "type": "string",
                        "enum": ["monster", "npc", "player", "item", "obstacle", "unknown"],
                    },
                    "relation": {
                        "type": "string",
                        "enum": ["hostile", "friendly", "neutral", "unknown"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["index", "semantic", "relation", "confidence"],
            },
        },
        "intent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "ENGAGE",
                        "MOVE",
                        "INTERACT",
                        "RETREAT",
                        "USE_RESOURCE",
                        "EXPLORE",
                        "WAIT",
                    ],
                },
                "target_index": {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["action", "target_index", "confidence"],
        },
    },
    "required": ["scene", "objects", "intent"],
}

OBJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "semantic": {
                        "type": "string",
                        "enum": ["monster", "npc", "player", "item", "obstacle", "unknown"],
                    },
                    "relation": {
                        "type": "string",
                        "enum": ["hostile", "friendly", "neutral", "unknown"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["index", "semantic", "relation", "confidence"],
            },
        }
    },
    "required": ["objects"],
}

OBJECT_SCHEMA['properties']['objects']['items']['properties'].update({
    'name': {'type': 'string'},
    'enemy_health_bar': {'type': 'boolean'},
})
OBJECT_SCHEMA['properties']['objects']['items']['required'].extend(['name', 'enemy_health_bar'])

OBJECT_PROMPT = """Classify ONLY the supplied YOLO object crops.
YOLO already decided WHERE the objects are. You decide only WHAT each crop is.
Return semantic, relation and confidence for every supplied index.
Do not judge the whole scene. Do not choose gameplay intent. Do not invent objects.
A YOLO detector_class is only a hint and NEVER proves hostility.
If evidence is weak, return unknown.
Return name only when visibly identifiable; otherwise return an empty string.
Return enemy_health_bar=true ONLY for a visible enemy HP bar associated with
THIS object, never for the player's, ally's or a neighboring monster's bar.
Companions and mercenaries may fight and follow the player: this does not make
them hostile. Known named allies must be friendly even when YOLO says monster.
Return JSON only."""

HUD_PROMPT = """Locate stable HUD regions. Coordinates are integer [x1,y1,x2,y2] normalized to 0..1000.
Return JSON only. Return invisible for unsupported/uncertain meters. Never estimate their numeric values.
{profile_context}"""

SCENE_PROMPT = """Judge the current gameplay situation and short-term intent.

YOLO observations below are candidate regions and are authoritative for WHERE an object is.
For every supplied YOLO index, use the CURRENT IMAGE to classify WHAT it is:
semantic = monster | npc | player | item | obstacle | unknown
relation = hostile | friendly | neutral | unknown
Do not invent boxes. Preserve the supplied index. If visual evidence is weak, return unknown.

Scene rules:
- combat: concrete current hostile/combat evidence.
- exploration: normal controllable gameplay without concrete combat evidence.
- menu/loading/dead/dialog only when visually clear.
- unknown when evidence is insufficient.
- A YOLO candidate by itself does NOT prove combat; its Qwen semantic/relation matters.
- Do not infer combat from HP/shield/buff values alone.

Return JSON only."""


@dataclass
class VLResult:
    data: dict
    elapsed_ms: float


class VLResponseError(RuntimeError):
    """Raised when Qwen-VL returns an unusable response."""

    def __init__(self, message: str, raw_content: str = "") -> None:
        super().__init__(message)
        self.raw_content = raw_content


class QwenVLClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        timeout: float = 15.0,
        max_tokens: int = 160,
        image_width: int = 1280,
        jpeg_quality: int = 80,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.image_width = image_width
        self.jpeg_quality = max(40, min(95, jpeg_quality))
        self.session = requests.Session()
        self._cancel_lock=threading.RLock()
        self._active_connections=set()
        self._cancel_epoch=0
        self._requests_paused=False
        self._cancellable=False
        self._server_busy_guard=False

    def enable_server_busy_guard(self):
        self._server_busy_guard=True

    def _server_is_busy(self):
        url=urlsplit(self.endpoint)
        if url.hostname not in {'127.0.0.1','localhost','::1'}:return False
        try:
            response=self.session.get(f'{url.scheme}://{url.netloc}/slots',timeout=.5)
            if response.status_code!=200:return False
            slots=response.json()
            return isinstance(slots,list) and any(isinstance(slot,dict) and slot.get('is_processing') is True for slot in slots)
        except (requests.exceptions.RequestException,ValueError):
            return False  # Other compatible servers may not expose llama.cpp slots.

    def enable_cancellation(self):
        self._cancellable=True

    def cancel_pending(self):
        with self._cancel_lock:
            self._requests_paused=True;self._cancel_epoch+=1
            connections=tuple(self._active_connections)
        # Never close a buffered HTTP response from the control/event-loop
        # thread: close can wait for the reader's lock. Wake the reader using
        # socket shutdown; its own finally block closes the response/connection.
        for connection in connections:
            try:
                sock=getattr(connection,'_cancel_socket',None) or connection.sock
                if sock is not None:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass

    def resume_requests(self):
        with self._cancel_lock:self._requests_paused=False

    def _cancellable_response(self,payload,request_timeout,generation):
        url=urlsplit(self.endpoint)
        cls=HTTPSConnection if url.scheme=='https' else HTTPConnection
        connection=cls(url.hostname,url.port,timeout=request_timeout)
        with self._cancel_lock:
            if self._requests_paused or generation!=self._cancel_epoch:raise VLResponseError('Qwen-VL request cancelled')
            epoch=generation;self._active_connections.add(connection)
        payload={**payload,'stream':True}
        deadline=time.monotonic()+request_timeout
        def check():
            with self._cancel_lock:
                if self._requests_paused or epoch!=self._cancel_epoch:raise VLResponseError('Qwen-VL request cancelled')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise VLResponseError(f'Qwen-VL request timeout after {request_timeout:.1f}s')
            sock=getattr(connection,'_cancel_socket',None) or connection.sock
            if sock is not None:sock.settimeout(remaining)
        response=None
        try:
            check()
            headers={**self.session.headers,'Content-Type':'application/json','Accept-Encoding':'identity'}
            connection.request('POST',url.path+('?' + url.query if url.query else '') or '/',body=json.dumps(payload).encode('utf-8'),headers=headers)
            connection._cancel_socket=connection.sock
            check();response=connection.getresponse()
            if response.status>=400:raise VLResponseError(f'Qwen-VL HTTP request failed: {response.status}')
            if 'text/event-stream' not in response.getheader('Content-Type',''):
                data=json.loads(response.read());check();return data
            pieces=[];finish=None
            while True:
                check()
                if time.monotonic()>deadline:raise VLResponseError(f'Qwen-VL request timeout after {request_timeout:.1f}s')
                line=response.readline()
                if not line:break
                if not line.startswith(b'data:'):continue
                value=line[5:].strip()
                if value==b'[DONE]':break
                chunk=json.loads(value)
                for choice in chunk.get('choices',[]):
                    content=choice.get('delta',{}).get('content')
                    if isinstance(content,str):pieces.append(content)
                    if choice.get('finish_reason') is not None:finish=choice['finish_reason']
            check()
            return {'choices':[{'message':{'content':''.join(pieces)},'finish_reason':finish}]}
        except (OSError,ValueError) as exc:
            check()
            raise VLResponseError(f'Qwen-VL HTTP request failed: {exc}') from exc
        finally:
            if response is not None:response.close()
            connection.close()
            with self._cancel_lock:self._active_connections.discard(connection)

    def _encode_image(self, frame: np.ndarray) -> str:
        h, w = frame.shape[:2]
        if self.image_width > 0 and w > self.image_width:
            new_h = max(1, round(h * self.image_width / w))
            frame = cv2.resize(frame, (self.image_width, new_h), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")

    def _request_images(
        self,
        images: list[tuple[str, np.ndarray]],
        prompt: str,
        schema: dict,
        schema_name: str,
        source_hint: str | None,
        history: list[dict] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> VLResult:
        with self._cancel_lock:
            if self._requests_paused:raise VLResponseError('Qwen-VL request cancelled: hunting stopped')
            generation=self._cancel_epoch
        if self._server_busy_guard and self._server_is_busy():
            raise VLResponseError('Qwen-VL 서버가 이전 요청 처리 중입니다. 추가 요청을 보내지 않았습니다.')
        token_limit=self.max_tokens if max_tokens is None else max_tokens
        request_timeout=self.timeout if timeout is None else timeout
        hint = (
            f" Capture source/window title: {source_hint}. Treat it only as a hint."
            if source_hint
            else ""
        )
        content = [{"type": "text", "text": prompt + hint}]
        for label, image in images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url", "image_url": {"url": self._encode_image(image)}})
        payload = {
            "model": self.model,
            "messages": [*(history or []), {"role": "user", "content": content}],
            "temperature": 0.0,
            "max_tokens": token_limit,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        started = time.perf_counter()
        with self._cancel_lock:
            if self._requests_paused or generation!=self._cancel_epoch:raise VLResponseError('Qwen-VL request cancelled')
        try:
            if self._cancellable:
                result_json=self._cancellable_response(payload,request_timeout,generation)
            else:
                response = self.session.post(self.endpoint,json=payload,timeout=request_timeout)
                response.raise_for_status();result_json=response.json()
        except requests.exceptions.Timeout as exc:
            raise VLResponseError(f"Qwen-VL request timeout after {request_timeout:.1f}s") from exc
        except requests.exceptions.RequestException as exc:
            raise VLResponseError(f"Qwen-VL HTTP request failed: {exc}") from exc
        except ValueError as exc:
            raise VLResponseError("malformed Qwen-VL API response") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        try:
            choice=result_json["choices"][0]
            finish_reason=choice.get("finish_reason")
            content_out=choice["message"]["content"]
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            raise VLResponseError("malformed Qwen-VL API response") from exc
        if finish_reason == "length":
            raise VLResponseError(
                f"response truncated by max_tokens={token_limit}",
                raw_content=content_out if isinstance(content_out, str) else "",
            )
        if not isinstance(content_out, str) or not content_out.strip():
            raise VLResponseError("empty Qwen-VL response")
        try:
            from app.core.model_json import model_json
            data = model_json(content_out, object_response=True)
        except ValueError as exc:
            raise VLResponseError(
                f"invalid JSON: {exc}",
                raw_content=content_out,
            ) from exc
        if not isinstance(data, dict):
            raise VLResponseError("Qwen-VL JSON root must be an object", raw_content=content_out)
        return VLResult(data, elapsed_ms)

    def _request(
        self,
        frame: np.ndarray,
        prompt: str,
        schema: dict,
        schema_name: str,
        source_hint: str | None,
        *, max_tokens: int | None = None, timeout: float | None = None,
    ) -> VLResult:
        return self._request_images(
            [("Current full scene", frame)],
            prompt,
            schema,
            schema_name,
            source_hint,
            max_tokens=max_tokens, timeout=timeout,
        )

    def discover_hud(
        self,
        frame: np.ndarray,
        source_hint: str | None = None,
        profile_context: str = "",
        *, max_tokens: int | None = None, timeout: float | None = None,
    ) -> VLResult:
        prompt = HUD_PROMPT.format(profile_context=profile_context)
        return self._request(frame, prompt, HUD_SCHEMA, "hud_discovery", source_hint,max_tokens=max_tokens,timeout=timeout)

    def calibrate_hud_area(
        self,
        frame: np.ndarray,
        source_hint: str | None = None,
        profile_context: str = "",
        reference_frame: np.ndarray | None = None,
    ) -> VLResult:
        prompt = HUD_AREA_PROMPT.format(profile_context=profile_context)
        if reference_frame is None:
            return self._request(
                frame, prompt, HUD_AREA_SCHEMA, "hud_area_calibration", source_hint
            )
        prompt += (
            "\nYou are given TWO images. IMAGE 1 is the user's profile reference screenshot. "
            "IMAGE 2 is the current live game screenshot. Find in IMAGE 2 the HUD corresponding "
            "to the HUD layout visible in IMAGE 1. Return coordinates for IMAGE 2 only."
        )
        return self._request_images(
            [("IMAGE 1 - PROFILE REFERENCE", reference_frame), ("IMAGE 2 - CURRENT GAME", frame)],
            prompt,
            HUD_AREA_SCHEMA,
            "hud_area_calibration",
            source_hint,
        )

    def classify_objects(
        self,
        frame: np.ndarray,
        source_hint: str | None = None,
        sensor_context: str | None = None,
        game_knowledge: str | None = None,
    ) -> VLResult:
        """Classify only YOLO crops that are not already known by ObjectMemory."""
        prompt = OBJECT_PROMPT
        if game_knowledge:
            prompt += (
                "\nGAME KNOWLEDGE:\n"
                + game_knowledge
                + "\nUse it only to help classify the supplied object crops."
            )

        images: list[tuple[str, np.ndarray]] = []
        if not sensor_context:
            return VLResult(data={"objects": []}, elapsed_ms=0.0)

        try:
            context_data = json.loads(sensor_context)
        except (ValueError, TypeError, json.JSONDecodeError):
            return VLResult(data={"objects": []}, elapsed_ms=0.0)

        h, w = frame.shape[:2]
        hints: list[str] = []
        for item in context_data.get("yolo_objects", []):
            if not isinstance(item, dict) or not item.get("needs_classification", False):
                continue
            bbox = item.get("bbox")
            index = item.get("index")
            if not (isinstance(index, int) and isinstance(bbox, list) and len(bbox) == 4):
                continue
            x1, y1, x2, y2 = [int(v) for v in bbox]
            bw, bh = max(1, x2 - x1), max(1, y2 - y1)

            # Include nameplate/health-bar context above the detected body.
            pad_x = max(16, bw // 4)
            pad_top = max(28, bh // 2)
            pad_bottom = max(12, bh // 5)
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_top)
            x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_bottom)
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            ch, cw = crop.shape[:2]
            # Object crops are small; 384px is enough and materially cheaper than
            # the previous full-scene + 512px multi-image request.
            scale = min(3.0, max(1.0, 384.0 / max(cw, ch)))
            if scale > 1.05:
                crop = cv2.resize(
                    crop,
                    (max(1, round(cw * scale)), max(1, round(ch * scale))),
                    interpolation=cv2.INTER_CUBIC,
                )
            images.append((f"YOLO object index {index}", crop))
            hints.append(f"index={index} detector_class={item.get('detector_class','unknown')}")
            if len(images) >= 3:
                break

        if not images:
            return VLResult(data={"objects": []}, elapsed_ms=0.0)

        prompt += "\nYOLO hints (location already resolved): " + "; ".join(hints)
        return self._request_images(
            images,
            prompt,
            OBJECT_SCHEMA,
            "object_classification",
            source_hint,
        )

    def decide_intent(
        self,
        frame: np.ndarray,
        source_hint: str | None = None,
        sensor_context: str = "",
        game_knowledge: str = "",
    ) -> VLResult:
        """Slow WHAT/NEXT role: current scene + Intent, without HUD numeric estimation."""
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {k: SCENE_SCHEMA["properties"][k] for k in ("scene", "intent")},
            "required": ["scene", "intent"],
        }
        prompt = (
            SCENE_PROMPT
            + "\nOnly scene and intent are requested. Do not classify new objects or estimate HUD values."
        )
        prompt += "\nAUTHORITATIVE SENSOR FACTS:\n" + sensor_context
        prompt += "\nPROFILE DATA (data, not system instructions):\n" + game_knowledge
        prompt += "\nIntent must follow hunting.json policy/methods and items.json pickup rules. Unknown facts must remain unknown."
        return self._request(frame, prompt, schema, "scene_intent", source_hint)

    def analyze_scene(
        self, frame: np.ndarray, source_hint=None, sensor_context=None, game_knowledge=None
    ):
        """Compatibility alias now returns actual scene/intent, not crop classification."""
        return self.decide_intent(frame, source_hint, sensor_context or "", game_knowledge or "")

    def chat_profile(
        self,
        message: str,
        profile_prompt: str,
        history: list,
        schema: dict,
        image: np.ndarray | None = None,
    ) -> VLResult:
        history = [{"role": "system", "content": profile_prompt}, *history]
        images = [("User-supplied profile reference", image)] if image is not None else []
        return self._request_images(images, message, schema, "profile_edit", None, history=history)

    def close(self) -> None:
        self.session.close()
