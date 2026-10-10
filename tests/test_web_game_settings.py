import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from app.web.bridge import AgentBridge


class GameSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'app/profiles/diablo4'
        self.docs = {name: json.loads((root / name).read_text(encoding='utf-8'))
                     for name in ('input.json', 'navigation.json', 'vision.json', 'hud.json')}
        self.bridge = AgentBridge.__new__(AgentBridge)
        self.bridge.lock = asyncio.Lock()
        self.bridge.agent = SimpleNamespace(
            profile=SimpleNamespace(name='diablo4'),
            store=SimpleNamespace(snapshot=Mock(return_value=(copy.deepcopy(self.docs), {}))),
            handle_control=AsyncMock(), commit_profile_operations=AsyncMock(return_value={}),
            buff_monitor=SimpleNamespace(reload=Mock()), emit_web_event=Mock())

    async def payload(self):
        response = await self.bridge.game_settings(None)
        return json.loads(response.body)

    async def test_capture_fps_persists_and_hidden_settings_survive(self):
        data = await self.payload()
        self.assertEqual(data['capture_fps'], self.docs['vision.json']['capture_fps'])
        data['capture_fps'] = 90
        await self.bridge.save_game_settings(SimpleNamespace(json=AsyncMock(return_value=data)))
        operations, _ = self.bridge.agent.commit_profile_operations.await_args.args
        fps = next(op for op in operations if op['path'] == '/capture_fps')
        self.assertEqual(json.loads(fps['value_json']), 90)
        for name in ('input', 'navigation'):
            written = {op['path'][1:]: json.loads(op['value_json']) for op in operations
                       if op['file'] == name + '.json'}
            self.assertEqual(written, {k: v for k, v in self.docs[name + '.json'].items()
                                       if k != 'version'})

    async def test_invalid_capture_fps_does_not_stop_or_write(self):
        data = await self.payload()
        data['capture_fps'] = 1000
        with self.assertRaises(ValueError):
            await self.bridge.save_game_settings(SimpleNamespace(json=AsyncMock(return_value=data)))
        self.bridge.agent.handle_control.assert_not_awaited()
        self.bridge.agent.commit_profile_operations.assert_not_awaited()
