from __future__ import annotations

import asyncio
import copy
import cv2
import hmac
import json
import logging
import secrets
import sqlite3
import time
import webbrowser
from urllib.parse import urlsplit
from aiohttp import web, WSMsgType
from app.profiles.runtime_settings import number
from app.profiles.profile_store import PROJECT_ROOT, validate_document
from app.web.local_dashboard import LocalDashboard
from app.web.screen_preview import encode_preview

SITE_ORIGIN = "https://qwen-visual-agent-lab.kiwimaru.chatgpt.site"


class AgentBridge:
    """Runs on the Agent event loop; no model work in control handlers."""

    def __init__(self, agent, *, port=8765, token=None, origins=(), timeout=8, dashboard_dir=None):
        self.agent = agent
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self.origins = {
            SITE_ORIGIN,
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            *origins,
        }
        self.timeout = timeout
        self.last_seen = None
        self.lease_lost = False
        self.hp_missing_since = None
        self.hp_disconnect_reason = None
        self.hp_timeout = 15
        self.runner = None
        self.watchdog = None
        self.gpu_task = None
        self.gpu = None
        self.gpu_at = 0
        self.lock = asyncio.Lock()
        self.sockets = set()
        self.browser_present = asyncio.Event()
        self.dashboard = LocalDashboard(
            dashboard_dir or PROJECT_ROOT / "Visual-Agent-Lab-local" / "dist",
            agent.profile.profile_dir,
        )
        self.app = web.Application(
            middlewares=[self.security], client_max_size=2_500_000
        )
        self.app.router.add_get("/", self.local_page)
        self.app.router.add_get("/v1/browser-presence", self.browser_presence)
        self.app.router.add_get("/assets/{path:.*}", self.dashboard.asset)
        self.app.router.add_get("/favicon.svg", self.dashboard.asset)
        for method in ("GET", "POST", "DELETE"):
            self.app.router.add_route(method, "/api/experiments", self.dashboard.experiments)
        self.app.router.add_post("/v1/session", self.session)
        self.app.router.add_get("/v1/state", self.state)
        self.app.router.add_get("/v1/preview", self.preview)
        self.app.router.add_post("/v1/control", self.control)
        self.app.router.add_post("/v1/chat", self.chat)
        self.app.router.add_get("/v1/games", self.games)
        self.app.router.add_post("/v1/games", self.add_game)
        self.app.router.add_post("/v1/games/select", self.select_game)
        self.app.router.add_get("/v1/profiles", self.profiles)
        self.app.router.add_patch("/v1/profiles", self.patch_profiles)
        self.app.router.add_post("/v1/tuning", self.tuning)
        self.app.router.add_get("/v1/statistics", self.statistics)
        self.app.router.add_post("/v1/statistics/kills/confirm", self.confirm_kill)
        self.app.router.add_get("/v1/game-settings", self.game_settings)
        self.app.router.add_post("/v1/game-settings", self.save_game_settings)
        self.app.router.add_get("/v1/buffs/frame", self.buff_frame)
        self.app.router.add_get("/v1/ws", self.websocket)
        self.app.router.add_route("OPTIONS", "/{path:.*}", self.options)

    def touch(self):
        self.last_seen = time.monotonic()
        self.lease_lost = False

    def authorized(self, request):
        supplied = request.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, "Bearer " + self.token)

    @web.middleware
    async def security(self, request, handler):
        origin = request.headers.get("Origin")
        host = urlsplit("http://" + request.headers.get("Host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1"} or (
            origin and origin not in self.origins
        ):
            return web.json_response(
                {"error": "허용되지 않은 연결 주소입니다."}, status=403
            )
        # Older connected pages also advertise their presence while reconnecting
        # with an expired token after the Agent restarts.
        if request.path in {'/v1/state', '/v1/session', '/v1/ws'} and (
            origin in self.origins or request.headers.get('Sec-Fetch-Site') == 'same-origin'
        ):
            self.browser_present.set()
        public = request.path in {"/", "/favicon.svg", "/api/experiments", "/v1/ws", "/v1/session", "/v1/browser-presence"} or request.path.startswith("/assets/")
        if request.method != "OPTIONS" and not public:
            if not self.authorized(request):
                response = web.json_response(
                    {"error": self.hp_disconnect_reason or "연결이 만료되었습니다. 다시 연결하세요."}, status=410 if self.hp_disconnect_reason else 401
                )
                return self.cors(response, origin)
            self.touch()
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        except web.HTTPException as exc:
            response = web.Response(text=exc.text, status=exc.status, headers=exc.headers)
        return self.cors(response, origin)

    @staticmethod
    def cors(response, origin):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if origin:
            response.headers.update(
                {
                    "Access-Control-Allow-Origin": origin,
                    "Vary": "Origin",
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Allow-Private-Network": "true",
                    "Access-Control-Max-Age": "600",
                }
            )
        return response

    async def options(self, request):
        return web.Response(status=204)

    async def browser_presence(self, request):
        if (request.headers.get('X-Agent-Page') != '1'
                or request.headers.get('Sec-Fetch-Site') != 'same-origin'
                or request.headers.get('Origin') not in {None, str(request.url.origin())}):
            raise web.HTTPForbidden(text='로컬 페이지에서 요청하세요.')
        # Presence is not a control lease and never authorizes game input.
        self.browser_present.set()
        return web.Response(status=204)

    async def open_dashboard_if_needed(self, wait_seconds=5):
        try:
            await asyncio.wait_for(self.browser_present.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            await asyncio.to_thread(webbrowser.open, f'http://127.0.0.1:{self.port}/#game')
            return True
        print('[WEB] 이미 열린 대시보드를 사용합니다. 새 탭을 열지 않습니다.')
        return False

    async def session(self, request):
        # Browser Origin is mandatory: no navigations, forms, opaque/file origins
        # or arbitrary websites may obtain the in-memory connection credential.
        if request.headers.get("Origin") not in self.origins:
            return web.json_response({"error": "허용된 웹페이지에서 연결하세요."}, status=403)
        if request.content_type != "application/json":
            return web.json_response({"error": "JSON 연결 요청이 필요합니다."}, status=415)
        body = await request.json()
        if not isinstance(body, dict) or body.get("connect") is not True:
            return web.json_response({"error": "올바른 연결 요청이 필요합니다."}, status=400)
        if self.hp_disconnect_reason:
            self.hp_disconnect_reason = None
            self.token = secrets.token_urlsafe(32)
        self.hp_missing_since = time.monotonic()
        self.touch()
        # Pairing only; execution stays paused until explicit start/resume.
        return web.json_response({"api_version": 1, "token": self.token})

    async def state(self, request):
        return web.json_response(self.snapshot())

    async def preview(self, request):
        a = self.agent
        epoch, profile = a._epoch, a.profile.name
        foreground = a._foreground()
        captured, at = a._preview_captured, a._preview_at
        if foreground and not a._processing_halted and a._latest_captured is not None:
            captured, at = a._latest_captured, a._capture_at
        if captured is None:
            return web.json_response({'screen': None, 'reason': '저장된 게임 화면이 없습니다. 게임 창 활성화 후 캡처를 기다려 주세요.',
                                      'profile': profile, 'halted': a._processing_halted})
        frame = captured.image.copy()
        settings = copy.deepcopy(a._docs['navigation.json'])
        hud=copy.deepcopy(a._docs.get('hud.json',{}));inputs=copy.deepcopy(a._docs.get('input.json',{}))
        # Export current tracked HUD coordinates, rather than a stale saved calibration.
        if a._latest_hud_state.regions:hud['regions']={**hud.get('regions',{}),**copy.deepcopy(a._latest_hud_state.regions)}
        data = await asyncio.to_thread(encode_preview, frame, settings, hud, inputs, profile)
        if epoch != a._epoch or profile != a.profile.name:
            return web.json_response({'screen': None, 'reason': '게임 설정이 변경되었습니다. 새 캡처를 기다려 주세요.'})
        age = max(0, time.monotonic() - at)
        data.update(profile=profile, source=captured.source, age_seconds=round(age, 1),
                    stale=age >= 1 or a._processing_halted or not foreground,
                    halted=a._processing_halted, foreground=foreground)
        return web.json_response(data)

    def snapshot(self):
        result = self.agent.web_snapshot()
        result["metrics"]["gpu"] = (
            self.gpu if time.monotonic() - self.gpu_at < 5 else None
        )
        result["connection"] = {
            "lease_timeout_seconds": self.timeout,
            "lease_lost": self.lease_lost,
            "hp_timeout_seconds": self.hp_timeout,
            "hp_missing_seconds": round(time.monotonic()-self.hp_missing_since, 1) if self.hp_missing_since is not None else 0,
            "disconnect_reason": self.hp_disconnect_reason,
        }
        return result

    async def control(self, request):
        data = await request.json()
        value = data.get("action")
        if value == "disconnect":
            await self.agent.handle_control("/stop")
            self.token = secrets.token_urlsafe(32)
            self.last_seen = None
            self.hp_missing_since = None
            for ws in list(self.sockets):
                await ws.close(code=1000, message=b"Disconnected")
            return web.json_response({"disconnected": True})
        commands = {
            "start": "/resume",
            "resume": "/resume",
            "stop": "/stop",
            "pause": "/stop",
        }
        if value not in commands:
            raise ValueError("start/resume/stop/pause만 가능합니다.")
        await self.agent.handle_control(commands[value])
        if value in {'start', 'resume'}:
            self.hp_missing_since = time.monotonic()
        return web.json_response({"ok": True, "state": self.snapshot()})

    async def chat(self, request):
        data = await request.json()
        message = data.get("message")
        request_id = data.get("id")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 6000:
            raise ValueError("대화는 1~6000자 필요합니다.")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            raise ValueError("대화 요청 ID 필요")
        if await self.agent.handle_control(message):
            mode_reply = None
            if self.agent._processing_halted:
                mode_reply = "사냥 중단 · 모든 작업 일시정지. 버튼 또는 단축키로 재개하세요."
            elif getattr(self.agent, '_stationary_hunt_mode', False):
                mode_reply = "제자리사냥 · 몹을 감지·타겟팅하고 주공격 스킬(오른쪽 클릭)만 사용합니다."
            elif getattr(self.agent, '_screen_guide_enabled', False):
                mode_reply = "이동 · 빨간 방향 표시와 흰 점을 함께 인식해 예정 진행 방향을 설정합니다. 미니맵 통로를 확인한 뒤 이동합니다."
            self.agent.emit_web_event(
                "chat_result",
                request_id=request_id,
                reply=(mode_reply or ("예정 진행 방향을 다시 선정합니다. 다른 통로가 있으면 이전 방향을 피하고, 최신 미니맵 확인 후 적용합니다." if "".join(message.lower().split()).rstrip(".!?") in {"/replan","예정진행방향변경","예정진행방향바꿔줘"} else "공격 없이 이동합니다. 주황색 선·핀을 우선 따라가고, 둘 다 없으면 미니맵 통로를 탐색합니다." if getattr(self.agent,"_move_only",False) and self.agent._hunt_active else "이동하며 사냥합니다. 주황색 선·핀을 우선 따라가고, 둘 다 없으면 미니맵 통로를 탐색합니다. 적을 만나면 공격을 우선하고 전투 후 이동을 이어갑니다." if getattr(self.agent,"_follow_orange_route",False) and self.agent._hunt_active else "지속 사냥을 시작합니다. HP와 객체 확인 후 적을 공격하고, 적이 없으면 장애물을 피하며 짧게 탐색 이동합니다." if self.agent._hunt_active else "제어 명령을 적용했습니다.")),
                saved=[],
                directive={"action": "NONE"},
            )
        else:
            if self.agent._processing_halted:
                raise ValueError((self.agent._halt_reason or "모든 작업 일시정지") + ". 버튼 또는 단축키로 재개하세요.")
            if self.agent._latest_frame is None:
                raise ValueError("게임 창을 캡처한 뒤 대화할 수 있습니다.")
            try:
                self.agent._chat_queue.put_nowait(
                    (message, self.agent._epoch, request_id)
                )
            except asyncio.QueueFull:
                return web.json_response(
                    {"error": "대화 대기열이 가득 찼습니다."}, status=429
                )
            self.agent.emit_web_event("chat_queued", request_id=request_id)
        return web.json_response({"id": request_id, "accepted": True}, status=202)

    async def games(self, request):
        from app.profiles.game_catalog import list_games
        return web.json_response({"active":self.agent.profile.name,"games":[g for g in await asyncio.to_thread(list_games,self.agent.profile_root) if g["id"]!="generic" or self.agent.profile.name=="generic"]})

    async def add_game(self, request):
        from app.profiles.game_catalog import add_game
        data=await request.json()
        async with self.lock:
            game=await asyncio.to_thread(add_game,data.get("name"),data.get("window_title",""),self.agent.profile_root)
        self.agent.emit_web_event("game_added",game=game)
        return web.json_response({"game":game,"active":self.agent.profile.name},status=201)

    async def select_game(self, request):
        data=await request.json()
        if not isinstance(data.get("id"),str):raise ValueError("게임을 선택하세요.")
        async with self.lock:
            await self.agent.switch_game(data["id"])
            self.dashboard.records_path=self.agent.profile.profile_dir/"tuning_experiments.json"
        return web.json_response({"active":self.agent.profile.name,"paused":True,"settings":self.agent.web_snapshot()["settings"]})

    async def profiles(self, request):
        docs, revisions = await asyncio.to_thread(self.agent.store.snapshot)
        return web.json_response(
            {
                "profile": self.agent.profile.name,
                "documents": docs,
                "revisions": revisions,
            }
        )

    async def patch_profiles(self, request):
        data = await request.json()
        if not isinstance(data.get("operations"), list) or not isinstance(
            data.get("revisions"), dict
        ):
            raise ValueError("operations 및 현재 revisions 필요")
        await self.agent.handle_control("/stop")
        async with self.lock:
            self.agent._profile_write_in_progress = True
            try:
                saved = await self.agent.commit_profile_operations(
                    data["operations"], data["revisions"]
                )
            finally:
                self.agent._profile_write_in_progress = False
        self.agent.emit_web_event("profile_saved", saved=saved)
        return web.json_response({"saved": saved, "paused": True})

    async def statistics(self, request):
        try:
            result = await asyncio.to_thread(self.agent.statistics.snapshot, request.query.get("date"))
        except (OSError, sqlite3.Error) as exc:
            return web.json_response({"error": "통계 저장소 접근 실패: " + str(exc)}, status=503)
        result["profile"] = self.agent.profile.name
        result["current"] = self.agent.web_snapshot()["object_counts"]
        result["memory_records"] = len(self.agent.object_memory.all_memory_ids())
        return web.json_response(result)

    async def confirm_kill(self, request):
        data = await request.json()
        if data.get("confirmed") is not True:
            raise ValueError("사용자 처치 확인이 필요합니다.")
        try:
            result = await asyncio.to_thread(self.agent.statistics.confirm_kill,data.get("id"))
        except (OSError, sqlite3.Error) as exc:
            return web.json_response({"error": "통계 저장소 접근 실패: " + str(exc)}, status=503)
        result["profile"] = self.agent.profile.name
        result["current"] = self.agent.web_snapshot()["object_counts"]
        result["memory_records"] = len(self.agent.object_memory.all_memory_ids())
        return web.json_response(result)

    async def game_settings(self, request):
        docs, revisions = await asyncio.to_thread(self.agent.store.snapshot)
        return web.json_response({"profile":self.agent.profile.name, "revisions":revisions, "input":docs["input.json"], "navigation":docs["navigation.json"], "window_titles":docs["vision.json"]["window_titles"], "detector":{k:docs["vision.json"]["yolo"][k] for k in ("enabled","model_path","confidence")}, "class_rules":docs["vision.json"].get("class_rules", {}), "hud_layout":docs["hud.json"].get("layout","classic"), "buff_region":docs["hud.json"]["regions"].get("buffs", {"visible":False,"bbox":None,"confidence":0})})

    async def save_game_settings(self, request):
        data = await request.json()
        if data.get("profile") != self.agent.profile.name or not isinstance(data.get("revisions"), dict):
            raise ValueError("현재 게임 설정을 불러온 뒤 저장하세요.")
        # Validate the entire transaction before pausing or changing any live state.
        for name in ("input", "navigation"):
            validate_document(name+".json", data.get(name))
        region = data.get("buff_region")
        docs, _ = await asyncio.to_thread(self.agent.store.snapshot)
        hud = copy.deepcopy(docs["hud.json"])
        hud["regions"]["buffs"] = region
        hud["layout"] = data.get("hud_layout", hud.get("layout", "classic"))
        layout_changed = hud["layout"] != docs["hud.json"].get("layout", "classic")
        if layout_changed:
            for meter in ("health", "sp", "mp"): hud["regions"].pop(meter, None)
            hud["hud_bbox"] = None
            hud["calibration"]["validated"] = False
            hud["calibration"]["source"] = "layout-changed"
        validate_document("hud.json", hud)
        vision=copy.deepcopy(docs["vision.json"])
        if "window_titles" in data:
            vision["window_titles"]=data["window_titles"]
        if "detector" in data:
            value=data["detector"]
            if not isinstance(value,dict) or set(value)!={"enabled","model_path","confidence"}:raise ValueError("객체 탐지 설정 형식 오류")
            vision["yolo"].update(value)
        if "class_rules" in data:
            vision["class_rules"] = data["class_rules"]
        validate_document("vision.json",vision)
        await self.agent.handle_control("/stop")
        async with self.lock:
            if data.get("profile") != self.agent.profile.name:raise ValueError("게임이 변경되었습니다. 설정을 다시 불러오세요.")
            operations = []
            if "window_titles" in data:
                operations.append({"file":"vision.json","op":"replace","path":"/window_titles","value_json":json.dumps(data["window_titles"],ensure_ascii=False)})
            if "class_rules" in data:
                operations.append({"file":"vision.json","op":"add","path":"/class_rules","value_json":json.dumps(vision["class_rules"],ensure_ascii=False)})
            if "detector" in data:
                operations.append({"file":"vision.json","op":"replace","path":"/yolo","value_json":json.dumps(vision["yolo"],ensure_ascii=False)})
            for name in ("input", "navigation"):
                operations.extend({"file":name+".json","op":"add","path":"/"+key,"value_json":json.dumps(value,ensure_ascii=False)} for key,value in data[name].items() if key != "version")
            for key in ("layout","regions","hud_bbox","calibration"):
                operations.append({"file":"hud.json","op":"add","path":"/"+key,"value_json":json.dumps(hud[key],ensure_ascii=False)})
            self.agent._profile_write_in_progress = True
            try:
                saved = await self.agent.commit_profile_operations(operations, data["revisions"])
                await asyncio.to_thread(self.agent.buff_monitor.reload)
                self.agent._buff_at = 0
                self.agent._buff_preview = None
            finally:
                self.agent._profile_write_in_progress = False
        self.agent.emit_web_event("profile_saved", saved=saved)
        return web.json_response({"saved":saved,"paused":True,"profile":self.agent.profile.name})

    async def buff_frame(self, request):
        # Compatibility endpoint: buff capture and recognition have been removed.
        return web.Response(status=204)

    async def tuning(self, request):
        data = await request.json()
        c = data.get("config", {})
        if (
            not all(
                number(c.get(k), lo, hi)
                for k, lo, hi in [
                    ("fps", 15, 120),
                    ("interval", 0.05, 2),
                    ("vlInterval", 0.5, 10),
                    ("x", 0, 90),
                    ("y", 0, 90),
                    ("w", 10, 100),
                    ("h", 10, 100),
                ]
            )
            or c.get("size") not in {320, 448, 640, 960}
            or c["x"] + c["w"] > 100
            or c["y"] + c["h"] > 100
            or type(c.get("gateY")) is not bool
            or type(c.get("gateVL")) is not bool
        ):
            raise ValueError("FPS·ROI·주기·크기·게이팅 범위를 확인하세요.")
        await self.agent.handle_control("/stop")
        async with self.lock:
            docs, revisions = await asyncio.to_thread(self.agent.store.snapshot)
            values = {
                "/capture_fps": c["fps"],
                "/learning_interval": c["vlInterval"],
                "/motion_gating": c["gateY"],
                "/semantic_gating": c["gateVL"],
                "/yolo/interval": c["interval"],
                "/yolo/image_size": c["size"],
                "/yolo/roi_x": c["x"] / 100,
                "/yolo/roi_y": c["y"] / 100,
                "/yolo/roi_width": c["w"] / 100,
                "/yolo/roi_height": c["h"] / 100,
            }
            operations = [
                {
                    "file": "vision.json",
                    "op": "add",
                    "path": path,
                    "value_json": json.dumps(value),
                }
                for path, value in values.items()
            ]
            self.agent._profile_write_in_progress = True
            try:
                saved = await self.agent.commit_profile_operations(
                    operations, revisions
                )
            finally:
                self.agent._profile_write_in_progress = False
        self.agent.emit_web_event("profile_saved", saved=saved)
        return web.json_response(
            {
                "saved": saved,
                "paused": True,
                "applied": self.snapshot()["settings"],
                "note": "FPS·ROI·YOLO 주기·이미지 크기·Qwen 주기·게이팅을 적용했습니다. 통과율은 추정 가정이며 실행 설정이 아닙니다. 고정 ONNX 크기와 다르면 같은 이름의 PT 모델을 사용합니다.",
            }
        )

    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=3, max_msg_size=8192)
        # Headers must be set before upgrade.
        self.cors(ws, request.headers.get("Origin"))
        await ws.prepare(request)
        try:
            first = await asyncio.wait_for(ws.receive(), 5)
            data = json.loads(first.data) if first.type == WSMsgType.TEXT else {}
            if not isinstance(data.get("token"), str) or not hmac.compare_digest(
                data["token"], self.token
            ):
                await ws.close(code=1008, message=b"Authentication required")
                return ws
            self.sockets.add(ws)
            while not ws.closed:
                if self.hp_disconnect_reason or not hmac.compare_digest(data["token"], self.token):
                    await ws.close(code=1008, message=b"Session expired")
                    break
                self.touch()
                await ws.send_json(self.snapshot())
                try:
                    message = await asyncio.wait_for(ws.receive(), 0.5)
                    if message.type in {
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSED,
                        WSMsgType.ERROR,
                    }:
                        break
                except asyncio.TimeoutError:
                    pass
        except (asyncio.TimeoutError, ValueError, ConnectionError):
            pass
        finally:
            self.sockets.discard(ws)
            await ws.close()
        return ws

    async def local_page(self, request):
        if self.dashboard.available:
            return await self.dashboard.index(request)
        return web.Response(
            text=LOCAL_PAGE,
            content_type="text/html",
            headers={
                "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
            },
        )

    async def _check_hp_connection(self, now=None):
        """Independent deadline: stop processing while retaining the session."""
        now = time.monotonic() if now is None else now
        if self.last_seen is None or self.hp_disconnect_reason:
            return
        a = self.agent
        if getattr(a,'movement_test_mode',False):
            self.hp_missing_since=None
            return
        # Game-focused play must not be halted by the web HP deadline.
        # Capture/HUD workers and per-input validation remain independent.
        if a._foreground() or a._processing_halted or getattr(a,'_focus_paused',False):
            self.hp_missing_since = None
            return
        hud = a._latest_hud_state
        valid = (a._hud_ready and not a._processing_halted and not a._hud_rechecking
                 and a._latest_frame is not None and now-a._capture_at < 1
                 and now-a._hud_at < .5 and hud.health_valid and hud.health is not None)
        if valid:
            self.hp_missing_since = None
            return
        if self.hp_missing_since is None:
            self.hp_missing_since = now
        if now-self.hp_missing_since < self.hp_timeout:
            return
        reason = "HP바를 15초 이상 찾지 못해 즉시 중단했습니다. 게임 화면과 HUD 설정을 확인한 뒤 시작/재개하세요."
        await a._halt_processing(reason)
        self.hp_missing_since = None
        a.emit_web_event("hp_timeout_stopped", message=reason)

    async def _watch(self):
        while True:
            await self._check_hp_connection()
            if (
                self.last_seen is not None
                and not self.lease_lost
                and time.monotonic() - self.last_seen > self.timeout
            ):
                await self.agent.handle_control("/stop")
                self.lease_lost = True
                self.agent.emit_web_event(
                    "connection_lost",
                    message="웹 연결이 끊겨 자동 입력을 중단했습니다.",
                )
            await asyncio.sleep(0.25)

    async def _gpu(self):
        while True:
            if self.agent._processing_halted:
                self.gpu = None
                await asyncio.sleep(0.5)
                continue
            try:
                process = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    **(
                        {"creationflags": 0x08000000}
                        if __import__("os").name == "nt"
                        else {}
                    ),
                )
                try:
                    output, _ = await asyncio.wait_for(process.communicate(), 2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    process.kill()
                    await process.communicate()
                    raise
                if process.returncode == 0:
                    values = [float(v) for v in output.decode().strip().splitlines()]
                    # Overall busiest GPU; includes the game and other applications.
                    self.gpu = max(values) if values else None
                    self.gpu_at = time.monotonic()
            except (FileNotFoundError, ValueError, OSError, asyncio.TimeoutError):
                self.gpu = None
            await asyncio.sleep(2)

    async def start(self):
        access_logger = logging.getLogger("aiohttp.access")
        self.runner = web.AppRunner(
            self.app,
            access_log=(
                access_logger if access_logger.isEnabledFor(logging.DEBUG) else None
            ),
        )
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        except BaseException:
            await self.runner.cleanup()
            raise
        self.port = self.runner.addresses[0][1]
        self.origins.update({f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"})
        self.watchdog = asyncio.create_task(self._watch())
        self.gpu_task = asyncio.create_task(self._gpu())
        print(f"[WEB] 연결 주소: http://127.0.0.1:{self.port}")
        if self.dashboard.available:
            print(f"[WEB] 로컬 대시보드: http://127.0.0.1:{self.port}/#game")
        else:
            print("[WEB] 전체 대시보드가 없습니다. Visual-Agent-Lab-local/dist 폴더를 확인하세요.")
        print("[WEB] 토큰 입력 없이 허용된 웹페이지에서 연결합니다.")
        mode='기본 자동사냥 · 게임 활성화 후 센서 확인' if getattr(self.agent,'default_auto_hunt',False) else '시작 전 일시정지'
        print(f"[WEB] 비공개 대시보드: {SITE_ORIGIN} | {mode}")

    async def close(self):
        for task in (self.watchdog, self.gpu_task):
            if task:
                task.cancel()
        await asyncio.gather(
            *(t for t in (self.watchdog, self.gpu_task) if t), return_exceptions=True
        )
        for ws in list(self.sockets):
            await ws.close()
        if self.runner:
            await self.runner.cleanup()


LOCAL_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Visual Agent 로컬 연결</title><style>body{background:#151a1e;color:#e7ebed;font:16px sans-serif;max-width:850px;margin:40px auto;padding:20px}button,input{font:inherit;padding:12px;margin:5px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#9cebb6}</style><h1>로컬 Agent 연결</h1><p>Agent 연결을 누르세요. 현재 상태와 즉시 중단을 확인할 수 있습니다.</p><button id="connect">연결</button><button id="stop">즉시 중단</button><p><a href="https://qwen-visual-agent-lab.kiwimaru.chatgpt.site" target="_blank" rel="noopener">비공개 대시보드 열기</a></p><pre id="state">미연결</pre><script>let socket;let token='';const state=document.getElementById('state');document.getElementById('connect').onclick=async()=>{try{const r=await fetch('/v1/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({connect:true})});const data=await r.json();if(!r.ok)throw new Error(data.error||'연결 실패');token=data.token;socket?.close();socket=new WebSocket('ws://'+location.host+'/v1/ws');socket.onopen=()=>socket.send(JSON.stringify({token}));socket.onmessage=e=>state.textContent=JSON.stringify(JSON.parse(e.data),null,2);socket.onclose=()=>state.textContent='연결 종료';}catch(e){state.textContent=e.message}};document.getElementById('stop').onclick=async()=>{try{const r=await fetch('/v1/control',{method:'POST',headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({action:'stop'})});state.textContent=JSON.stringify(await r.json(),null,2)}catch(e){state.textContent=e.message}};</script></html>"""
