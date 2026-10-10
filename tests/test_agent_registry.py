import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.ai.agent_registry import agent_class
from app.ai.profile_agent import ProfileAgent
from app.profiles.agent_runtime import load_agent_runtime


class AgentRegistryTests(unittest.TestCase):
    def test_diablo4_implementation_is_profile_local(self):
        cls = agent_class('diablo4')
        self.assertEqual(cls.__module__, 'app.profiles.diablo4.agent')
        from app.ai.main_agent import MainAgent
        self.assertIs(MainAgent, cls)

    def test_diablo2_does_not_import_diablo4_agent(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            "import sys; from app.ai.agent_registry import agent_class; "
            "cls=agent_class('diablo2'); "
            "assert cls.__module__=='app.profiles.diablo2.agent'; "
            "assert 'app.profiles.diablo4.agent' not in sys.modules; "
            "assert 'app.ai.main_agent' not in sys.modules"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_settings_are_independent(self):
        root = Path(__file__).resolve().parents[1] / 'app/profiles'
        d4 = load_agent_runtime(root / 'diablo4')
        d2 = load_agent_runtime(root / 'diablo2')
        self.assertEqual(d4['scene']['max_objects'], 18)
        self.assertNotIn('scene', d2)
        self.assertFalse(d2['agent']['hud_mode'])

    def test_diablo2_shell_blocks_game_inputs(self):
        agent = agent_class('diablo2').__new__(agent_class('diablo2'))
        for action in ('MOVE', 'ATTACK', 'USE_SKILL', 'DODGE', 'INTERACT', 'USE_POTION'):
            self.assertFalse(agent.can_execute(SimpleNamespace(action_type=action)))
        self.assertTrue(agent.can_execute(SimpleNamespace(action_type='STOP')))

    def test_cross_game_switch_stops_and_saves_selection_for_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'diablo2').mkdir()
            agent = ProfileAgent.__new__(ProfileAgent)
            agent.profile_root = root
            agent.profile = SimpleNamespace(name='diablo4')
            agent.handle_control = AsyncMock(return_value=True)
            agent.statistics = SimpleNamespace(flush=Mock())
            agent.emit_web_event = Mock()
            self.assertTrue(asyncio.run(agent._request_runtime_switch('diablo2')))
            agent.handle_control.assert_awaited_once_with('/stop')
            self.assertTrue(agent._runtime_restart_required)
            self.assertTrue(agent._processing_halted)
            self.assertEqual(agent.profile.name, 'diablo4')
            self.assertEqual(json.loads((root / 'active_game.json').read_text()), {'id': 'diablo2'})

    def test_old_diablo4_agent_cannot_resume_after_game_switch(self):
        cls = agent_class('diablo4')
        agent = cls.__new__(cls)
        agent._runtime_restart_required = True
        agent._halt_reason = 'restart required'
        agent.emit_web_event = Mock()
        self.assertTrue(asyncio.run(agent.handle_control('/hunt')))
        self.assertFalse(agent.can_execute(SimpleNamespace(action_type='ATTACK')))


if __name__ == '__main__':
    unittest.main()
