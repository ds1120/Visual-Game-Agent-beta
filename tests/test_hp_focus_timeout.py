import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.web.bridge import AgentBridge


class HpFocusTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self,foreground):
        bridge=AgentBridge.__new__(AgentBridge)
        bridge.last_seen=1
        bridge.hp_disconnect_reason=None
        bridge.hp_missing_since=1
        bridge.hp_timeout=15
        bridge.agent=SimpleNamespace(_foreground=lambda:foreground,
            _processing_halted=False,_focus_paused=False,_hud_ready=False,
            _latest_hud_state=SimpleNamespace(health_valid=False,health=None),
            _halt_processing=AsyncMock(),emit_web_event=Mock())
        return bridge

    async def test_game_focus_ignores_elapsed_hp_timeout(self):
        bridge=self.bridge(True)
        await bridge._check_hp_connection(now=100)
        bridge.agent._halt_processing.assert_not_awaited()
        self.assertIsNone(bridge.hp_missing_since)

    async def test_focus_return_clears_previous_missing_timer(self):
        bridge=self.bridge(False)
        bridge.hp_missing_since=None
        await bridge._check_hp_connection(now=10)
        self.assertEqual(bridge.hp_missing_since,10)
        bridge.agent._foreground=lambda:True
        await bridge._check_hp_connection(now=30)
        self.assertIsNone(bridge.hp_missing_since)
        bridge.agent._halt_processing.assert_not_awaited()

    async def test_focus_pause_does_not_accumulate_timeout(self):
        bridge=self.bridge(False)
        bridge.agent._focus_paused=True
        await bridge._check_hp_connection(now=100)
        self.assertIsNone(bridge.hp_missing_since)
        bridge.agent._halt_processing.assert_not_awaited()

    async def test_movement_test_disables_hp_watchdog_even_without_focus(self):
        bridge=self.bridge(False)
        bridge.agent.movement_test_mode=True
        await bridge._check_hp_connection(now=100)
        self.assertIsNone(bridge.hp_missing_since)
        bridge.agent._halt_processing.assert_not_awaited()
