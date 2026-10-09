from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit
from aiohttp import web, WSMsgType
from app.profiles.runtime_settings import number

SITE_ORIGIN = "https://qwen-visual-agent-lab.kiwimaru.chatgpt.site"


class AgentBridge:
    """Runs on the Agent event loop; no model work in control handlers."""

    def __init__(self, agent, *, port=8765, token=None, origins=(), timeout=8):
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
        self.runner = None
        self.watchdog = None
        self.gpu_task = None
        self.gpu = None
        self.gpu_at = 0
        self.lock = asyncio.Lock()
        self.sockets = set()
        self.app = web.Application(
            middlewares=[self.security], client_max_size=2_500_000
        )
        self.app.router.add_get("/", self.local_page)
        self.app.router.add_get("/v1/state", self.state)
        self.app.router.add_post("/v1/control", self.control)
        self.app.router.add_post("/v1/chat", self.chat)
        self.app.router.add_get("/v1/profiles", self.profiles)
        self.app.router.add_patch("/v1/profiles", self.patch_profiles)
        self.app.router.add_post("/v1/tuning", self.tuning)
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
        if request.method != "OPTIONS" and request.path not in {"/", "/v1/ws"}:
            if not self.authorized(request):
                response = web.json_response(
                    {"error": "연결 토큰을 확인하세요."}, status=401
                )
                return self.cors(response, origin)
            self.touch()
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        except web.HTTPException as exc:
            response = exc
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

    async def state(self, request):
        return web.json_response(self.snapshot())

    def snapshot(self):
        result = self.agent.web_snapshot()
        result["metrics"]["gpu"] = (
            self.gpu if time.monotonic() - self.gpu_at < 5 else None
        )
        result["connection"] = {
            "lease_timeout_seconds": self.timeout,
            "lease_lost": self.lease_lost,
        }
        return result

    async def control(self, request):
        data = await request.json()
        value = data.get("action")
        commands = {
            "start": "/resume",
            "resume": "/resume",
            "stop": "/stop",
            "pause": "/stop",
        }
        if value not in commands:
            raise ValueError("start/resume/stop/pause만 가능합니다.")
        await self.agent.handle_control(commands[value])
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
            self.agent.emit_web_event(
                "chat_result",
                request_id=request_id,
                reply="제어 명령을 적용했습니다.",
                saved=[],
                directive={"action": "NONE"},
            )
        else:
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
        return web.Response(
            text=LOCAL_PAGE,
            content_type="text/html",
            headers={
                "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
            },
        )

    async def _watch(self):
        while True:
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
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        except BaseException:
            await self.runner.cleanup()
            raise
        self.watchdog = asyncio.create_task(self._watch())
        self.gpu_task = asyncio.create_task(self._gpu())
        print(f"[WEB] 연결 주소: http://127.0.0.1:{self.port}")
        print(f"[WEB] 연결 토큰: {self.token}")
        print(f"[WEB] 비공개 대시보드: {SITE_ORIGIN} | 시작 전 일시정지")

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


LOCAL_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Visual Agent 로컬 연결</title><style>body{background:#151a1e;color:#e7ebed;font:16px sans-serif;max-width:850px;margin:40px auto;padding:20px}button,input{font:inherit;padding:12px;margin:5px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#9cebb6}</style><h1>로컬 Agent 연결</h1><p>콘솔 토큰을 입력하세요. 현재 상태와 즉시 중단을 확인할 수 있습니다.</p><input id="token" type="password" aria-label="연결 토큰"><button id="connect">연결</button><button id="stop">즉시 중단</button><p><a href="https://qwen-visual-agent-lab.kiwimaru.chatgpt.site" target="_blank" rel="noopener">비공개 대시보드 열기</a></p><pre id="state">미연결</pre><script>let socket;const field=document.getElementById('token'),state=document.getElementById('state');document.getElementById('connect').onclick=()=>{socket?.close();socket=new WebSocket('ws://'+location.host+'/v1/ws');socket.onopen=()=>socket.send(JSON.stringify({token:field.value}));socket.onmessage=e=>state.textContent=JSON.stringify(JSON.parse(e.data),null,2);socket.onclose=()=>state.textContent='연결 종료';};document.getElementById('stop').onclick=async()=>{try{const r=await fetch('/v1/control',{method:'POST',headers:{'Authorization':'Bearer '+field.value,'Content-Type':'application/json'},body:JSON.stringify({action:'stop'})});state.textContent=JSON.stringify(await r.json(),null,2)}catch(e){state.textContent=e.message}};</script></html>"""
