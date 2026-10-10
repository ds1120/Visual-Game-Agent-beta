from __future__ import annotations
import asyncio
import copy
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
from app.core.control_hotkeys import watch_control_hotkeys
from app.core.game_statistics import GameStatistics
from app.vision.buff_monitor import BuffMonitor
from app.vision.qwen_vl_client import QwenVLClient, VLResponseError
from app.vision.scene_source import SceneSource
from app.core.logger import get_logger
from app.profiles.registry import create_game_profile
from app.profiles.profile_store import ProfileStore, migrate_legacy_memory, PROJECT_ROOT, PROFILE_ROOT, atomic_json
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
        from app.vision.bar_hud_profile import BarHUDProfile
        self.profile = BarHUDProfile(self.profile)
        self.profile_root = PROFILE_ROOT
        self.store = ProfileStore(self.profile.profile_dir)
        migrate_legacy_memory(self.profile.profile_dir)
        ensure_runtime_settings(self.profile.profile_dir)
        self._docs, self._revisions = self.store.snapshot()
        self.statistics = GameStatistics(self.profile.profile_dir)
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
        from app.core.navigation_monitor import NavigationMonitor
        self.navigation_monitor = NavigationMonitor()
        from app.core.combat_guard import CombatGuard
        self.combat_guard = CombatGuard()
        from app.vision.combat_feedback import CombatFeedback
        self.combat_feedback = CombatFeedback(PROJECT_ROOT)
        self._skill_feedback = {}
        self._attack_track_id = None
        self._minimap_at = 0
        self._nav_player = None
        self._nav_direction = None
        self._nav_direction_at = 0
        self._nav_qwen_at = -1e9
        self._latest_frame = None
        self._latest_captured = None
        self._preview_captured = None
        self._preview_at = 0
        self._focus_paused = False
        self._focus_was_active = False
        self._focus_resume_state = None
        self._focus_stopping = False
        self._frame_seq = 0
        self._capture_at = 0
        self._frame_identity = None
        self._latest_hud_state = HUDState()
        self._hud_at = 0
        self._hud_ready = False
        self._hud_failures = 0
        self._hud_missing_at = 0
        self._hud_probe_attempted = False
        self._hud_probe_command = None
        self._hud_probe_done = None
        self._hud_probe_frame_floor = -1
        self._semantic_wait_since = None
        self._hud_retry_interval = max(1, float(hud_retry_interval))
        self._last_hud_attempt = -1e9
        self._last_potion_submit = 0
        self._hud_scan_complete = self._docs["hud.json"]["calibration"]["validated"]
        self._set_hud_regions(self._docs["hud.json"]["regions"])
        self.yolo = SceneSource(**(yolo_config or {"enabled": False}))
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
        self._processing_halted = False
        self._halt_reason = None
        self._hud_rechecking = False
        self._hud_waiting = False
        self._hud_recheck_attempted = False
        self._hud_recheck_status = None
        self._hud_occlusion_grace = 1.2
        self.buff_monitor = BuffMonitor(self.profile.profile_dir)
        self._buff_preview = None
        self._buff_at = 0
        self._path_blocked_since = 0
        self._minimap_unusable = False
        self._hunt_active = False
        self._hunt_heading = 0
        self._hunt_turn_at = time.monotonic()
        self._last_requested_command = None
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
            executor.result_observer = self._record_input

    def _record_input(self, c, status, error=None):
        self.statistics.on_input(c, status, error)
        self.navigation_monitor.on_input(c, status, error)
        self.combat_guard.on_input(c, status, error)
        if c.source == 'BUFF_EXPIRED' and status == 'sent':
            self.buff_monitor.recast_sent()
        if c is self._hud_probe_command and self._hud_probe_done is not None:
            self._hud_probe_done.set()
            self.emit_web_event('hud_probe_result', status=status, error=error)

    async def _navigation_loop(self):
        while self._running:
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            if self._paused or self._processing_halted or self._hud_rechecking or not self._hud_ready or not self._fresh() or not self._foreground():
                self.navigation_monitor.reset()
                self._minimap_at = 0
                self._minimap_mask = None
                self._nav_direction = None
                await asyncio.sleep(.1)
                continue
            settings = copy.deepcopy(self._docs["navigation.json"])
            epoch = self._epoch
            frame = self._latest_frame.copy()
            player = self.navigation_monitor.update(frame, settings)
            mask = await asyncio.to_thread(minimap_mask, frame, settings)
            if epoch == self._epoch:
                self._nav_player = player
                self._minimap_unusable = bool(settings['minimap']['enabled'] and (mask is None or (mask > 0).mean() < .01))
                self._minimap_mask = None if self._minimap_unusable else mask
                self._minimap_at = 0 if self._minimap_unusable else time.monotonic()
                if self.navigation_monitor.stuck:
                    self._hunt_turn_at = 0
            await asyncio.sleep(.2)

    async def _navigation_replan_loop(self):
        while self._running:
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            now=time.monotonic()
            if (self._hunt_active and not self._paused and not self._hud_rechecking and not self._processing_halted
                and (self.navigation_monitor.stuck or self._minimap_unusable or self._path_blocked_since and now-self._path_blocked_since>=2) and self._fresh() and self._foreground() and self._hud_ready
                and now-self._nav_qwen_at>=10):
                self._nav_qwen_at=now
                epoch=self._epoch
                frame=self._latest_frame.copy()
                settings=self._docs["navigation.json"]["minimap"]
                message=("자동사냥 이동이 정체됐습니다. 설정된 미니맵의 정규화 0~1000 bbox="+json.dumps(settings["bbox"])+
                         ". 플레이어 좌표="+json.dumps(self._nav_player)+". 미니맵에서 연결된 통로를 보고 화면 기준 8방향 MOVE direction을 하나 제안하세요. "
                         "미니맵 ROI/통로 색상 설정이 실제 화면과 다를 수 있습니다. 미니맵을 확인할 수 없으면 게임 화면의 플레이어 주변 통행 가능한 바닥을 보고 짧은 화면 기준 이동 방향을 제안하세요. "
                         "벽을 클릭하지 말고 미니맵 회전을 고려하세요. 확인 불가면 NONE. operations는 비워 두세요.")
                try:
                    async with self._vl_lock:
                        result=await asyncio.to_thread(self.conversation.propose,message,frame,list(self._objects),self._latest_hud_state)
                    directive=result["directive"]
                    from app.ai.game_conversation import validate_directive
                    validate_directive(directive,self._objects)
                    if epoch==self._epoch and not self._paused and directive.get("action")=="MOVE" and directive.get("direction"):
                        self._nav_direction=tuple(directive["direction"])
                        self._nav_direction_at=time.monotonic()
                        self.emit_web_event("navigation_replanned",direction=self._nav_direction)
                except Exception as exc:
                    log.debug("[NAV] Qwen replan failed: %s",exc)
            await asyncio.sleep(.2)

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

    def _set_hud_regions(self, data, layout=None):
        with self._profile_lock:
            self.profile.layout = layout or self._docs["hud.json"].get("layout", "classic")
            self.profile.set_hud_regions(data)

    def _validate_hud(self, frame):
        with self._profile_lock:
            return self.profile.validate_hud(frame)

    def _analyze_hud(self, frame):
        with self._profile_lock:
            state = self.profile.analyze_hud(frame)
            now = time.monotonic()
            region = self._docs['hud.json']['regions'].get('buffs', {})
            if (state.health_valid or getattr(self,'startup_hud_only',False)) and self.buff_monitor.references and now-self._buff_at >= .5 and self.buff_monitor.crop(frame, region) is not None:
                if self.buff_monitor.require_all_absent and now-self._buff_at>=1:
                    self.buff_monitor.observe([],observable=set())
                state.buffs = self.buff_monitor.detect(frame, region)
                self.buff_monitor.observe(state.buffs,observable=self.buff_monitor.observable)
                self._observed_buffs = state.buffs
                self._buff_at = now
            else:
                state.buffs = list(getattr(self, '_observed_buffs', [])) if now-self._buff_at < 1 else []
            return state

    def _fresh(self):
        now = time.monotonic()
        return (
            self._latest_frame is not None
            and now - self._capture_at < 1
            and now - self._hud_at < 0.5
        )

    async def _halt_processing(self, reason="HP바를 찾지 못했습니다"):
        if self._processing_halted:
            return
        self.statistics.suspend()
        self.statistics.event("processing_halted", reason=reason)
        self._processing_halted = True
        self._halt_reason = reason
        self._paused = True
        self._hunt_active = False
        self._directive = None
        self._epoch += 1
        self._hud_ready = False
        self._latest_frame = None
        self._latest_captured = None
        self._latest_hud_state = HUDState()
        self._objects = []
        self._latest_yolo_objects = []
        self._minimap_mask = None
        self._capture_at = self._hud_at = self._yolo_at = 0
        self._capture_samples.clear()
        self._last_requested_command = command("STOP", source="HUD_LOST", epoch=self._epoch)
        await self.scheduler.submit_emergency(self._last_requested_command)
        self.emit_web_event("processing_halted", message=reason + ". 화면과 HUD 설정을 확인한 뒤 시작/재개하세요.")
        log.warning("[HALT] %s; capture/OpenCV/YOLO/Qwen stopped", reason)

    async def _try_hud_probe_move(self):
        """One bounded, explicitly authorized movement per missing-HP episode."""
        if (self._hud_probe_attempted or self._hud_probe_command is not None or self._paused or self._processing_halted
                or self._hud_rechecking or not self.scheduler.running
                or self._latest_frame is None or time.monotonic() - self._capture_at >= 1
                or not self._foreground()):
            return False
        hud = self._latest_hud_state
        if hud.health_valid and hud.health is not None and hud.health <= 0:
            return False
        self._hud_probe_attempted = True
        settings = copy.deepcopy(self._docs['navigation.json'])
        prepare = getattr(self, '_prepare_hud_probe', None)
        if prepare is not None:
            step = prepare(settings)
        else:
            settings['step_fraction'] = .08
            settings['minimap']['enabled'] = False
            step = self.navigator.choose((1, 0), self._objects, self._latest_frame.shape, settings)
        # HP loss also stops minimap updates. Use only a short screen-space
        # obstacle-checked step, not a route based on an unavailable map.
        if step is None:
            self.emit_web_event('hud_probe_skipped', message='HP 재탐색 이동 경로가 막혀 있습니다.')
            return False
        self._hud_waiting = True
        self._hud_ready = False
        c = command('MOVE', direction=step[0], target=step[1], duration_ms=120,
                    source='HUD_OCCLUSION_PROBE', reason='텍스트 가림 해소 후 HP 재측정', epoch=self._epoch)
        c = replace(c, expires_at=time.monotonic() + .8)
        self._hud_probe_command = c
        self._hud_probe_done = asyncio.Event()
        self._last_requested_command = c
        self.emit_web_event('hud_probe_move', message='짧게 한 번 이동한 뒤 최신 화면에서 HP를 다시 확인합니다.')
        try:
            await self.scheduler.submit_emergency(c)
            await asyncio.wait_for(self._hud_probe_done.wait(), timeout=.8)
        except asyncio.TimeoutError:
            self.emit_web_event('hud_probe_result', status='timeout')
        finally:
            self._hud_probe_frame_floor = self._frame_seq
            self._hud_probe_command = None
            self._hud_probe_done = None
            if self._epoch == c.decision_epoch:
                await self.scheduler.submit_emergency(command('STOP', source='HUD_PROBE_FINISHED', epoch=self._epoch))
        # Let capture collect the post-move image; do not immediately ask Qwen
        # about the same pre-move screenshot.
        await asyncio.sleep(.1)
        return True

    async def _recheck_hud(self, reason="HP바를 찾지 못했습니다"):
        if self._processing_halted or self._hud_rechecking or self._hud_probe_command is not None:
            return
        if getattr(self.profile, 'layout', 'classic') == 'bars_hp_sp_mp' and await self._try_hud_probe_move():
            return
        self.statistics.suspend()
        self.statistics.event("hp_recheck")
        self._hud_recheck_attempted = True
        self._hud_rechecking = True
        self._hud_recheck_status = "checking"
        self._hud_ready = False
        self._epoch += 1
        epoch, identity = self._epoch, self._frame_identity
        frame = self._latest_frame.copy() if self._latest_frame is not None else None
        previous = self._docs["hud.json"]
        self._last_requested_command = command("STOP", source="HUD_RECHECK", epoch=epoch)
        await self.scheduler.submit_emergency(self._last_requested_command)
        self.emit_web_event("hud_recheck", message="HP 측정 실패: 입력을 중단하고 Qwen으로 한 번 재확인합니다.")
        try:
            if frame is None:
                raise ValueError("재확인할 캡처 화면이 없습니다")
            _, revisions = await asyncio.to_thread(self.store.snapshot)
            source = getattr(self._latest_captured, "source", self.profile.name)
            context = getattr(self.profile, "hud_prompt_context", "Locate the player HP HUD") + "\nOpenCV failed to read the previous PLAYER HP region. Recheck whether the player HP HUD really exists in this screenshot, and correct its bbox. Do not confuse enemy bars with player HP. Previous regions: " + json.dumps(previous["regions"], ensure_ascii=False)
            async with self._vl_lock:
                result = await asyncio.to_thread(self.vl.discover_hud, frame, source, context)
            if self._processing_halted or epoch != self._epoch or identity != self._frame_identity:
                return
            self._last_vl_ms = result.elapsed_ms
            regions = copy.deepcopy(result.data)
            health = regions.get("health", {})
            if not health.get("visible") or health.get("confidence", 0) < .6:
                raise ValueError("Qwen 재확인에서도 플레이어 HP바를 확인하지 못했습니다")
            # Keep calibrated color ranges; model geometry alone cannot supply a numeric HP value.
            for name, region in regions.items():
                old = previous["regions"].get(name, {})
                if "hsv_ranges" in old:
                    region["hsv_ranges"] = old["hsv_ranges"]
            # HP recheck must not erase a manually configured buff strip or unrelated meters.
            for name, old in previous["regions"].items():
                if name != "health" and old.get("visible") and not regions.get(name, {}).get("visible"):
                    regions[name] = copy.deepcopy(old)
            candidate = {**previous, "regions": regions, "calibration": {"resolution": [frame.shape[1], frame.shape[0]], "validated": False, "source": "qwen-recheck+opencv"}}
            from app.profiles.profile_store import validate_document
            validate_document("hud.json", candidate)
            self._set_hud_regions(regions)
            valid = await asyncio.to_thread(self._validate_hud, frame)
            state = await asyncio.to_thread(self._analyze_hud, frame) if valid else HUDState()
            if epoch != self._epoch or identity != self._frame_identity or self._processing_halted:
                return
            if not valid or not state.health_valid or state.health is None:
                raise ValueError("Qwen이 HP 위치를 제안했지만 OpenCV 수치 측정이 실패했습니다")
            with self._profile_lock:
                candidate["regions"] = getattr(self.profile, "export_hud_regions", lambda: regions)() or regions
            candidate["calibration"]["validated"] = True
            operations = [{"file":"hud.json", "op":"replace", "path":"/"+key, "value_json":json.dumps(candidate[key],ensure_ascii=False)} for key in ("regions", "calibration")]
            self._profile_write_in_progress = True
            try:
                await asyncio.to_thread(self.store.apply, operations, revisions)
                docs, self._revisions = await asyncio.to_thread(self.store.snapshot)
                self._docs["hud.json"] = docs["hud.json"]
            finally:
                self._profile_write_in_progress = False
            self._latest_hud_state = state
            # The model may take seconds: require a newly measured capture before input resumes.
            self._hud_at = 0
            self._hud_ready = self._hud_scan_complete = True
            self._hud_recheck_status = "recovered"
            self.statistics.event("hp_recovered")
            self.emit_web_event("hud_recovered", message="Qwen으로 HP 위치를 보정하고 OpenCV 측정을 확인했습니다.")
        except Exception as exc:
            if epoch == self._epoch and not self._processing_halted:
                self._set_hud_regions(previous["regions"])
                self._hud_recheck_status = "failed"
                if getattr(self.profile, "layout", "classic") == "bars_hp_sp_mp":
                    self._hud_waiting = True
                    self._latest_hud_state = HUDState()
                    self.emit_web_event("hud_waiting", message="Qwen 확인 실패 · 입력을 차단하고 최신 화면에서 HP를 재탐색합니다. 15초 미검출 시 즉시 중단.")
                else:
                    await self._halt_processing(f"{reason} · {exc}")
        finally:
            if self._hud_recheck_status == "checking":
                self._hud_recheck_status = "cancelled"
            self._hud_rechecking = False

    def _restart_processing(self):
        if not self._processing_halted:
            return
        self._processing_halted = False
        self._halt_reason = None
        self._latest_frame = None
        self._frame_identity = None
        self._hud_ready = False
        self._hud_scan_complete = self._docs["hud.json"]["calibration"]["validated"]
        self._last_hud_attempt = -1e9
        self._hud_failures = 0
        self._hud_probe_attempted = False

    def _foreground(self):
        if callable(getattr(type(self.capture), 'is_foreground', None)):
            return self.capture.is_foreground() is True
        check = getattr(self.capture, "can_input", None)
        return True if check is None else check() is True

    def _focus_work_allowed(self):
        return not self._focus_paused and self._foreground()

    async def _check_focus_transition(self):
        foreground = self._foreground()
        auto_hunt = getattr(self,'focus_activation_starts_hunt',False) and not getattr(self,'_manual_control',False)
        activated = foreground and not self._focus_was_active
        lost = self._focus_was_active and not foreground
        self._focus_was_active = foreground
        if (lost or auto_hunt and not foreground) and not self._focus_paused:
            state = None if auto_hunt or self._paused or self._processing_halted else (self._hunt_active, self._directive, max(0, self._directive_until-time.monotonic()))
            self._focus_paused = True
            self._focus_resume_state = state
            self._focus_stopping = True
            try:
                if not getattr(self,'_manual_control',False):await self.handle_control('/stop')
            finally:
                self._focus_stopping = False
            self._capture_at = self._hud_at = self._yolo_at = 0
            self._hud_ready = False
            self._objects = []; self._latest_yolo_objects = []
            self._minimap_mask = None
            if hasattr(self, 'minimap_memory'):self.minimap_memory.suspend('foreground_wait')
            self.emit_web_event('focus_paused', message='게임 창 비활성 · 즉시 중단 · 활성화 시 자동 시작/재개' if auto_hunt else '게임 포커스 이탈 · 전체 일시정지 · 복귀 시 자동 재개')
            print('[FOCUS] 게임 창 비활성 · 캡처/OpenCV/Qwen/입력 즉시 중단')

        if foreground and (self._focus_paused or auto_hunt and activated):
            state = self._focus_resume_state
            self._focus_resume_state = None
            self._focus_paused = False
            if auto_hunt:
                await self.handle_control('이동' if getattr(self,'_move_only',False) else '/hunt')
                self.emit_web_event('focus_resumed', message='게임 창 활성화 · 자동사냥 시작/재개')
                print('[FOCUS] 게임 창 활성화 · 자동사냥 시작/재개')
            elif state is not None and not self._processing_halted:
                hunt, directive, remaining = state
                await self.handle_control('이동' if getattr(self,'_move_only',False) else '/hunt' if hunt else '/resume')
                self._directive = directive
                self._directive_until = time.monotonic()+remaining
                self.emit_web_event('focus_resumed', message='게임 포커스 복귀 · 자동 재개')
                print('[FOCUS] 게임 포커스 복귀 · 자동 재개')

    def can_execute(self, c):
        if c.action_type == "STOP":
            return True
        if c is self._hud_probe_command:
            hud = self._latest_hud_state
            return (c.action_type == 'MOVE' and c.duration_ms <= 120
                    and c.decision_epoch == self._epoch and not c.is_expired()
                    and self._hud_waiting and not self._paused and not self._processing_halted
                    and not self._hud_rechecking and self._latest_frame is not None
                    and time.monotonic() - self._capture_at < 1 and self._foreground()
                    and not (hud.health_valid and hud.health is not None and hud.health <= 0))
        if (
            self._paused or self._processing_halted or self._hud_rechecking
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
        if c.skill_id and (c.action_type != "USE_SKILL" or not any(s["id"] == c.skill_id and s["enabled"] for s in self._docs["input.json"].get("attack_skills", []))):
            return False
        if c.action_type == "USE_POTION":
            return hud.health < self._emergency_hp_threshold
        if c.source == 'BUFF_EXPIRED':
            return c.action_type == 'CAST_BUFF' and self.buff_monitor.pending_recast and time.monotonic()-self._buff_at < 1
        if time.monotonic() - self._yolo_at > max(0.75, self.yolo.interval * 2):
            return False
        if c.track_id is not None:
            if (c.action_type == "ATTACK" or c.skill_id) and not self.combat_guard.permits(c.track_id):
                return False
            obj = next((o for o in self._objects if o.track_id == c.track_id), None)
            if (
                obj is None
                or obj.status != "confirmed"
                or obj.semantic_confidence < 0.6
            ):
                return False
            if (c.action_type == "ATTACK" or c.skill_id) and self.combat_guard.is_blocked(obj):
                return False
            if (c.action_type == "ATTACK" or c.skill_id) and (
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
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            start = time.monotonic()
            try:
                epoch = self._epoch
                captured = await loop.run_in_executor(
                    self._capture_executor, self.capture.grab
                )
                if epoch != self._epoch or not self._focus_work_allowed():
                    continue
                if captured is not None:
                    identity = (captured.source, tuple(captured.image.shape[:2]))
                    if (
                        self._frame_identity is not None
                        and identity != self._frame_identity
                    ):
                        self._epoch += 1
                        self.statistics.suspend()
                        self.resolver.reset()
                        self.combat_guard.reset()
                        self._attack_track_id = None
                        self._objects = []
                        self._latest_yolo_objects = []
                        self._hud_ready = False
                        self._latest_hud_state = HUDState()
                        self._hud_scan_complete = False
                        self._hud_probe_attempted = False
                        self._set_hud_regions(self._docs["hud.json"]["regions"])
                    self._frame_identity = identity
                    self._latest_captured = captured
                    self._latest_frame = captured.image
                    self._frame_seq += 1
                    self._capture_at = time.monotonic()
                    # A screen-region capture can show a covering browser. Archive only the foreground game.
                    if self._foreground():
                        self._preview_captured = captured
                        self._preview_at = self._capture_at
                    self._capture_samples.append(self._capture_at)
            except Exception:
                log.exception("[CAPTURE] failed")
            await asyncio.sleep(
                max(0.001, 1 / self.capture_fps - (time.monotonic() - start))
            )

    async def _hud_read_loop(self):
        """Read HUD for display without stopping or changing movement inputs."""
        last = -1
        loop = asyncio.get_running_loop()
        while self._running:
            start = time.monotonic()
            if (self._focus_work_allowed() and not self._processing_halted
                    and self._latest_frame is not None and self._frame_seq != last):
                last = self._frame_seq
                epoch, identity = self._epoch, self._frame_identity
                try:
                    hud = await loop.run_in_executor(
                        self._hud_executor, self._analyze_hud, self._latest_frame
                    )
                    if (epoch == self._epoch and identity == self._frame_identity
                            and self._focus_work_allowed() and not self._processing_halted):
                        self._latest_hud_state = hud
                        self._hud_ms = (time.monotonic() - start) * 1000
                        self._hud_at = time.monotonic()
                        self._hud_ready = hud.health_valid
                        self._hud_waiting = not hud.health_valid
                except Exception:
                    self._hud_ready = False
                    self._hud_waiting = True
                    log.exception('[OpenCV] HUD read failed; movement continues')
            await asyncio.sleep(max(0.001, self.hud_interval - (time.monotonic() - start)))

    async def _hud_loop(self):
        last = -1
        loop = asyncio.get_running_loop()
        while self._running:
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            if self._processing_halted or self._hud_rechecking or self._hud_probe_command is not None or (not self._hud_ready and not self._hud_waiting):
                await asyncio.sleep(0.1)
                continue
            start = time.monotonic()
            if self._latest_frame is not None and self._frame_seq != last:
                if self._hud_probe_attempted and self._frame_seq <= self._hud_probe_frame_floor:
                    await asyncio.sleep(self.hud_interval)
                    continue
                last = self._frame_seq
                try:
                    epoch = self._epoch
                    hud = await loop.run_in_executor(
                        self._hud_executor, self._analyze_hud, self._latest_frame
                    )
                    if epoch != self._epoch or not self._focus_work_allowed():
                        continue
                    self._latest_hud_state = hud
                    self._hud_ms = (time.monotonic() - start) * 1000
                    self._hud_at = time.monotonic()
                    if hud.health_valid:
                        self._hud_ready = True
                        self._hud_waiting = False
                        self._hud_recheck_attempted = False
                        if self._hud_failures:
                            self.emit_web_event("hud_recovered", message="OpenCV가 최신 화면에서 HP 바를 다시 확인했습니다.")
                        self._hud_failures = 0
                        self._hud_missing_at = 0
                        self._hud_probe_attempted = False
                    else:
                        # Tiny overhead meters can be hidden by one animation or
                        # compression frame. Stop inputs immediately, then retry
                        # fresh captures briefly before paying for a VL request.
                        if getattr(self.profile, "layout", "classic") == "bars_hp_sp_mp":
                            self._hud_waiting = True
                            self._hud_ready = False
                            self._hud_failures += 1
                            if self._hud_failures == 1:
                                self._hud_missing_at = time.monotonic()
                                self._epoch += 1
                                self._last_requested_command = command("STOP", source="HUD_RECHECK", epoch=self._epoch)
                                await self.scheduler.submit_emergency(self._last_requested_command)
                                if not getattr(self,'startup_hud_only',False):
                                    self.emit_web_event("hud_retry", message="HP 미검출 · 입력을 멈추고 OpenCV로 다시 탐색합니다.")
                            if time.monotonic() - self._hud_missing_at < self._hud_occlusion_grace:
                                await asyncio.sleep(self.hud_interval)
                                continue
                            if await self._try_hud_probe_move():
                                continue
                        if self._hud_recheck_attempted:
                            await asyncio.sleep(self.hud_interval)
                            continue
                        await self._recheck_hud()
                        continue
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
                    await self._recheck_hud("HP 측정에 실패했습니다")
            await asyncio.sleep(
                max(0.001, self.hud_interval - (time.monotonic() - start))
            )

    def _resolve_frame(self, frame, detections, settings):
        self.resolver.friendly_names = {
            name.casefold().strip()
            for entry in self._docs.get('knowledge.json', {}).get('named_entities', [])
            if isinstance(entry, dict) and entry.get('relation') == 'friendly'
            for name in entry.get('names', []) if isinstance(name, str)
        }
        objects = self.resolver.resolve(detections, frame)
        if self.profile.name == 'diablo4':
            self.combat_feedback.confirm_red_bars(frame, objects, self._docs['vision.json'].get('red_enemy_bar'))
            self.combat_feedback.annotate(frame, objects, self._docs['vision.json'].get('enemy_health_bar'))
        return objects, None  # Minimap has its own CPU sampling loop.

    def _detect_frame(self, frame):
        with self._detector_lock:
            return self.yolo.detect(frame)

    def _prepare_live_vision(self, document):
        # The GameBot branch never constructs or warms a detector model.
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
            self._preview_captured = None
            self._preview_at = 0
            self._hud_ready = False
            self._frame_identity = None
        self.resolver.class_rules = document.get("class_rules", {})
        self.resolver.reset()
        self._motion_image = None

    async def switch_game(self, name):
        from app.vision.bar_hud_profile import BarHUDProfile
        profile=BarHUDProfile(create_game_profile(name,self.profile_root))
        ensure_runtime_settings(profile.profile_dir)
        store=ProfileStore(profile.profile_dir)
        docs,revisions=await asyncio.to_thread(store.snapshot)
        # Pause and invalidate every queued input/model result before replacing
        # any profile-bound state. A game switch never starts gameplay.
        await self.handle_control("/stop")
        self._processing_halted=True
        try:
            async with self._vl_lock:
                while self._profile_write_in_progress:
                    await asyncio.sleep(.01)
                prepared=await asyncio.to_thread(self._prepare_live_vision,docs["vision.json"])
                await asyncio.to_thread(self.statistics.flush)
                memory=ObjectMemory(str(profile.profile_dir/"object_memory.json"),similarity_threshold=.86)
                with self._profile_lock:
                    self.profile=profile;self.store=store;self._docs,self._revisions=docs,revisions
                    self.object_memory=memory
                    self.resolver=SemanticResolver(memory,docs["vision.json"].get("class_rules",{}))
                    self.object_tracker=self.resolver.tracker
                    self.buff_monitor=BuffMonitor(profile.profile_dir)
                    self.conversation=GameConversation(self.chat_vl,store)
                    self.statistics=GameStatistics(profile.profile_dir)
                    self._set_hud_regions(docs["hud.json"]["regions"],docs["hud.json"].get("layout","classic"))
                    self._refresh_profile_policy()
                await asyncio.to_thread(self._apply_live_vision,docs["vision.json"],prepared)
                self._objects=[];self._latest_yolo_objects=[];self._yolo_at=0
                self._latest_frame=None;self._latest_captured=None;self._capture_at=0;self._frame_identity=None
                self._preview_captured=None;self._preview_at=0
                self._latest_hud_state=HUDState();self._hud_ready=False
                self._hud_scan_complete=docs["hud.json"]["calibration"]["validated"]
                self._hud_waiting=False;self._hud_recheck_attempted=False;self._hud_failures=0;self._hud_missing_at=0;self._hud_at=0;self._last_hud_attempt=-1e9
                self._halt_reason=None;self._hud_recheck_status=None
                self._buff_preview=None;self._buff_at=0;self._observed_buffs=[]
                self._last_intent_data=None;self._last_intent_signature=None;self._last_intent_attempt=0
                self._game_knowledge_prompt=json.dumps(docs["knowledge.json"],ensure_ascii=False)
                self._learning_at=-1e9;self._nav_player=None;self._minimap_mask=None
                self._epoch+=1
                await asyncio.to_thread(atomic_json,self.profile_root/"active_game.json",{"id":profile.name})
        finally:
            self._processing_halted=False
        self.emit_web_event("game_changed",profile=profile.name)
        return profile.name

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
            self._set_hud_regions(docs["hud.json"]["regions"], docs["hud.json"].get("layout", "classic"))
            self._hud_ready = False
            self._hud_scan_complete = docs["hud.json"]["calibration"]["validated"]
            self._last_hud_attempt = -1e9
        if docs["object_memory.json"] != old["object_memory.json"]:
            await asyncio.to_thread(self.resolver.reset)
        self._docs, self._revisions = docs, revisions
        self.statistics.suspend()
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
        executor = getattr(self.scheduler, "executor", None)
        def describe(c):
            if c is None:
                return None
            binding = self._docs["input.json"]["bindings"].get(c.action_type)
            skill = next((s for s in self._docs["input.json"].get("attack_skills", []) if s["id"] == c.skill_id), None)
            if skill:
                binding = skill["key"]
            if c.action_type == "ATTACK" and c.maintain_attack:
                binding = "mouse_right"
            if c.action_type == "MOVE" and self._docs["input.json"]["movement"]["mode"] == "keys" and c.direction:
                dx, dy = c.direction
                movement = self._docs["input.json"]["movement"]
                binding = "+".join(([movement["right" if dx > 0 else "left"]] if abs(dx) > .25 else []) + ([movement["down" if dy > 0 else "up"]] if abs(dy) > .25 else []))
            return {"action": c.action_type, "source": c.source, "reason": c.reason, "direction": c.direction, "target": c.target, "track_id": c.track_id, "duration_ms": c.duration_ms, "binding": binding, "skill_id": c.skill_id, "skill_name": skill["name"] if skill else None}
        attack_ready = [o for o in self._objects if o.object_type == "monster" and o.relation == "hostile" and o.status == "confirmed" and o.semantic_confidence >= .6 and not self.combat_guard.is_blocked(o) and self.combat_guard.permits(o.track_id)] if yolo_fresh else []
        if self._processing_halted: attack_reason = self._halt_reason or "처리 중단"
        elif self._focus_paused: attack_reason = "게임 창 비활성 · 복귀 시 자동 재개"
        elif self._paused: attack_reason = "일시정지 · 시작/재개 필요"
        elif not self._foreground(): attack_reason = "게임 창 비활성 · 입력 차단"
        elif not hud_fresh or not hud.health_valid: attack_reason = "HP 최신 측정 대기"
        elif not self.yolo.enabled: attack_reason = "YOLO 비활성"
        elif not yolo_fresh: attack_reason = "최신 객체 탐지 대기"
        elif attack_ready: attack_reason = f"공격 가능 대상 {len(attack_ready)}개"
        elif self._objects: attack_reason = "공격 가능한 적 없음 · 의미 확정/재판정/제외 기준 확인"
        else: attack_reason = "YOLO 객체 없음 · 모델/ROI/탐지 신뢰도 확인"
        vision = self._docs["vision.json"]
        y = vision["yolo"]
        restart = False  # No detector model/device to reload in this branch.
        return {
            "api_version": 1,
            "instance": getattr(self, "_web_instance", None),
            "profile": self.profile.name,
            "running": self._running,
            "paused": self._paused,
            "foreground": self._foreground(),
            "hud_ready": self._hud_ready,
            "processing_halted": self._processing_halted,
            "hud_rechecking": self._hud_rechecking,
            "hud_recheck_status": self._hud_recheck_status,
            "buffs": {"configured": False, "bbox": None, "fresh": False, "active": [], "known_templates": [], "source": "disabled"},
            "halt_reason": self._halt_reason,
            "hunt_active": self._hunt_active,
            "combat":self.combat_guard.snapshot(),
            "attack": {"ready": len(attack_ready), "reason": attack_reason, "class_rules": self.resolver.class_rules},
            "navigation": {**self.navigation_monitor.snapshot(), "minimap_enabled":self._docs["navigation.json"]["minimap"]["enabled"], "fresh":now-self._minimap_at<.8, "player":self._nav_player},
            "object_counts": {
                "total": len(self._objects) if yolo_fresh and not self._processing_halted else 0,
                "raw": getattr(self.yolo, "raw_count", 0) if yolo_fresh and not self._processing_halted else 0,
                "monsters": sum(o.object_type == "monster" for o in self._objects) if yolo_fresh and not self._processing_halted else 0,
                "items": sum(o.object_type == "item" for o in self._objects) if yolo_fresh and not self._processing_halted else 0,
                "unknown": sum(o.object_type == "unknown" or o.status != "confirmed" for o in self._objects) if yolo_fresh and not self._processing_halted else 0,
            },
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
            "input": {
                "basic_attack_mode": self._docs['input.json'].get('basic_attack_mode', 'tap'),
                "attack_held": bool(getattr(getattr(executor, '_input', None), 'attack_held', False)),
                "move_held": getattr(executor,'_held_move_command',None) is not None,
                "bindings": dict(self._docs["input.json"]["bindings"]),
                "attack_skills": self._docs["input.json"].get("attack_skills", []),
                "movement": dict(self._docs["input.json"]["movement"]),
                "requested": describe(self._last_requested_command),
                "active": describe(getattr(executor, "active_command", None)),
                "last_sent": describe(latest),
                "last_sent_age_ms": round((now - executor.last_completed_at) * 1000) if getattr(executor, "last_completed_at", 0) else None,
                "error": getattr(executor, "last_error", None),
                "events": [{**{k: v for k, v in e.items() if k not in {"command", "at"}}, "age_ms": round((now-e["at"])*1000), **describe(e["command"])} for e in list(getattr(executor, "input_events", []))[-40:]],
            },
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
                        "enemy_health": o.enemy_health,
                        "enemy_health_valid": o.enemy_health_valid,
                        "enemy_bar_confirmed": o.enemy_bar_confirmed,
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
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            if self._processing_halted or self._hud_rechecking or not self._hud_ready:
                await asyncio.sleep(0.1)
                continue
            now = time.monotonic()
            if (
                self._latest_frame is not None
                and self._frame_seq != last
                and now >= next_run
            ):
                last = self._frame_seq
                next_run = now + self.yolo.interval
                frame = self._latest_frame
                captured_at = self._capture_at
                if self._docs["vision.json"].get("motion_gating", False):
                    motion = cv2.cvtColor(
                        cv2.resize(frame, (80, 45)), cv2.COLOR_BGR2GRAY
                    )
                    still = (
                        self._motion_image is not None
                        and float(cv2.absdiff(motion, self._motion_image).mean()) < 2
                    )
                    self._motion_image = motion
                    if still and self.combat_guard.focus is None and now - self._last_yolo_inference < 0.5:
                        self._gated_frames += 1
                        await asyncio.sleep(0.01)
                        continue
                epoch = self._epoch
                try:
                    detections = await loop.run_in_executor(
                        self._yolo_executor, self._detect_frame, frame
                    )
                    if epoch != self._epoch or self._processing_halted or self._hud_rechecking:
                        continue
                    self._last_yolo_inference = time.monotonic()
                    objects, mask = await loop.run_in_executor(
                        self._yolo_executor,
                        self._resolve_frame,
                        frame,
                        detections,
                        self._docs["navigation.json"],
                    )
                    if epoch == self._epoch:
                        for skill in self._docs['input.json'].get('attack_skills', []):
                            feedback = skill.get('visual_ready')
                            if not feedback:
                                continue
                            ready = self.combat_feedback.matches(frame, feedback)
                            previous = self._skill_feedback.get(skill['id'])
                            self._skill_feedback[skill['id']] = (ready, captured_at, epoch)
                            # Readiness transition is visual evidence, not a
                            # guarantee that a particular BLE input caused it.
                            if previous and previous[2] == epoch and previous[0] and not ready:
                                self.emit_web_event('skill_became_unavailable', skill_id=skill['id'])
                        self._latest_yolo_objects = detections
                        self._objects = objects
                        self._yolo_at = time.monotonic()
                        previously_dead = {tid for tid, reason in self.combat_guard.blocked.items() if reason == 'HEALTH_DEPLETED_CONFIRMED'}
                        self.combat_guard.observe(objects)
                        for tid, reason in self.combat_guard.blocked.items():
                            if reason == 'HEALTH_DEPLETED_CONFIRMED' and tid not in previously_dead:
                                self.statistics.event('combat_death_evidence', track_id=tid, source='calibrated_health_bar')
                                self.emit_web_event('combat_death_evidence', track_id=tid)
                        self.statistics.observe(objects,shape=frame.shape,roi=(self.yolo.roi_x,self.yolo.roi_y,self.yolo.roi_width,self.yolo.roi_height))
                except Exception as exc:
                    self.yolo.last_error = str(exc)
                    log.exception("[YOLO] failed")
                    self._objects = []
                    self._latest_yolo_objects = []
            await asyncio.sleep(0.01)

    async def _calibration_loop(self):
        while self._running:
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            if self._processing_halted or self._hud_rechecking or self._hud_waiting:
                await asyncio.sleep(0.1)
                continue
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
            epoch = self._epoch
            try:
                _, revisions = await asyncio.to_thread(self.store.snapshot)
                cached = self._docs["hud.json"]
                if self._hud_scan_complete:
                    if not await asyncio.to_thread(self._validate_hud, frame):
                        await self._recheck_hud()
                        continue
                    self._latest_hud_state = await asyncio.to_thread(self._analyze_hud, frame)
                    self._hud_at = time.monotonic()
                    self._hud_ready = self._latest_hud_state.health_valid
                    if not self._hud_ready:
                        await self._recheck_hud()
                    continue
                native = await asyncio.to_thread(self.profile.locate_bars, frame)
                if native:
                    from types import SimpleNamespace
                    boxes=[r["bbox"] for k,r in native.items() if k in {"health","sp","mp"}]
                    area=SimpleNamespace(data={"hud_bbox":[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]})
                    result=SimpleNamespace(data=native,elapsed_ms=0)
                else:
                    async with self._vl_lock:
                        area = await asyncio.to_thread(
                            self.vl.calibrate_hud_area,
                            frame,
                            self._latest_captured.source,
                            self.profile.hud_calibration_context,
                        )
                        if self._processing_halted or self._hud_rechecking or epoch != self._epoch or identity != self._frame_identity:
                            continue
                        result = await asyncio.to_thread(
                            self.vl.discover_hud,
                            frame,
                            self._latest_captured.source,
                            self.profile.hud_prompt_context,
                        )
                if self._processing_halted or self._hud_rechecking or epoch != self._epoch or identity != self._frame_identity:
                    continue
                regions = self.profile.prepare_regions(result.data)
                # Reject malformed model geometry before touching the live sensors.
                from app.profiles.profile_store import validate_document

                candidate = {
                    "version": 1,
                    "hud_bbox": area.data.get("hud_bbox"),
                    "regions": regions,
                    "calibration": {
                        "resolution": [frame.shape[1], frame.shape[0]],
                        "validated": False,
                        "source": "opencv-player-bars" if native else "qwen+opencv",
                    },
                }
                validate_document("hud.json", candidate)
                self._set_hud_regions(regions)
                valid = await asyncio.to_thread(self._validate_hud, self._latest_frame)
                if valid:
                    self._latest_hud_state = await asyncio.to_thread(self._analyze_hud, self._latest_frame)
                    self._hud_at = time.monotonic()
                    if not self._latest_hud_state.health_valid:
                        await self._recheck_hud()
                        continue
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
                    self._profile_write_in_progress = True
                    try:
                        await asyncio.to_thread(self.store.apply, ops, revisions)
                        docs, self._revisions = await asyncio.to_thread(self.store.snapshot)
                        self._docs["hud.json"] = docs["hud.json"]
                    finally:
                        self._profile_write_in_progress = False
                    self._hud_ready = self._hud_scan_complete = True
                    log.info(
                        "[HUD] OpenCV validated; normalized regions saved to profile"
                    )
                else:
                    await self._recheck_hud()
            except (VLResponseError, ValueError, OSError) as exc:
                self._log_vl_error("HUD", exc)
                await self._recheck_hud("HP바 초기 판별에 실패했습니다")
            except Exception:
                log.exception("[HUD] calibration failed")
                await self._recheck_hud("HP바 초기 판별에 실패했습니다")
            await asyncio.sleep(0.1)

    async def _learning_loop(self):
        while self._running:
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            if self._processing_halted or self._hud_rechecking or not self._hud_ready:
                await asyncio.sleep(0.1)
                continue
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
                if accepted:
                    self.statistics.event("objects_learned", count=len(accepted), records=accepted)
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

    async def _combat_recheck_loop(self):
        while self._running:
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            tid=self.combat_guard.pending()
            if tid is not None and not self._paused and not self._processing_halted and not self._hud_rechecking and self._fresh() and self._foreground():
                epoch=self._epoch
                target=next((o for o in self._objects if o.track_id==tid),None)
                session=self.combat_guard.sessions.get(tid)
                if target is None:
                    await asyncio.sleep(.1);continue
                frame=self._latest_frame.copy()
                payload={"yolo_objects":[{"index":0,"bbox":list(target.bbox),"detector_class":target.detector_type,"needs_classification":True}]}
                valid=False
                record={}
                try:
                    async with self._vl_lock:
                        result=await asyncio.to_thread(self.vl.classify_objects,frame,getattr(self._latest_captured,"source",self.profile.name),json.dumps(payload),self._game_knowledge_prompt+"\nThis target was attacked repeatedly. Recheck actual hostile-monster evidence (enemy health bar/combat context), not merely humanoid shape. NPC/item/obstacle or uncertainty means do not attack.")
                    record=next((o for o in result.data.get("objects",[]) if o.get("index")==0),{})
                    confidence=record.get("confidence",0)
                    valid=record.get("semantic")=="monster" and record.get("relation")=="hostile" and type(confidence) in (float,int) and .7<=confidence<=1
                    if self.profile.name == 'diablo4' and self._docs['input.json'].get('attack_verify_hostility', False):
                        valid = valid and record.get('enemy_health_bar') is True
                    name = record.get('name', '')
                    named_ally = isinstance(name, str) and name.casefold().strip() in self.resolver.friendly_names
                    if named_ally:
                        valid = False
                    self._last_vl_ms=result.elapsed_ms
                except Exception as exc:
                    log.debug("[COMBAT] target recheck failed: %s",exc)
                current=next((o for o in self._objects if o.track_id==tid),None)
                if epoch==self._epoch and self.combat_guard.sessions.get(tid) is session:
                    name = record.get('name', '')
                    named_ally = isinstance(name, str) and name.casefold().strip() in self.resolver.friendly_names
                    confidence = record.get('confidence', 0)
                    friendly = (record.get('relation') == 'friendly' or named_ally) and type(confidence) in (int, float) and .8 <= confidence <= 1
                    if friendly and current is not None and current.memory_id == target.memory_id:
                        await asyncio.to_thread(self.resolver.remember_friendly, tid, frame, name if isinstance(name, str) and len(name) <= 100 else '')
                        current.object_type, current.relation = 'npc', 'friendly'
                        current.status, current.semantic_confidence = 'confirmed', .9
                    valid=valid and current is not None and current.memory_id==target.memory_id and current.object_type=="monster" and current.relation=="hostile"
                    self.combat_guard.confirm(tid,valid)
                    self.statistics.event("combat_rechecked",track_id=tid,accepted=valid)
                    self.emit_web_event("combat_rechecked",track_id=tid,accepted=valid)
            await asyncio.sleep(.1)

    async def _statistics_loop(self):
        try:
            while self._running:
                if not self._processing_halted:
                    await asyncio.to_thread(self.statistics.flush)
                await asyncio.sleep(1)
        finally:
            await asyncio.to_thread(self.statistics.flush)

    async def _refresh_loop(self):
        while self._running:
            if self._processing_halted or self._hud_rechecking:
                await asyncio.sleep(0.1)
                continue
            if self._profile_write_in_progress:
                await asyncio.sleep(0.05)
                continue
            try:
                watched_store=self.store
                docs, revisions = await asyncio.to_thread(watched_store.snapshot)
                if self._profile_write_in_progress or watched_store is not self.store:
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
                        self._set_hud_regions(docs["hud.json"]["regions"], docs["hud.json"].get("layout", "classic"))
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

    async def _control_shortcuts_loop(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()
        def pressed(number):
            if not stop.is_set():loop.call_soon_threadsafe(queue.put_nowait, number)
        def watch():
            try:
                watch_control_hotkeys(stop, self._foreground, pressed)
            except Exception:
                log.exception('[HOTKEY] shortcut listener failed')
        thread = threading.Thread(target=watch, name='game-control-hotkeys', daemon=True)
        thread.start()
        try:
            while self._running:
                number=await queue.get()
                try:
                    await self._handle_control_shortcut(number)
                except Exception:
                    log.exception('[HOTKEY] control failed; listener remains active')
        finally:
            stop.set()
            await asyncio.to_thread(thread.join, .5)

    async def _handle_control_shortcut(self, number):
        if number == 6:
            if self._foreground():await self.handle_control('/release-pause')
            return
        if number == 0:
            if self._foreground():
                log.info('[HOTKEY] Tab -> pause/resume requested')
                await self._toggle_tab_pause()
            return
        if number != 2 and not self._foreground():return
        message = {1: '/hunt', 2: '/stop', 3: '이동',
                   4: '반복스킬', 5: '제자리사냥'}.get(number)
        if message is not None:
            log.info('[HOTKEY] Ctrl+%s -> %s', number, message)
            await self.handle_control(message)

    async def _toggle_tab_pause(self):
        self._tab_toggle_inflight=True
        try:
            if not self._paused and not self._processing_halted:
                fields=('_move_only','_hunt_active','_stationary_hunt_mode','_stationary_skill_mode',
                        '_manual_skill_mode','_repeat_skills_hunting','_movement_hunt_requested',
                        '_screen_guide_enabled','_follow_orange_route','_hunt_preferred_direction',
                        '_planned_move_heading','_arrow_hold_distance')
                state={key:getattr(self,key,None) for key in fields}
                await self.handle_control('/stop')
                self._tab_resume_state=state
                log.info('[HOTKEY] Tab -> all commands paused')
            else:
                state=getattr(self,'_tab_resume_state',None)
                message='이동' if state and state['_move_only'] else '/hunt' if not state or state['_hunt_active'] else '/resume'
                if await self.handle_control(message):
                    if state:
                        for key,value in state.items():setattr(self,key,value)
                    self._tab_resume_state=None
                    log.info('[HOTKEY] Tab -> previous control modes resumed')
        finally:
            self._tab_toggle_inflight=False

    async def _emergency_loop(self):
        while self._running:
            await self._check_focus_transition()
            emergency = getattr(self.capture, "emergency_stop_pressed", None)
            if emergency is not None and emergency() is True and not getattr(self,'_manual_control',False):
                await self.handle_control("/stop")
            await asyncio.sleep(.01)

    async def _action_loop(self):
        while self._running:
            if getattr(self,'_release_paused',False):
                await asyncio.sleep(.02)
                continue
            if getattr(self,'_manual_control',False):
                await asyncio.sleep(.05);continue
            if not self._focus_work_allowed():
                await asyncio.sleep(.1);continue
            now = time.monotonic()
            if (not getattr(self,'_stationary_hunt_mode',False)
                    and getattr(self,'_hunt_active',False)
                    and await self._stationary_manual_move_tick()):
                await asyncio.sleep(.01)
                continue
            if getattr(self,'_stationary_hunt_mode',False):
                if not self._paused and not self._processing_halted and self._fresh() and self._foreground():
                    await self._stationary_hunt_tick()
                await asyncio.sleep(.01)
                continue
            if getattr(self,'_manual_skill_mode',False) or getattr(self,'_repeat_skills_hunting',False):
                if getattr(self,'_stationary_skill_mode',False):
                    if (not self._paused and not self._processing_halted and self._latest_frame is not None
                            and now-self._capture_at<1 and self._foreground()):
                        skill=self._hunt_attack_rotation(command('ATTACK',source='MANUAL_SKILL',epoch=self._epoch))
                        if skill is not None:await self.scheduler.submit(skill)
                    await asyncio.sleep(.05)
                    continue
                if (not self._paused and not self._processing_halted and self._latest_frame is not None
                        and now-self._capture_at<1 and self._foreground()):
                    if await self._manual_skill_tick():
                        await asyncio.sleep(.05)
                        continue
            if self._processing_halted or self._hud_rechecking or self._hud_probe_command is not None:
                await asyncio.sleep(0.1)
                continue
            if self._directive and self._directive.get("action")=="ATTACK" and now >= self._directive_until:
                tid=self._directive.get("track_id")
                target=next((o for o in self._objects if o.track_id==tid and o.object_type=="monster" and o.relation=="hostile" and o.status=="confirmed" and o.semantic_confidence>=.6),None)
                if (target or tid==self.combat_guard.focus and self.combat_guard.waiting_for_focus(now)) and tid not in self.combat_guard.blocked and now-self._yolo_at<=max(.75,self.yolo.interval*2):
                    self._directive_until=now+1  # Revalidated target lease; no blind key hold.
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
                filter_objects=getattr(self,'_policy_objects',None)
                if filter_objects:objects=filter_objects(objects)
                c = self.policy.decide(
                    [o for o in objects if not self.combat_guard.is_blocked(o) and self.combat_guard.permits(o.track_id)],
                    self._latest_hud_state,
                    self._docs,
                    self._latest_frame.shape,
                    self._directive,
                    self._epoch,
                    potion_ready=self.scheduler._is_cooldown_ready(
                        command("USE_POTION")
                    ),
                    preferred_track_id=self._attack_track_id,
                )
                defer_optional=getattr(self,'_defer_optional_actions',None)
                if getattr(self,'_manual_skill_mode',False):
                    c=command('MOVE',source='HUNT_EXPLORE',direction=self.minimap_memory.last_direction or (1.,0.),epoch=self._epoch)
                if defer_optional and defer_optional(c):
                    await asyncio.sleep(self.reaction_interval)
                    continue
                awaiting = any(o.status != "confirmed" and o.detector_type in self.resolver.class_rules and self.resolver.class_rules[o.detector_type]["type"] == "monster" for o in objects)
                if awaiting:
                    if self._semantic_wait_since is None:
                        self._semantic_wait_since = now
                else:
                    self._semantic_wait_since = None
                if self._hunt_active and c.action_type == "STOP" and c.reason == "NO_KNOWN_TARGET" and awaiting and now - self._semantic_wait_since < 2:
                    c = command("STOP", source="SEMANTIC_WAIT", reason="몬스터 후보 의미 확인 중", epoch=self._epoch)
                if c.action_type in {"STOP","MOVE","TAKE"} and c.source not in {"HP_RETREAT","HP_EMERGENCY","USER_COMMAND","COMBAT_APPROACH","COMBAT_SPACING"} and self.combat_guard.waiting_for_focus(now):
                    c = command("STOP", source="COMBAT_TRACK_WAIT", reason="공격 대상 재탐지/재판정 대기 · 이동 보류", epoch=self._epoch)
                if self._hunt_active and c.action_type == "STOP" and c.reason == "NO_KNOWN_TARGET":
                    if now - self._yolo_at <= max(0.75, self.yolo.interval * 2) and self.yolo.enabled:
                        directions = ((0, -1), (.7, -.7), (1, 0), (.7, .7), (0, 1), (-.7, .7), (-1, 0), (-.7, -.7))
                        if now >= self._hunt_turn_at:
                            self._hunt_heading = (self._hunt_heading + 1) % len(directions)
                            self._hunt_turn_at = now + 4
                        c = command("MOVE", direction=directions[self._hunt_heading], source="HUNT_EXPLORE", reason="search for targets", epoch=self._epoch)
                if self._directive and self._directive.get("action")=="ATTACK" and c.action_type=="STOP" and self.combat_guard.waiting_for_focus(now):
                    c=command("STOP",source="COMBAT_TRACK_WAIT",reason="지정 공격 대상 재탐지 대기",epoch=self._epoch)
                if c.action_type == "ATTACK":
                    target=next((o for o in objects if o.track_id==c.track_id),None)
                    combat_settings = dict(self._docs['input.json'])
                    combat_settings['attack_until_bar_lost']=getattr(self,'attack_until_bar_lost',False) and self.profile.name=='diablo4'
                    combat_settings['attack_verify_hostility'] = self.profile.name == 'diablo4' and combat_settings.get('attack_verify_hostility', False)
                    combat_settings['attack_require_health_feedback'] = (
                        self.profile.name == 'diablo4' and
                        combat_settings.get('attack_require_health_feedback', False)
                        and self._docs['vision.json'].get('enemy_health_bar', {}).get('enabled', False)
                    )
                    if target is None or not self.combat_guard.request(target,combat_settings):
                        c=command("STOP",source="COMBAT_GUARD",reason="전투 대상 재판정/시간 제한",epoch=self._epoch)
                if c.action_type == "ATTACK":
                    self._attack_track_id = c.track_id
                    c=replace(c,maintain_attack=False)
                    skills = self._docs["input.json"].get("attack_skills", [])
                    for skill in sorted(skills, key=lambda s: self.scheduler._last_execution.get(("USE_SKILL", s["id"]), -1e9)):
                        if not skill["enabled"] or 'USE_SKILL' in self._docs['input.json'].get('disabled_actions',[]):
                            continue
                        allowed=getattr(self,'_automatic_skill_allowed',None)
                        if allowed and not allowed(skill):continue
                        if skill.get('visual_ready'):
                            ready, sampled_at, sampled_epoch = self._skill_feedback.get(skill['id'], (False, 0, -1))
                            if not ready or now - sampled_at > .5 or sampled_epoch != self._epoch:
                                continue
                        candidate = replace(c, action_type="USE_SKILL", skill_id=skill["id"], cooldown=skill["cooldown_ms"]/1000, source="ATTACK_ROTATION", reason=skill["name"])
                        if self.scheduler._is_cooldown_ready(candidate):
                            c = candidate
                            break
                    if c.action_type=='ATTACK' and not getattr(self,'_repeat_skills_hunting',False):
                        c=command('STOP',source='COMBAT',reason='COMBAT_COOLDOWN',epoch=self._epoch)
                if c.action_type in {"MOVE", "DODGE"} and c.direction is not None:
                    continue_navigation=getattr(self,'_continue_navigation',None)
                    navigation_escape=(getattr(self,'_try_navigation_escape',None)
                                       if c.action_type=='MOVE' and c.source in {'HUNT_EXPLORE','MINIMAP_QWEN','USER_COMMAND'} else None)
                    if navigation_escape and await navigation_escape():
                        await asyncio.sleep(self.reaction_interval)
                        continue
                    buff_due=('CAST_BUFF' not in self._docs['input.json'].get('disabled_actions',[])
                              and self.buff_monitor.pending_recast and now-self._buff_at<1)
                    if continue_navigation and continue_navigation(c):
                        # Buff maintenance must not bypass the goal lock and invoke a planner.
                        if buff_due:
                            candidate=command('CAST_BUFF',source='BUFF_EXPIRED',reason='확인된 버프 아이콘 소실 · 1회 재사용',epoch=self._epoch)
                            if self.scheduler._is_cooldown_ready(candidate):await self.scheduler.submit(candidate)
                        await asyncio.sleep(self.reaction_interval)
                        continue
                    if navigation_escape and await navigation_escape():
                        await asyncio.sleep(self.reaction_interval)
                        continue
                    nav_settings = copy.deepcopy(self._docs["navigation.json"])
                    exclusions=getattr(self,'_world_click_exclusions',None)
                    if exclusions:nav_settings['excluded_regions']=exclusions()
                    if self._nav_player is not None: nav_settings["minimap"]["player"] = self._nav_player
                    if c.source == "HUNT_EXPLORE" and self._nav_direction and now-self._nav_direction_at<3:
                        c = replace(c,direction=self._nav_direction,source="MINIMAP_QWEN")
                    if now - self._minimap_at >= .8:
                        nav_settings['minimap']['enabled'] = False
                        c = replace(c, reason='미니맵 미확인: 화면 장애물 검사 · 설정된 이동거리 사용')
                    prepare_navigation=getattr(self,'_prepare_navigation',None)
                    if prepare_navigation:c=prepare_navigation(c,nav_settings)
                    step = self.navigator.choose(
                        c.direction,
                        objects,
                        self._latest_frame.shape,
                        nav_settings,
                        self._minimap_mask if now-self._minimap_at<.8 else None,
                        self.navigation_monitor.failed_direction if now-self.navigation_monitor.last_recovery<3 else None,
                    ) if c.direction is not None else None
                    if step is None:
                        failed=getattr(self,'_navigation_click_failed',None)
                        if failed:failed(c)
                        if not self._path_blocked_since:
                            self._path_blocked_since = now
                        if c.action_type!='STOP':c = command("STOP", reason="PATH_BLOCKED", epoch=self._epoch)
                        if self._hunt_active:
                            self._hunt_turn_at = 0
                    else:
                        self._path_blocked_since = 0
                        c = replace(
                            c,
                            direction=step[0],
                            target=step[1],
                            duration_ms=int(self._docs["navigation.json"]["step_ms"]),
                        )
                if (c.source not in {'HP_EMERGENCY', 'HP_RETREAT', 'USER_COMMAND'} and c.reason != 'POTION_COOLDOWN_WAIT'
                        and 'CAST_BUFF' not in self._docs['input.json'].get('disabled_actions',[])
                        and self.buff_monitor.pending_recast and now-self._buff_at < 1):
                    candidate = command('CAST_BUFF', source='BUFF_EXPIRED', reason='확인된 버프 아이콘 소실 · 1회 재사용', epoch=self._epoch)
                    if self.scheduler._is_cooldown_ready(candidate):
                        c = candidate
            finalize=getattr(self,'_finalize_navigation_command',None)
            if finalize:c=finalize(c)
            self._last_requested_command = c
            dispatch=getattr(self,'_dispatch_action',None)
            if dispatch and not dispatch(c):
                await asyncio.sleep(self.reaction_interval)
                continue
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
        log.debug(
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
                log.debug(
                    f"[OBJECT] TRACK={o.track_id} MEM={o.memory_id} {o.object_type}/{o.relation} {o.status} conf={o.semantic_confidence:.2f} pos=({x:.0f},{y:.0f})"
                )
        self._last_report_action = c.action_type

    async def handle_control(self, message):
        value = message.strip().lower()
        compact = "".join(value.split()).rstrip(".!?")
        if value in {"/stop", "/pause", "멈춰", "정지", "중지", "stop", "/quit"} or compact in {"사냥중지", "사냥중지해", "사냥멈춰", "사냥중단", "사냥중단해", "사냥그만", "사냥그만해"}:
            if not self._focus_stopping:
                self._focus_resume_state = None
            self._epoch += 1
            self.statistics.suspend()
            self.navigation_monitor.reset()
            self.combat_guard.reset()
            self._attack_track_id = None
            self._minimap_at = 0
            self._nav_direction = None
            self._paused = True
            self._path_blocked_since = 0
            self._minimap_unusable = False
            self._directive = None
            self._hunt_active = False
            self.emit_web_event("control", action="stop")
            print('[CONTROL] pause applied; sending STOP to input controller')
            try:
                await self.scheduler.submit_emergency(
                    command("STOP", source="USER_STOP", epoch=self._epoch)
                )
            except Exception as exc:
                print(f'[STOP INPUT] failed: {exc}')
                raise
            print("[CONTROL] stopped; pending model commands invalidated")
            if value == "/quit":
                self._running = False
            return True
        compact = "".join(value.split()).rstrip(".!?")
        hunt = compact in {"/hunt", "hunt", "사냥시작", "사냥시작해", "사냥시작해줘", "자동사냥시작", "자동사냥시작해", "자동사냥", "자동사냥해", "자동사냥해줘", "자동사냥시작해줘", "사냥해", "사냥해줘", "사냥을해줘"}
        if hunt or value in {"/resume", "계속", "다시 시작", "resume"}:
            self._focus_paused = False
            self._focus_resume_state = None
            self._focus_was_active = self._foreground()
            self._epoch += 1
            self._hud_waiting = False
            self._hud_recheck_attempted = False
            self._hud_probe_attempted = False
            self._semantic_wait_since = None
            self._restart_processing()
            self._paused = False
            self._hud_waiting = not self._hud_ready and getattr(self.profile, 'layout', 'classic') == 'bars_hp_sp_mp'
            self._directive = None
            self._hunt_active = hunt
            self._hunt_heading = 0
            self._hunt_turn_at = time.monotonic() + 4
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
            "[CHAT] /stop 즉시 중단, /resume 자동 반응, /status, /quit | ESC 즉시 중단"
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

    async def _wait_qwen_foreground(self, epoch, request_id):
        if not getattr(self, "qwen_requires_foreground", False):return True
        waiting=False
        while self._running and epoch==self._epoch and not self._processing_halted:
            if self._focus_work_allowed() and not self._hud_rechecking:return True
            if not waiting:
                self.emit_web_event("chat_waiting",request_id=request_id,message="Qwen 처리 대기: 게임 창을 활성화하세요.")
                waiting=True
            await asyncio.sleep(.1)
        self.emit_web_event("chat_cancelled",request_id=request_id,message="중단 또는 설정 변경으로 취소했습니다.")
        return False

    async def _conversation_loop(self):
        while self._running:
            queued = await self._chat_queue.get()
            message, epoch = queued[:2]
            request_id = queued[2] if len(queued) > 2 else None
            try:
                if not await self._wait_qwen_foreground(epoch, request_id):continue
                if getattr(self,"_qwen_stopped",False):
                    raise ValueError("사냥 중단 상태에서는 Qwen 처리를 하지 않습니다. 시작/재개하세요.")
                if self._processing_halted or self._hud_rechecking:
                    raise ValueError("HP바 미검출로 처리 중단 상태입니다. 먼저 시작/재개하세요.")
                if epoch != self._epoch:
                    self.emit_web_event(
                        "chat_cancelled",
                        request_id=request_id,
                        message="중단 또는 설정 변경으로 취소했습니다.",
                    )
                    continue
                async with self._vl_lock:
                    if getattr(self, "qwen_requires_foreground", False) and (not self._foreground() or self._hud_rechecking):
                        # Release the model lock while waiting so startup calibration can run.
                        self._chat_queue.put_nowait(queued)
                        continue
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
                        self._set_hud_regions(docs["hud.json"]["regions"], docs["hud.json"].get("layout", "classic"))
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
                elif action == "HUNT":
                    await self.handle_control("/hunt")
                elif action != "NONE":
                    self._hunt_active = False
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
            self._statistics_loop,
            self._navigation_loop,
            self._navigation_replan_loop,
            self._emergency_loop,
            self._combat_recheck_loop,
        ]
        if getattr(self,'movement_test_mode',False):
            disabled={'_hud_loop','_yolo_loop','_calibration_loop','_learning_loop',
                      '_emergency_loop','_combat_recheck_loop','_navigation_replan_loop'}
            workers=[self._hud_read_loop if worker.__name__=='_hud_loop' and getattr(self,'hud_mode',False) else
                     self._idle_sensor_loop if worker.__name__ in disabled else
                     self._movement_test_loop if worker.__name__=='_action_loop' else worker
                     for worker in workers]
        if self._console_enabled:
            workers += [self._console_loop]
            self._start_console()
        if callable(getattr(getattr(self, 'capture', None), 'control_shortcuts_down', None)):
            workers.append(self._control_shortcuts_loop)
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
            await asyncio.to_thread(self.statistics.flush)

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
