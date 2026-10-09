from __future__ import annotations
import asyncio
import json
import time
import threading
import uuid
from collections import deque
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
from app.core.object_memory import ObjectMemory
from app.core.semantic_resolver import SemanticResolver
from app.core.fast_policy import FastPolicy, command
from app.core.local_navigation import LocalNavigator, minimap_mask
from app.core.action_mapper import ActionMapper
from app.core.game_state import HUDState
from app.core.intent import Intent
from app.vision.qwen_vl_client import QwenVLClient, VLResponseError
from app.vision.yolo_sensor import YOLOSensor
from app.core.logger import get_logger
from app.profiles.registry import create_game_profile
from app.profiles.profile_store import ProfileStore, migrate_legacy_memory, PROJECT_ROOT
from app.profiles.runtime_settings import ensure_runtime_settings
from app.ai.game_conversation import GameConversation

log = get_logger(__name__)


class VisualAgent:
    """Independent capture/HUD/YOLO/reactions; Qwen only calibrates, learns and chats."""

    def __init__(
        self,
        capture,
        vl,
        scheduler,
        interval=1.0,
        min_intent_confidence=0.6,
        hud_retry_interval=5,
        summary_interval=2,
        yolo_config=None,
        profile=None,
        capture_fps=60,
        hud_interval=0.05,
        intent_interval=1,
        intent_cache_ttl=3,
        chat_vl=None,
        console_enabled=True,
        reaction_interval=0.05,
    ):
        self.capture = capture
        self.vl = vl
        self.chat_vl = chat_vl or vl
        self.scheduler = scheduler
        self.profile = profile or create_game_profile("diablo4")
        self.store = ProfileStore(self.profile.profile_dir)
        migrate_legacy_memory(self.profile.profile_dir)
        ensure_runtime_settings(self.profile.profile_dir)
        self._docs, self._revisions = self.store.snapshot()
        self._profile_lock = threading.RLock()
        self._detector_lock = threading.RLock()
        self._web_events = deque(maxlen=200)
        self._web_instance = str(uuid.uuid4())
        self._web_event_seq = 0
        self._capture_samples = deque(maxlen=240)
        self._hud_ms = None
        self._last_vl_ms = None
        self._learning_at = -1e9
        self._motion_image = None
        self._last_yolo_inference = -1e9
        self._gated_frames = 0
        self._vl_lock = asyncio.Lock()
        self.capture_fps = max(
            1,
            min(120, float(self._docs["vision.json"].get("capture_fps", capture_fps))),
        )
        self.hud_interval = max(0.02, float(hud_interval))
        self.interval = max(0.2, float(interval))
        self.reaction_interval = max(0.02, float(reaction_interval))
        self.intent_interval = max(0.2, float(intent_interval))
        self.intent_cache_ttl = max(self.intent_interval, float(intent_cache_ttl))
        self._last_intent_data = None
        self._last_intent_signature = None
        self._last_intent_at = 0
        self._last_intent_attempt = 0
        self._vl_backoff_until = 0
        self._vl_backoff_seconds = 2
        self._vl_error_active = False
        self._game_knowledge_prompt = ""
        self._emergency_hp_threshold = 30
        self._pickup_enabled = False
        self._refresh_profile_policy()
        self.mapper = ActionMapper(min_intent_confidence)
        self.policy = FastPolicy()
        self.navigator = LocalNavigator()
        self._latest_frame = None
        self._latest_captured = None
        self._frame_seq = 0
        self._capture_at = 0
        self._frame_identity = None
        self._latest_hud_state = HUDState()
        self._hud_at = 0
        self._hud_ready = False
        self._hud_failures = 0
        self._hud_retry_interval = max(1, float(hud_retry_interval))
        self._last_hud_attempt = -1e9
        self._last_potion_submit = 0
        self._hud_scan_complete = self._docs["hud.json"]["calibration"]["validated"]
        self._set_hud_regions(self._docs["hud.json"]["regions"])
        self.yolo = YOLOSensor(**(yolo_config or {"enabled": False}))
        self.object_memory = ObjectMemory(
            str(self.profile.profile_dir / "object_memory.json"),
            similarity_threshold=0.86,
        )
        self.resolver = SemanticResolver(
            self.object_memory, self._docs["vision.json"].get("class_rules", {})
        )
        self.object_tracker = self.resolver.tracker
        self._latest_yolo_objects = []
        self._objects = []
        self._yolo_at = 0
        self._minimap_mask = None
        self._profile_write_in_progress = False
        self._epoch = 0
        self._directive = None
        self._directive_until = 0
        self._paused = False
        self._running = False
        self._summary_interval = max(0.5, float(summary_interval))
        self._last_summary_time = 0
        self._last_report_action = None
        self._tasks = []
        self._capture_task = None
        self._yolo_task = None
        self._hud_task = None
        self._capture_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="vga-capture"
        )
        self._yolo_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="vga-yolo"
        )
        self._hud_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="vga-hud"
        )
        self._console_enabled = console_enabled
        self._console_queue = asyncio.Queue()
        self._chat_queue = asyncio.Queue(maxsize=4)
        self.conversation = GameConversation(self.chat_vl, self.store)
        executor = getattr(scheduler, "executor", None)
        if executor is not None:
            executor.validator = self.can_execute

    def _refresh_profile_policy(self):
        selected = {
            k: v
            for k, v in self._docs.items()
            if k in {"knowledge.json", "monsters.json", "items.json", "hunting.json"}
        }
        self._game_knowledge_prompt = json.dumps(
            selected, ensure_ascii=False, separators=(",", ":")
        )
        policy = self._docs.get("hunting.json", {}).get("policy", {})
        self._emergency_hp_threshold = policy.get("potion_hp_threshold", 30)
        self._pickup_enabled = policy.get("pickup_enabled", False)

    def _set_hud_regions(self, data):
        with self._profile_lock:
            self.profile.set_hud_regions(data)

    def _validate_hud(self, frame):
        with self._profile_lock:
            return self.profile.validate_hud(frame)

    def _analyze_hud(self, frame):
        with self._profile_lock:
            return self.profile.analyze_hud(frame)

    def _fresh(self):
        now = time.monotonic()
        return (
            self._latest_frame is not None
            and now - self._capture_at < 1
            and now - self._hud_at < 0.5
        )

    def _foreground(self):
        check = getattr(self.capture, "can_input", None)
        return True if check is None else check() is True

    def can_execute(self, c):
        if c.action_type == "STOP":
            return True
        if (
            self._paused
            or c.decision_epoch != self._epoch
            or not self._fresh()
            or not self._foreground()
        ):
            return False
        hud = self._latest_hud_state
        if (
            not self._hud_ready
            or not hud.health_valid
            or hud.health is None
            or hud.health <= 0
        ):
            return False
        if c.action_type == "USE_POTION":
            return hud.health < self._emergency_hp_threshold
        if time.monotonic() - self._yolo_at > max(0.75, self.yolo.interval * 2):
            return False
        if c.track_id is not None:
            obj = next((o for o in self._objects if o.track_id == c.track_id), None)
            if (
                obj is None
                or obj.status != "confirmed"
                or obj.semantic_confidence < 0.6
            ):
                return False
            if c.action_type == "ATTACK" and (
                obj.object_type != "monster" or obj.relation != "hostile"
            ):
                return False
            if c.action_type == "TAKE" and (
                obj.object_type != "item"
                or not self._pickup_enabled
                or not self.policy.entry(obj, self._docs).get("pickup", True)
            ):
                return False
        return True

    async def _capture_loop(self):
        loop = asyncio.get_running_loop()
        while self._running:
            start = time.monotonic()
            try:
                epoch = self._epoch
                captured = await loop.run_in_executor(
                    self._capture_executor, self.capture.grab
                )
                if epoch != self._epoch:
                    continue
                if captured is not None:
                    identity = (captured.source, tuple(captured.image.shape[:2]))
                    if (
                        self._frame_identity is not None
                        and identity != self._frame_identity
                    ):
                        self._epoch += 1
                        self.resolver.reset()
                        self._objects = []
                        self._latest_yolo_objects = []
                        self._hud_ready = False
                        self._latest_hud_state = HUDState()
                        self._hud_scan_complete = False
                        self._set_hud_regions(self._docs["hud.json"]["regions"])
                    self._frame_identity = identity
                    self._latest_captured = captured
                    self._latest_frame = captured.image
                    self._frame_seq += 1
                    self._capture_at = time.monotonic()
                    self._capture_samples.append(self._capture_at)
            except Exception:
                log.exception("[CAPTURE] failed")
            await asyncio.sleep(
                max(0.001, 1 / self.capture_fps - (time.monotonic() - start))
            )

    async def _hud_loop(self):
        last = -1
        loop = asyncio.get_running_loop()
        while self._running:
            start = time.monotonic()
            if self._latest_frame is not None and self._frame_seq != last:
                last = self._frame_seq
                try:
                    epoch = self._epoch
                    hud = await loop.run_in_executor(
                        self._hud_executor, self._analyze_hud, self._latest_frame
                    )
                    if epoch != self._epoch:
                        continue
                    self._latest_hud_state = hud
                    self._hud_ms = (time.monotonic() - start) * 1000
                    self._hud_at = time.monotonic()
                    if hud.health_valid:
                        self._hud_failures = 0
                    else:
                        self._hud_failures += 1
                        if self._hud_failures >= 10:
                            self._hud_ready = False
                            self._hud_scan_complete = False
                    if (
                        not self._paused
                        and self._hud_ready
                        and hud.health_valid
                        and hud.health is not None
                        and 0 < hud.health < self._emergency_hp_threshold
                        and start - self._last_potion_submit >= 1
                    ):
                        await self.scheduler.submit_emergency(
                            command(
                                "USE_POTION",
                                source="HP_EMERGENCY",
                                reason=f"hp={hud.health:.1f}% OpenCV",
                                epoch=self._epoch,
                            )
                        )
                        self._last_potion_submit = start
                except Exception:
                    log.exception("[OpenCV] HUD worker failed")
            await asyncio.sleep(
                max(0.001, self.hud_interval - (time.monotonic() - start))
            )

    def _resolve_frame(self, frame, detections, settings):
        objects = self.resolver.resolve(detections, frame)
        mask = minimap_mask(frame, settings)
        return objects, mask

    def _detect_frame(self, frame):
        with self._detector_lock:
            return self.yolo.detect(frame)

    def _prepare_live_vision(self, document):
        values = dict(document["yolo"])
        values["model_path"] = str(PROJECT_ROOT / values["model_path"])
        keys = ("enabled", "model_path", "device", "image_size")
        if any(getattr(self.yolo, key) != values[key] for key in keys):
            try:
                candidate = YOLOSensor(**values)
                if candidate.enabled:
                    candidate.detect(np.zeros((64, 64, 3), dtype=np.uint8))
                return candidate
            except Exception as exc:
                raise ValueError(f"YOLO 설정 적용 실패; 기존 설정 유지: {exc}") from exc
        return None

    def _apply_live_vision(self, document, prepared=None):
        replacement = (
            prepared if prepared is not None else self._prepare_live_vision(document)
        )
        with self._detector_lock:
            if replacement is not None:
                self.yolo = replacement
            values = document["yolo"]
            for key in (
                "confidence",
                "image_size",
                "interval",
                "max_detections",
                "roi_x",
                "roi_y",
                "roi_width",
                "roi_height",
            ):
                setattr(self.yolo, key, values[key])
            self.yolo.raw_confidence = min(0.1, self.yolo.confidence)
            self.yolo._last_run = 0
            self.yolo._cache = []
        self.capture_fps = document.get("capture_fps", self.capture_fps)
        titles = document["window_titles"]
        if (
            hasattr(self.capture, "window_titles")
            and self.capture.window_titles != titles
        ):
            self.capture.window_titles = list(titles)
            if hasattr(self.capture, "_invalidate_window"):
                self.capture._invalidate_window()
            self._latest_frame = None
            self._capture_at = 0
            self._hud_ready = False
            self._frame_identity = None
        self.resolver.class_rules = document.get("class_rules", {})
        self.resolver.reset()
        self._motion_image = None

    async def commit_profile_operations(self, operations, revisions):
        prepared = {}

        def check(changes):
            if "vision.json" in changes:
                prepared["vision"] = self._prepare_live_vision(changes["vision.json"])

        saved = await asyncio.to_thread(self.store.apply, operations, revisions, check)
        await self.reload_web_settings(prepared)
        return saved

    async def reload_web_settings(self, prepared=None):
        docs, revisions = await asyncio.to_thread(self.store.snapshot)
        old = self._docs
        if docs["vision.json"] != old["vision.json"]:
            await asyncio.to_thread(
                self._apply_live_vision,
                docs["vision.json"],
                (prepared or {}).get("vision"),
            )
        if docs["hud.json"] != old["hud.json"]:
            self._set_hud_regions(docs["hud.json"]["regions"])
            self._hud_ready = False
            self._hud_scan_complete = docs["hud.json"]["calibration"]["validated"]
            self._last_hud_attempt = -1e9
        if docs["object_memory.json"] != old["object_memory.json"]:
            await asyncio.to_thread(self.resolver.reset)
        self._docs, self._revisions = docs, revisions
        self._objects = []
        self._latest_yolo_objects = []
        self._yolo_at = 0
        self._refresh_profile_policy()
        self._epoch += 1

    def emit_web_event(self, kind, **data):
        self._web_event_seq += 1
        self._web_events.append(
            {"seq": self._web_event_seq, "type": kind, "time": time.time(), **data}
        )

    def web_snapshot(self):
        now = time.monotonic()
        hud = self._latest_hud_state
        samples = [v for v in self._capture_samples if now - v <= 3]
        fps = (
            (len(samples) - 1) / (samples[-1] - samples[0])
            if len(samples) > 1 and samples[-1] > samples[0] and now - samples[-1] < 1
            else None
        )
        yolo_ms = getattr(self.yolo, "last_inference_ms", 0)
        yolo_fresh = now - self._yolo_at < max(0.75, self.yolo.interval * 2)
        hud_fresh = now - self._hud_at < 0.5 and self._hud_ready
        latest = getattr(
            getattr(self.scheduler, "executor", None), "last_command", None
        )
        vision = self._docs["vision.json"]
        y = vision["yolo"]
        restart = any(
            getattr(self.yolo, k, None) != y.get(k) for k in ("enabled", "device")
        ) or str(self.yolo.model_path) != str(PROJECT_ROOT / y["model_path"])
        return {
            "api_version": 1,
            "instance": getattr(self, "_web_instance", None),
            "profile": self.profile.name,
            "running": self._running,
            "paused": self._paused,
            "foreground": self._foreground(),
            "hud_ready": self._hud_ready,
            "capture_fresh": now - self._capture_at < 1,
            "input_backend": type(
                getattr(getattr(self.scheduler, "executor", None), "_input", None)
            ).__name__,
            "restart_required": restart,
            "hud": {
                "health": hud.health if hud.health_valid and hud_fresh else None,
                "mp": hud.mp if hud.mp_valid and hud_fresh else None,
                "sp": hud.sp if hud.sp_valid and hud_fresh else None,
            },
            "action": latest.action_type if latest else None,
            "directive": self._directive,
            "metrics": {
                "fps": round(fps, 1) if fps is not None else None,
                "hud_ms": self._hud_ms if hud_fresh else None,
                "yolo_ms": yolo_ms if yolo_fresh and yolo_ms > 0 else None,
                "latency": (
                    self._hud_ms + yolo_ms
                    if hud_fresh
                    and yolo_fresh
                    and self._hud_ms is not None
                    and yolo_ms > 0
                    else None
                ),
                "vl": self._last_vl_ms,
                "gpu": None,
                "gated_frames": self._gated_frames,
            },
            "settings": {
                "fps": self.capture_fps,
                "interval": self.yolo.interval,
                "size": self.yolo.image_size,
                "x": self.yolo.roi_x * 100,
                "y": self.yolo.roi_y * 100,
                "w": self.yolo.roi_width * 100,
                "h": self.yolo.roi_height * 100,
                "vlInterval": vision.get("learning_interval", 1),
                "gateY": vision.get("motion_gating", False),
                "gateVL": vision.get("semantic_gating", True),
            },
            "detector": {
                "enabled": self.yolo.enabled,
                "configured_model": y["model_path"],
                "active_model": getattr(
                    self.yolo, "active_model_path", self.yolo.model_path
                ),
                "confidence": self.yolo.confidence,
                "device": self.yolo.device,
                "raw": getattr(self.yolo, "raw_count", 0),
                "passed": getattr(self.yolo, "filtered_count", 0),
                "resolved": len(self._objects),
                "monsters": sum(
                    o.object_type == "monster" and o.relation == "hostile"
                    for o in self._objects
                ),
                "classes": getattr(self.yolo, "classes", {}),
                "error": getattr(self.yolo, "last_error", None),
            },
            "objects": (
                [
                    {
                        "track_id": o.track_id,
                        "memory_id": o.memory_id,
                        "type": o.object_type,
                        "relation": o.relation,
                        "status": o.status,
                        "confidence": o.semantic_confidence,
                        "bbox": o.bbox,
                    }
                    for o in self._objects
                ]
                if yolo_fresh
                else []
            ),
            "events": list(self._web_events),
        }

    async def _yolo_loop(self):
        last = -1
        loop = asyncio.get_running_loop()
        next_run = 0
        while self._running:
            now = time.monotonic()
            if (
                self._latest_frame is not None
                and self._frame_seq != last
                and now >= next_run
            ):
                last = self._frame_seq
                next_run = now + self.yolo.interval
                frame = self._latest_frame
                if self._docs["vision.json"].get("motion_gating", False):
                    motion = cv2.cvtColor(
                        cv2.resize(frame, (80, 45)), cv2.COLOR_BGR2GRAY
                    )
                    still = (
                        self._motion_image is not None
                        and float(cv2.absdiff(motion, self._motion_image).mean()) < 2
                    )
                    self._motion_image = motion
                    if still and now - self._last_yolo_inference < 0.5:
                        self._gated_frames += 1
                        await asyncio.sleep(0.01)
                        continue
                epoch = self._epoch
                try:
                    detections = await loop.run_in_executor(
                        self._yolo_executor, self._detect_frame, frame
                    )
                    self._last_yolo_inference = time.monotonic()
                    objects, mask = await loop.run_in_executor(
                        self._yolo_executor,
                        self._resolve_frame,
                        frame,
                        detections,
                        self._docs["navigation.json"],
                    )
                    if epoch == self._epoch:
                        if (
                            self.resolver.changed
                            and not self._profile_write_in_progress
                        ):
                            self._epoch += 1
                        self._latest_yolo_objects = detections
                        self._objects = objects
                        self._minimap_mask = mask
                        self._yolo_at = time.monotonic()
                except Exception as exc:
                    self.yolo.last_error = str(exc)
                    log.exception("[YOLO] failed")
                    self._objects = []
                    self._latest_yolo_objects = []
            await asyncio.sleep(0.01)

    async def _calibration_loop(self):
        while self._running:
            if (
                self._latest_frame is None
                or (self._hud_ready and self._hud_scan_complete)
                or time.monotonic() - self._last_hud_attempt < self._hud_retry_interval
            ):
                await asyncio.sleep(0.1)
                continue
            self._last_hud_attempt = time.monotonic()
            frame = self._latest_frame
            identity = self._frame_identity
            try:
                _, revisions = await asyncio.to_thread(self.store.snapshot)
                cached = self._docs["hud.json"]
                if self._hud_scan_complete and await asyncio.to_thread(
                    self._validate_hud, frame
                ):
                    self._hud_ready = True
                    continue
                async with self._vl_lock:
                    area = await asyncio.to_thread(
                        self.vl.calibrate_hud_area,
                        frame,
                        self._latest_captured.source,
                        self.profile.hud_calibration_context,
                    )
                    result = await asyncio.to_thread(
                        self.vl.discover_hud,
                        frame,
                        self._latest_captured.source,
                        self.profile.hud_prompt_context,
                    )
                if identity != self._frame_identity:
                    continue
                regions = result.data
                # Reject malformed model geometry before touching the live sensors.
                from app.profiles.profile_store import validate_document

                candidate = {
                    "version": 1,
                    "hud_bbox": area.data.get("hud_bbox"),
                    "regions": regions,
                    "calibration": {
                        "resolution": [frame.shape[1], frame.shape[0]],
                        "validated": False,
                        "source": "qwen+opencv",
                    },
                }
                validate_document("hud.json", candidate)
                self._set_hud_regions(regions)
                valid = await asyncio.to_thread(self._validate_hud, self._latest_frame)
                self._hud_ready = valid
                if valid:
                    with self._profile_lock:
                        export = getattr(
                            self.profile, "export_hud_regions", lambda: regions
                        )()
                    candidate["regions"] = export or regions
                    candidate["calibration"]["validated"] = True
                    ops = [
                        {
                            "file": "hud.json",
                            "op": "replace",
                            "path": "/" + k,
                            "value_json": json.dumps(candidate[k], ensure_ascii=False),
                        }
                        for k in ("hud_bbox", "regions", "calibration")
                    ]
                    await asyncio.to_thread(self.store.apply, ops, revisions)
                    self._docs["hud.json"] = candidate
                    self._hud_scan_complete = True
                    log.info(
                        "[HUD] OpenCV validated; normalized regions saved to profile"
                    )
            except (VLResponseError, ValueError, OSError) as exc:
                self._log_vl_error("HUD", exc)
            except Exception:
                log.exception("[HUD] calibration failed")
            await asyncio.sleep(0.1)

    async def _learning_loop(self):
        while self._running:
            if (
                self._paused
                or time.monotonic() - self._learning_at
                < self._docs["vision.json"].get("learning_interval", 1)
                or self._latest_frame is None
                or time.monotonic() < self._vl_backoff_until
            ):
                await asyncio.sleep(0.1)
                continue
            vision = self._docs["vision.json"]
            tracks, revision, semantic_frame = self.resolver.candidates(
                self._objects,
                only_unknown=vision.get("semantic_gating", True),
                retry_interval=vision.get("learning_interval", 1),
            )
            if not tracks:
                await asyncio.sleep(0.1)
                continue
            frame = semantic_frame.copy()
            epoch = self._epoch
            source = self._latest_captured.source
            payload = {
                "yolo_objects": [
                    {
                        "index": i,
                        "bbox": [
                            t.detection.x1,
                            t.detection.y1,
                            t.detection.x2,
                            t.detection.y2,
                        ],
                        "detector_class": t.detection.class_name,
                        "needs_classification": True,
                    }
                    for i, t in enumerate(tracks)
                ]
            }
            try:
                async with self._vl_lock:
                    if epoch != self._epoch:
                        continue
                    result = await asyncio.to_thread(
                        self.vl.classify_objects,
                        frame,
                        source,
                        json.dumps(payload),
                        self._game_knowledge_prompt,
                    )
                    self._learning_at = time.monotonic()
                    self._last_vl_ms = result.elapsed_ms
                if epoch != self._epoch:
                    continue
                accepted = await asyncio.to_thread(
                    self.resolver.accept, tracks, frame, result.data, revision
                )
                for mid, label, status in accepted:
                    log.info(f"[LEARN] MEM={mid} {label} status={status}")
                    self.emit_web_event(
                        "object_learned", memory_id=mid, label=label, status=status
                    )
                self._vl_error_active = False
            except (VLResponseError, ValueError, OSError) as exc:
                self._vl_backoff_until = time.monotonic() + self._vl_backoff_seconds
                self._log_vl_error("OBJECT", exc)
            except Exception:
                log.exception("[LEARN] failed")
            await asyncio.sleep(0.05)

    async def _refresh_loop(self):
        while self._running:
            if self._profile_write_in_progress:
                await asyncio.sleep(0.05)
                continue
            try:
                docs, revisions = await asyncio.to_thread(self.store.snapshot)
                if self._profile_write_in_progress:
                    continue
                changed = any(
                    docs.get(k) != self._docs.get(k)
                    for k in docs
                    if k != "object_memory.json"
                )
                if changed:
                    if docs["vision.json"] != self._docs["vision.json"]:
                        await asyncio.to_thread(
                            self._apply_live_vision, docs["vision.json"]
                        )
                        log.info("[PROFILE] runtime tuning/model/device/window applied")
                    if docs["hud.json"] != self._docs["hud.json"]:
                        self._set_hud_regions(docs["hud.json"]["regions"])
                        self._hud_ready = False
                        self._hud_scan_complete = docs["hud.json"]["calibration"][
                            "validated"
                        ]
                        self._last_hud_attempt = -1e9
                    self._docs = docs
                    self._revisions = revisions
                    self._refresh_profile_policy()
                    self._epoch += 1
            except (ValueError, OSError) as exc:
                log.warning(f"[PROFILE] edit rejected; keeping last settings: {exc}")
            await asyncio.sleep(0.5)

    async def _action_loop(self):
        while self._running:
            emergency = getattr(self.capture, "emergency_stop_pressed", None)
            if emergency is not None and emergency() is True:
                await self.handle_control("/stop")
            now = time.monotonic()
            if self._directive and now >= self._directive_until:
                self._directive = None
                # A finite user move/target command does not silently resume hunting.
                self._paused = True
                self._epoch += 1
                await self.scheduler.submit_emergency(
                    command("STOP", source="USER_TTL", epoch=self._epoch)
                )
            if (
                self._paused
                or not self._fresh()
                or not self._foreground()
                or not self._hud_ready
            ):
                c = command("STOP", reason="PAUSED_OR_NO_FRESH_HUD", epoch=self._epoch)
            else:
                objects = (
                    self._objects
                    if now - self._yolo_at <= max(0.75, self.yolo.interval * 2)
                    else []
                )
                c = self.policy.decide(
                    objects,
                    self._latest_hud_state,
                    self._docs,
                    self._latest_frame.shape,
                    self._directive,
                    self._epoch,
                    potion_ready=self.scheduler._is_cooldown_ready(
                        command("USE_POTION")
                    ),
                )
                if c.action_type in {"MOVE", "DODGE"} and c.direction is not None:
                    step = self.navigator.choose(
                        c.direction,
                        objects,
                        self._latest_frame.shape,
                        self._docs["navigation.json"],
                        self._minimap_mask,
                    )
                    if step is None:
                        c = command("STOP", reason="PATH_BLOCKED", epoch=self._epoch)
                    else:
                        c = replace(
                            c,
                            direction=step[0],
                            target=step[1],
                            duration_ms=int(self._docs["navigation.json"]["step_ms"]),
                        )
            if c.action_type == "USE_POTION":
                await self.scheduler.submit_emergency(c)
            else:
                await self.scheduler.submit(c)
            if now - self._last_summary_time >= self._summary_interval:
                self._report(c)
                self._last_summary_time = now
            await asyncio.sleep(self.reaction_interval)

    def _report(self, c):
        hud = self._latest_hud_state
        hp = "N/A" if not hud.health_valid else f"{hud.health:.1f}%"
        executed = getattr(
            getattr(self.scheduler, "executor", None), "last_command", None
        )
        last = executed.action_type if executed else "NONE"
        log.info(
            f"[GAME] HP={hp} MP={hud.mp} SP={hud.sp} paused={self._paused} [AI] requested={c.action_type} executed={last}"
        )
        if (
            any(
                o.object_type == "monster" and o.relation == "hostile"
                for o in self._objects
            )
            or c.action_type != self._last_report_action
        ):
            h, w = (
                self._latest_frame.shape[:2]
                if self._latest_frame is not None
                else (1, 1)
            )
            for o in self._objects:
                x, y = (o.bbox[0] + o.bbox[2]) / 2 - w / 2, (
                    o.bbox[1] + o.bbox[3]
                ) / 2 - h / 2
                log.info(
                    f"[OBJECT] TRACK={o.track_id} MEM={o.memory_id} {o.object_type}/{o.relation} {o.status} conf={o.semantic_confidence:.2f} pos=({x:.0f},{y:.0f})"
                )
        self._last_report_action = c.action_type

    async def handle_control(self, message):
        value = message.strip().lower()
        if value in {"/stop", "/pause", "멈춰", "정지", "중지", "stop", "/quit"}:
            self._epoch += 1
            self._paused = True
            self._directive = None
            await self.scheduler.submit_emergency(
                command("STOP", source="USER_STOP", epoch=self._epoch)
            )
            print("[CONTROL] stopped; pending model commands invalidated")
            self.emit_web_event("control", action="stop")
            if value == "/quit":
                self._running = False
            return True
        if value in {"/resume", "계속", "다시 시작", "resume"}:
            self._epoch += 1
            self._paused = False
            self._directive = None
            print("[CONTROL] automatic profile reactions resumed")
            self.emit_web_event("control", action="resume")
            return True
        if value == "/status":
            self._report(command("STOP"))
            print("[PROFILE] " + str(self.profile.profile_dir))
            return True
        return False

    def _start_console(self):
        loop = asyncio.get_running_loop()

        def read():
            while self._running:
                try:
                    message = input("나> ")
                except (EOFError, KeyboardInterrupt):
                    message = "/quit"
                try:
                    loop.call_soon_threadsafe(self._console_queue.put_nowait, message)
                except RuntimeError:
                    return
                if message == "/quit":
                    return

        threading.Thread(target=read, name="vga-dialogue", daemon=True).start()
        print(
            "[CHAT] /stop 즉시 중단, /resume 자동 반응, /status, /quit | F8 즉시 중단"
        )

    async def _console_loop(self):
        while self._running:
            message = await self._console_queue.get()
            if await self.handle_control(message):
                continue
            if not message.strip():
                continue
            try:
                self._chat_queue.put_nowait((message, self._epoch))
            except asyncio.QueueFull:
                print("[CHAT] 처리 중인 요청이 많습니다. 잠시 후 다시 입력하세요.")

    async def _conversation_loop(self):
        while self._running:
            queued = await self._chat_queue.get()
            message, epoch = queued[:2]
            request_id = queued[2] if len(queued) > 2 else None
            try:
                if epoch != self._epoch:
                    self.emit_web_event(
                        "chat_cancelled",
                        request_id=request_id,
                        message="중단 또는 설정 변경으로 취소했습니다.",
                    )
                    continue
                async with self._vl_lock:
                    if epoch != self._epoch:
                        self.emit_web_event(
                            "chat_cancelled",
                            request_id=request_id,
                            message="중단 또는 설정 변경으로 취소했습니다.",
                        )
                        continue
                    frame = (
                        self._latest_frame.copy()
                        if self._latest_frame is not None
                        else None
                    )
                    snapshot = list(self._objects)
                    result = await asyncio.to_thread(
                        self.conversation.propose,
                        message,
                        frame,
                        snapshot,
                        self._latest_hud_state,
                    )
                    if result.get("elapsed_ms") is not None:
                        self._last_vl_ms = result["elapsed_ms"]
                if epoch != self._epoch:
                    self.emit_web_event(
                        "chat_cancelled",
                        request_id=request_id,
                        message="늦게 도착한 지시를 폐기했습니다.",
                    )
                    print("[CHAT] 중단/화면/설정 변경 후 도착한 제안을 폐기했습니다.")
                    continue
                # Recheck live targets before saving any accompanying patch.
                directive = result["directive"]
                from app.ai.game_conversation import validate_directive

                validate_directive(directive, self._objects)
                self._profile_write_in_progress = True
                prepared = {}

                def check(changes):
                    if "vision.json" in changes:
                        prepared["vision"] = self._prepare_live_vision(
                            changes["vision.json"]
                        )

                saved = await asyncio.to_thread(
                    self.store.apply, result["operations"], result["revisions"], check
                )
                print("Qwen-VL> " + result["reply"])
                self.emit_web_event(
                    "chat_result",
                    request_id=request_id,
                    reply=result["reply"],
                    saved=saved,
                    directive=directive,
                )
                if saved:
                    print("[SAVED] " + ", ".join(saved))
                if epoch != self._epoch:
                    continue
                if saved:
                    docs, revisions = await asyncio.to_thread(self.store.snapshot)
                    if epoch != self._epoch:
                        continue
                    self._docs = docs
                    self._revisions = revisions
                    self._refresh_profile_policy()
                    if "object_memory.json" in saved:
                        await asyncio.to_thread(self.resolver.reset)
                        self._objects = []
                    if "hud.json" in saved:
                        self._set_hud_regions(docs["hud.json"]["regions"])
                        self._hud_ready = False
                        self._hud_scan_complete = docs["hud.json"]["calibration"][
                            "validated"
                        ]
                        self._last_hud_attempt = -1e9
                    if "vision.json" in saved:
                        await asyncio.to_thread(
                            self._apply_live_vision,
                            docs["vision.json"],
                            prepared.get("vision"),
                        )
                    self._epoch += 1
                action = directive["action"]
                if action == "STOP":
                    await self.handle_control("/stop")
                elif action == "RESUME":
                    await self.handle_control("/resume")
                elif action != "NONE":
                    self._epoch += 1
                    self._directive = directive
                    self._directive_until = time.monotonic() + directive["ttl_seconds"]
                    self._paused = False
                    print(
                        f"[STAGED] {action} track={directive['track_id']} TTL={directive['ttl_seconds']}s; 최신 센서 검증 후 실행"
                    )
            except (VLResponseError, ValueError, OSError) as exc:
                self.emit_web_event(
                    "chat_error", request_id=request_id, message=str(exc)
                )
                print(f"[CHAT] 적용하지 않았습니다: {exc}")
            except Exception:
                self.emit_web_event(
                    "chat_error",
                    request_id=request_id,
                    message="대화 처리에 실패했습니다. Agent 로그를 확인하세요.",
                )
                log.exception("[CHAT] failed")
            finally:
                self._profile_write_in_progress = False
                self._chat_queue.task_done()

    async def run(self):
        self._running = True
        workers = [
            self._capture_loop,
            self._hud_loop,
            self._yolo_loop,
            self._action_loop,
            self._calibration_loop,
            self._learning_loop,
            self._refresh_loop,
            self._conversation_loop,
        ]
        if self._console_enabled:
            workers += [self._console_loop]
            self._start_console()
        self._tasks = [
            asyncio.create_task(worker(), name=worker.__name__) for worker in workers
        ]
        self._capture_task, self._hud_task, self._yolo_task = self._tasks[:3]
        try:
            while self._running:
                failed = next(
                    (
                        t
                        for t in self._tasks
                        if t.done() and not t.cancelled() and t.exception()
                    ),
                    None,
                )
                if failed:
                    raise failed.exception()
                await asyncio.sleep(0.1)
        finally:
            self.stop()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _log_vl_error(self, stage, exc):
        if not self._vl_error_active:
            log.warning(f"[QWEN-VL] {stage}: {exc}")
        self._vl_error_active = True

    def stop(self):
        self._running = False
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for task in self._tasks:
            if task is not current:
                task.cancel()
        self._capture_executor.shutdown(wait=False, cancel_futures=True)
        self._yolo_executor.shutdown(wait=False, cancel_futures=True)
        self._hud_executor.shutdown(wait=False, cancel_futures=True)

    # Optional legacy semantic Intent API, retained for callers; the fast loop never calls it.
    async def _decide_intent(self, objects, hud, captured):
        signature = (
            tuple((o.object_id, o.object_type, o.relation) for o in objects),
            (
                None
                if not hud.health_valid or hud.health is None
                else int(hud.health // 10)
            ),
            self._game_knowledge_prompt,
        )
        now = time.monotonic()
        fresh = (
            self._last_intent_data is not None
            and signature == self._last_intent_signature
            and now - self._last_intent_at < self.intent_cache_ttl
        )
        elapsed = 0.0
        if (
            not fresh
            and now - self._last_intent_attempt >= self.intent_interval
            and now >= self._vl_backoff_until
        ):
            self._last_intent_attempt = now
            facts = {
                "hud": {
                    "health": hud.health if hud.health_valid else None,
                    "mp": hud.mp if hud.mp_valid else None,
                    "sp": hud.sp if hud.sp_valid else None,
                    "buffs": hud.buffs,
                },
                "objects": [
                    {
                        "index": o.object_id,
                        "semantic": o.object_type,
                        "relation": o.relation,
                        "confidence": o.semantic_confidence,
                        "bbox": o.bbox,
                    }
                    for o in objects
                ],
            }
            try:
                result = await asyncio.to_thread(
                    self.vl.decide_intent,
                    captured.image,
                    captured.source,
                    json.dumps(facts, ensure_ascii=False),
                    self._game_knowledge_prompt,
                )
                proposal = Intent.from_vl(result.data)
                allowed = {
                    "ENGAGE",
                    "MOVE",
                    "INTERACT",
                    "RETREAT",
                    "USE_RESOURCE",
                    "EXPLORE",
                    "WAIT",
                }
                if proposal.action not in allowed or not isinstance(
                    result.data.get("scene"), dict
                ):
                    raise VLResponseError("invalid scene/Intent data")
                if proposal.target_index is not None and (
                    type(proposal.target_index) is not int
                    or not any(o.object_id == proposal.target_index for o in objects)
                ):
                    raise VLResponseError("Intent target not present in sensor facts")
                self._last_intent_data = result.data
                self._last_intent_signature = signature
                self._last_intent_at = time.monotonic()
                elapsed = result.elapsed_ms
                fresh = True
            except (VLResponseError, ValueError, TypeError) as exc:
                self._last_intent_data = None
                self._log_vl_error(
                    "INTENT",
                    (
                        exc
                        if isinstance(exc, VLResponseError)
                        else VLResponseError(str(exc))
                    ),
                )
                self._vl_backoff_until = time.monotonic() + self._vl_backoff_seconds
        if not fresh:
            return (
                {"type": "unknown", "confidence": 0.0},
                Intent(action="WAIT", confidence=1.0),
                elapsed,
            )
        data = self._last_intent_data
        scene = data.get("scene", {})
        intent = Intent.from_vl(data)
        if scene.get("type") in {"menu", "loading", "dead", "dialog"}:
            intent = Intent(action="WAIT", confidence=1.0)
        # A model call can take seconds. Re-check the proposed target after it returns.
        if intent.target_index is not None:
            target = next(
                (o for o in objects if o.object_id == intent.target_index), None
            )
            if target is None or not self._is_present_now(target.bbox):
                intent = Intent(action="WAIT", confidence=1.0)
            elif intent.action == "ENGAGE" and (
                target.object_type != "monster" or target.relation != "hostile"
            ):
                intent = Intent(action="WAIT", confidence=1.0)
            elif (
                intent.action == "INTERACT"
                and target.object_type == "item"
                and not self._pickup_enabled
            ):
                intent = Intent(action="WAIT", confidence=1.0)
        return scene, intent, elapsed

    @staticmethod
    def _bbox_iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / float(area_a + area_b - inter)

    def _is_present_now(self, bbox, min_iou: float = 0.20) -> bool:
        """Require the delayed semantic object to still exist in latest YOLO."""
        return any(
            self._bbox_iou(bbox, current.bbox) >= min_iou
            for current in self._latest_yolo_objects
        )
