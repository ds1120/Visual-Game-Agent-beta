from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from app.ai.visual_agent import VisualAgent
from app.core.action_executor import ActionExecutor
from app.core.action_scheduler import ActionScheduler
from app.core.game_state import HUDState
from app.profiles.profile_store import FILES, ProfileStore
from app.profiles.runtime_settings import ensure_runtime_settings
from app.web.bridge import AgentBridge, SITE_ORIGIN

ROOT = Path(__file__).resolve().parents[1]


class Controller:
    def __init__(self):
        self.calls = []

    async def stop(self, command):
        self.calls.append(command.action_type)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        for name in FILES:
            shutil.copy(ROOT / "app/profiles/generic" / name, self.directory / name)
        ensure_runtime_settings(self.directory)
        profile = types.SimpleNamespace(
            name="generic", profile_dir=self.directory, set_hud_regions=lambda x: None
        )
        self.input = Controller()
        self.scheduler = ActionScheduler(ActionExecutor(self.input))
        self.agent = VisualAgent(
            Mock(can_input=lambda: True),
            Mock(),
            self.scheduler,
            profile=profile,
            console_enabled=False,
            yolo_config={"enabled": False},
        )
        self.agent._paused = True
        self.agent._running = True
        self.bridge = AgentBridge(
            self.agent, token="test-token-not-a-production-secret", timeout=0.1
        )
        self.client = TestClient(TestServer(self.bridge.app))
        await self.client.start_server()
        self.headers = {
            "Authorization": "Bearer " + self.bridge.token,
            "Origin": SITE_ORIGIN,
        }
        self.worker = None

    async def asyncTearDown(self):
        self.agent._running = False
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self.client.close()
        await self.scheduler.stop()
        self.agent._capture_executor.shutdown(wait=True)
        self.agent._yolo_executor.shutdown(wait=True)
        self.agent._hud_executor.shutdown(wait=True)
        self.temp.cleanup()

    async def test_authentication_origin_and_rebinding_rejected(self):
        response = await self.client.get("/v1/state")
        self.assertEqual(response.status, 401)
        response = await self.client.get(
            "/v1/state", headers={**self.headers, "Origin": "https://untrusted.example"}
        )
        self.assertEqual(response.status, 403)
        response = await self.client.get(
            "/v1/state", headers={**self.headers, "Host": "untrusted.example"}
        )
        self.assertEqual(response.status, 403)
        response = await self.client.get("/v1/state", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], SITE_ORIGIN)
        self.assertNotIn(self.bridge.token, await response.text())

    async def test_local_dashboard_origin(self):
        for origin in ("http://127.0.0.1:3000", "http://localhost:3000"):
            response = await self.client.get(
                "/v1/state", headers={**self.headers, "Origin": origin}
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        response = await self.client.get(
            "/v1/state", headers={**self.headers, "Origin": "http://localhost:3001"}
        )
        self.assertEqual(response.status, 403)

    async def test_private_network_preflight(self):
        response = await self.client.options(
            "/v1/control",
            headers={
                "Origin": SITE_ORIGIN,
                "Access-Control-Request-Private-Network": "true",
            },
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(
            response.headers["Access-Control-Allow-Private-Network"], "true"
        )

    async def test_start_stop_invalidates_pending_inference(self):
        epoch = self.agent._epoch
        response = await self.client.post(
            "/v1/control", headers=self.headers, json={"action": "start"}
        )
        self.assertEqual(response.status, 200)
        self.assertFalse(self.agent._paused)
        response = await self.client.post(
            "/v1/control", headers=self.headers, json={"action": "stop"}
        )
        self.assertTrue((await response.json())["state"]["paused"])
        self.assertGreater(self.agent._epoch, epoch)

    async def test_live_tuning_saved_and_applied_to_detector(self):
        config = {
            "fps": 60,
            "x": 15,
            "y": 15,
            "w": 70,
            "h": 65,
            "interval": 0.3,
            "size": 448,
            "vlInterval": 2,
            "gateY": True,
            "gateVL": True,
            "passY": 65,
            "passVL": 22,
        }
        response = await self.client.post(
            "/v1/tuning", headers=self.headers, json={"config": config}
        )
        data = await response.json()
        self.assertEqual(response.status, 200, data)
        self.assertEqual(self.agent.yolo.image_size, 448)
        self.assertEqual(self.agent.yolo.roi_x, 0.15)
        self.assertEqual(self.agent.yolo.interval, 0.3)
        docs, _ = self.agent.store.snapshot()
        self.assertEqual(docs["vision.json"]["learning_interval"], 2)
        self.assertTrue(docs["vision.json"]["motion_gating"])
        self.assertTrue(self.agent._paused)
        self.assertTrue(list((self.directory / "_backups").rglob("vision.json")))

    async def test_semantic_gate_off_persists_and_reports_actual_setting(self):
        config = {
            "fps": 45,
            "x": 0,
            "y": 0,
            "w": 100,
            "h": 100,
            "interval": 0.05,
            "size": 320,
            "vlInterval": 0.5,
            "gateY": False,
            "gateVL": False,
        }
        response = await self.client.post(
            "/v1/tuning", headers=self.headers, json={"config": config}
        )
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertFalse(result["applied"]["gateVL"])
        self.assertEqual(self.agent.yolo.interval, 0.05)
        docs, _ = self.agent.store.snapshot()
        self.assertFalse(docs["vision.json"]["semantic_gating"])

    async def test_model_enable_failure_does_not_save_or_claim_applied(self):
        before = (self.directory / "vision.json").read_bytes()
        docs, revisions = self.agent.store.snapshot()
        operations = [
            {
                "file": "vision.json",
                "op": "replace",
                "path": "/yolo/enabled",
                "value_json": "true",
            },
            {
                "file": "vision.json",
                "op": "replace",
                "path": "/yolo/model_path",
                "value_json": '"missing/model.onnx"',
            },
        ]
        response = await self.client.patch(
            "/v1/profiles",
            headers=self.headers,
            json={"operations": operations, "revisions": revisions},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(before, (self.directory / "vision.json").read_bytes())
        self.assertFalse(self.agent.yolo.enabled)

    async def test_live_enable_and_window_titles_apply_without_restart(self):
        from unittest.mock import patch
        from app.vision.yolo_sensor import YOLOSensor

        sensor = YOLOSensor(
            enabled=False,
            model_path=str(ROOT / "yolo/models/best.pt"),
            confidence=0.7,
            image_size=640,
            device="cpu",
        )
        sensor.enabled = True
        self.agent.capture.window_titles = ["old"]
        docs, revisions = self.agent.store.snapshot()
        settings = {
            "/yolo/enabled": True,
            "/yolo/model_path": "yolo/models/best.pt",
            "/yolo/confidence": 0.7,
            "/yolo/image_size": 640,
            "/yolo/device": "cpu",
            "/window_titles": ["Moonlight"],
        }
        operations = [
            {
                "file": "vision.json",
                "op": "replace",
                "path": k,
                "value_json": json.dumps(v),
            }
            for k, v in settings.items()
        ]
        with patch("app.ai.visual_agent.YOLOSensor", return_value=sensor) as loader:
            response = await self.client.patch(
                "/v1/profiles",
                headers=self.headers,
                json={"operations": operations, "revisions": revisions},
            )
        self.assertEqual(response.status, 200, await response.text())
        loader.assert_called_once()
        self.assertIs(self.agent.yolo, sensor)
        self.assertEqual(self.agent.capture.window_titles, ["Moonlight"])
        self.agent.capture._invalidate_window.assert_called_once()
        self.assertFalse(self.agent.web_snapshot()["restart_required"])

    async def test_invalid_tuning_does_not_modify_profiles(self):
        before = (self.directory / "vision.json").read_bytes()
        response = await self.client.post(
            "/v1/tuning", headers=self.headers, json={"config": {"fps": 500}}
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(before, (self.directory / "vision.json").read_bytes())

    async def test_profile_patch_and_stale_revision_rejected(self):
        response = await self.client.get("/v1/profiles", headers=self.headers)
        data = await response.json()
        patch = {
            "operations": [
                {
                    "file": "hunting.json",
                    "op": "replace",
                    "path": "/policy/potion_hp_threshold",
                    "value_json": "35",
                }
            ],
            "revisions": data["revisions"],
        }
        response = await self.client.patch(
            "/v1/profiles", headers=self.headers, json=patch
        )
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(
            self.agent._docs["hunting.json"]["policy"]["potion_hp_threshold"], 35
        )
        response = await self.client.patch(
            "/v1/profiles", headers=self.headers, json=patch
        )
        self.assertEqual(response.status, 400)

    async def test_chat_saves_json_and_returns_correlated_event(self):
        docs, revisions = self.agent.store.snapshot()
        self.agent._latest_frame = np.zeros((60, 100, 3), dtype=np.uint8)
        result = {
            "reply": "물약 기준을 35%로 저장합니다.",
            "operations": [
                {
                    "file": "hunting.json",
                    "op": "replace",
                    "path": "/policy/potion_hp_threshold",
                    "value_json": "35",
                }
            ],
            "directive": {
                "action": "NONE",
                "track_id": None,
                "direction": None,
                "ttl_seconds": 1,
            },
            "revisions": revisions,
            "elapsed_ms": 12,
        }
        self.agent.conversation.propose = lambda *args: result
        self.worker = asyncio.create_task(self.agent._conversation_loop())
        response = await self.client.post(
            "/v1/chat",
            headers=self.headers,
            json={"id": "chat-1", "message": "물약 기준 35%"},
        )
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(self.agent._chat_queue.join(), 1)
        response = await self.client.get("/v1/state", headers=self.headers)
        state = await response.json()
        event = next(e for e in state["events"] if e["type"] == "chat_result")
        self.assertEqual(event["request_id"], "chat-1")
        self.assertEqual(event["saved"], ["hunting.json"])
        self.assertEqual(state["metrics"]["vl"], 12)
        self.assertEqual(
            self.agent.store.snapshot()[0]["hunting.json"]["policy"][
                "potion_hp_threshold"
            ],
            35,
        )

    async def test_web_stop_does_not_wait_for_slow_model(self):
        self.agent._latest_frame = np.zeros((60, 100, 3), dtype=np.uint8)
        started = threading.Event()
        release = threading.Event()

        def propose(*args):
            started.set()
            release.wait(2)
            return {
                "reply": "이동 제안",
                "operations": [],
                "directive": {
                    "action": "MOVE",
                    "track_id": None,
                    "direction": [1, 0],
                    "ttl_seconds": 3,
                },
                "revisions": {},
                "elapsed_ms": 1000,
            }

        self.agent.conversation.propose = propose
        self.worker = asyncio.create_task(self.agent._conversation_loop())
        await self.client.post(
            "/v1/chat",
            headers=self.headers,
            json={"id": "slow", "message": "오른쪽 이동"},
        )
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.1)
            begin = time.monotonic()
            response = await self.client.post(
                "/v1/control", headers=self.headers, json={"action": "stop"}
            )
            self.assertEqual(response.status, 200)
            self.assertLess(time.monotonic() - begin, 0.3)
        finally:
            release.set()
        await asyncio.wait_for(self.agent._chat_queue.join(), 1)
        self.assertIsNone(self.agent._directive)
        self.assertTrue(
            any(e["type"] == "chat_cancelled" for e in self.agent._web_events)
        )

    async def test_watchdog_stops_after_connection_loss(self):
        self.agent._paused = False
        self.bridge.last_seen = time.monotonic() - 0.5
        task = asyncio.create_task(self.bridge._watch())
        try:
            await asyncio.sleep(0.02)
            self.assertTrue(self.agent._paused)
            self.assertTrue(self.bridge.lease_lost)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_websocket_authenticated_state_stream(self):
        ws = await self.client.ws_connect("/v1/ws", headers={"Origin": SITE_ORIGIN})
        await ws.send_json({"token": self.bridge.token})
        data = await asyncio.wait_for(ws.receive_json(), 1)
        self.assertEqual(data["api_version"], 1)
        self.assertEqual(data["profile"], "generic")
        await ws.close()
        bad = await self.client.ws_connect("/v1/ws", headers={"Origin": SITE_ORIGIN})
        await bad.send_json({"token": "wrong"})
        message = await asyncio.wait_for(bad.receive(), 1)
        self.assertEqual(message.type, WSMsgType.CLOSE)
        self.assertEqual(bad.close_code, 1008)

    async def test_state_metrics_never_invent_measurements(self):
        response = await self.client.get("/v1/state", headers=self.headers)
        state = await response.json()
        self.assertIsNone(state["metrics"]["gpu"])
        self.assertIsNone(state["metrics"]["latency"])
        self.assertIsNone(state["metrics"]["fps"])
        self.assertNotIn("frame", state)
        self.agent._capture_samples.extend([time.monotonic() - 0.1, time.monotonic()])
        self.agent._hud_ready = True
        self.agent._latest_hud_state = HUDState(health=75, health_valid=True)
        self.agent._hud_at = time.monotonic()
        self.agent._yolo_at = time.monotonic()
        self.agent._hud_ms = 3
        self.agent.yolo.last_inference_ms = 8
        response = await self.client.get("/v1/state", headers=self.headers)
        state = await response.json()
        self.assertEqual(state["hud"]["health"], 75)
        self.assertEqual(state["metrics"]["latency"], 11)
        self.assertGreater(state["metrics"]["fps"], 8)


if __name__ == "__main__":
    unittest.main()
