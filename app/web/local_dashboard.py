"""Built local dashboard and profile-local experiment storage, served by aiohttp."""
from __future__ import annotations

import asyncio
import html
import json
import math
from datetime import datetime
from pathlib import Path
from uuid import UUID

from aiohttp import web
from app.profiles.profile_store import atomic_json

PAGE_PRESENCE_SCRIPT = """<script>(()=>{
const report=()=>fetch('/v1/browser-presence',{headers:{'X-Agent-Page':'1'},cache:'no-store',signal:AbortSignal.timeout(1500)}).catch(()=>{});
report();setInterval(report,1000);window.addEventListener('pageshow',report);document.addEventListener('visibilitychange',report);
})();</script>"""

def validate_experiment(value):
    if not isinstance(value, dict):
        raise ValueError("실험 입력 형식을 확인하세요.")
    UUID(value["id"])
    name = value["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("실험 이름을 확인하세요.")
    if not isinstance(value["note"], str) or len(value["note"]) > 500:
        raise ValueError("실험 메모를 확인하세요.")
    created = datetime.fromisoformat(value["createdAt"].replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("기록일에 시간대가 필요합니다.")
    groups = (
        ("config", (("fps", 15, 120), ("x", 0, 90), ("y", 0, 90),
                    ("w", 10, 100), ("h", 10, 100), ("interval", .05, 2),
                    ("vlInterval", .5, 10), ("passY", 5, 100), ("passVL", 5, 100))),
        ("assumptions", (("baseGpu", 0, 100), ("hudMs", 0, 100),
                         ("yoloMs", .1, 500), ("vlMs", 1, 60000), ("gpuScale", .1, 3))),
        ("measured", (("gpu", 0, 100), ("latency", 0, 60000),
                      ("fps", 0, 240), ("vl", 0, 120000))),
    )
    for group, ranges in groups:
        for key, lo, hi in ranges:
            n = value[group][key]
            if group == "measured" and n is None:
                continue
            if type(n) not in (int, float) or not math.isfinite(n) or not lo <= n <= hi:
                raise ValueError("튜닝·실측 범위를 확인하세요.")
    c = value["config"]
    if (c["size"] not in (320, 448, 640, 960)
            or type(c["gateY"]) is not bool or type(c["gateVL"]) is not bool
            or c["x"] + c["w"] > 100 or c["y"] + c["h"] > 100):
        raise ValueError("ROI 또는 게이팅 설정을 확인하세요.")
    return {**value, "name": name.strip()}


class LocalDashboard:
    def __init__(self, directory: Path, profile_directory: Path):
        self.directory = Path(directory).resolve()
        self.records_path = Path(profile_directory) / "tuning_experiments.json"
        self.lock = asyncio.Lock()

    @property
    def available(self):
        return (self.directory / "index.html").is_file()

    def _file(self, relative):
        path = (self.directory / relative).resolve()
        if not path.is_relative_to(self.directory) or not path.is_file():
            raise web.HTTPNotFound(text="웹 파일을 찾을 수 없습니다.")
        return path

    async def index(self, request):
        page = await asyncio.to_thread(self._file("index.html").read_text, encoding="utf-8")
        # This lets custom --web-port values work without changing compiled JS.
        origin = html.escape(str(request.url.origin()), quote=True)
        page = page.replace("</head>", f'<meta name="visual-agent-url" content="{origin}">{PAGE_PRESENCE_SCRIPT}</head>')
        return web.Response(text=page, content_type="text/html")

    async def asset(self, request):
        relative = "assets/" + request.match_info["path"] if "path" in request.match_info else "favicon.svg"
        return web.FileResponse(self._file(relative))

    def _read_records(self):
        try:
            records = json.loads(self.records_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        if not isinstance(records, list):
            raise ValueError("실험 기록 파일 형식을 확인하세요.")
        return records

    async def experiments(self, request):
        # The dashboard API is same-origin only, including writes before Agent pairing.
        origin = request.headers.get("Origin")
        if origin and origin != str(request.url.origin()):
            raise web.HTTPForbidden(text="로컬 대시보드에서 요청하세요.")
        if request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            raise web.HTTPForbidden(text="로컬 대시보드에서 요청하세요.")
        if request.method != "GET" and request.headers.get("X-Lab-Request") != "1":
            raise web.HTTPForbidden(text="요청이 허용되지 않습니다.")
        experiment = None
        if request.method == "POST":
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType()
            if request.content_length and request.content_length > 60000:
                raise web.HTTPRequestEntityTooLarge(max_size=60000, actual_size=request.content_length)
            data = bytearray()
            async for chunk in request.content.iter_chunked(16384):
                data.extend(chunk)
                if len(data) > 60000:
                    raise web.HTTPRequestEntityTooLarge(max_size=60000, actual_size=len(data))
            experiment = validate_experiment(json.loads(data))
        elif request.method == "DELETE":
            UUID(request.query.get("id", ""))
        async with self.lock:
            requested_game=request.query.get("game") or (experiment or {}).get("game")
            if requested_game and requested_game != self.records_path.parent.name:
                raise web.HTTPConflict(text="게임이 변경되었습니다. 기록을 다시 불러오세요.")
            records = await asyncio.to_thread(self._read_records)
            if request.method == "GET":
                return web.json_response({"experiments": sorted(records, key=lambda e: e["createdAt"], reverse=True)})
            record_id = experiment["id"] if experiment else request.query["id"]
            records = [e for e in records if e["id"] != record_id]
            if experiment:
                records.append(experiment)
            await asyncio.to_thread(atomic_json, self.records_path, records)
        return web.json_response({"experiment": experiment} if experiment else {"ok": True}, status=201 if experiment else 200)
