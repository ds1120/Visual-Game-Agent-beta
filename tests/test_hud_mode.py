import asyncio
import unittest
import time
import threading
import numpy as np
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.ai.main_agent import MainAgent
from app.ai.visual_agent import VisualAgent


class HudModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_minimap_updates_without_hp_even_when_processing_is_halted(self):
        for halted in (False,True):
            agent=MainAgent.__new__(MainAgent)
            agent._running=True;agent._move_only=True;agent._paused=False
            agent._processing_halted=halted;agent._hud_ready=False
            agent._epoch=1;agent._frame_seq=1;agent._frame_identity='game'
            agent._capture_at=time.monotonic()
            agent._latest_frame=np.zeros((600,960,3),np.uint8)
            agent._focus_work_allowed=lambda:True;agent._foreground=lambda:True
            agent._docs={'navigation.json':{'minimap':{'enabled':True,'player':[.5,.5],
                         'mapping':{'enabled':True}}}}
            agent.navigation_monitor=SimpleNamespace(reset=Mock(),stuck=False)
            def update(*args):
                agent._running=False
                return np.ones((32,32),np.uint8)
            agent.minimap_memory=SimpleNamespace(update=Mock(side_effect=update),
                suspend=Mock(),snapshot=lambda:{'valid':True,'stuck':False},
                lock=threading.RLock(),position=None)
            agent._review_missing_navigation_route=Mock()
            agent._recover_rejected_movement=Mock()
            with patch('app.ai.main_agent.asyncio.sleep',new=AsyncMock()):
                await agent._navigation_loop()
            agent.minimap_memory.update.assert_called_once()
            agent.minimap_memory.suspend.assert_not_called()
            self.assertIsNotNone(agent._minimap_mask)
            agent._recover_rejected_movement.assert_not_called()

    def test_hud_measurements_do_not_change_movement_geometry(self):
        agent = MainAgent.__new__(MainAgent)
        agent.movement_test_mode = True
        agent._docs = {'navigation.json': {'steering': {
            'player_screen': [.5, .5], 'infer_player_from_hud': True}},
            'hud.json': {'regions': {'health': {'tracked_stack': False}}}}
        agent.profile = SimpleNamespace(name='diablo4')
        agent._latest_frame = SimpleNamespace(shape=(1080, 1920, 3))
        agent._objects = []
        agent._hud_at = time.monotonic()
        for valid in (False, True):
            agent._latest_hud_state = SimpleNamespace(health_valid=valid, regions={
                'health': {'tracked_stack': True, 'visible': True,
                           'bbox': [400, 400, 450, 420]}})
            self.assertEqual(agent._world_player_origin(), (.5, .5))
            with patch('app.ai.main_agent.click_exclusions', return_value=[]) as exclusions:
                self.assertEqual(agent._world_click_exclusions(), [(.47, .43, .53, .60)])
                self.assertFalse(exclusions.call_args.args[1]['regions']['health']['tracked_stack'])

    async def read_hud(self, valid=False, error=False):
        agent = MainAgent.__new__(MainAgent)
        agent._running = True
        agent._processing_halted = False
        agent._latest_frame = object()
        agent._frame_seq = 1
        agent._frame_identity = 'game'
        agent._epoch = 7
        agent._hud_executor = None
        agent.hud_interval = .001
        agent._focus_work_allowed = lambda: True
        agent.scheduler = SimpleNamespace(submit_emergency=Mock())
        agent._recheck_hud = Mock()
        agent._try_hud_probe_move = Mock()
        agent._halt_processing = Mock()
        hud = SimpleNamespace(health_valid=valid, health=1 if valid else None)

        def analyze(frame):
            agent._running = False
            if error:
                raise ValueError('HUD missing')
            return hud

        agent._analyze_hud = analyze
        if error:
            with self.assertLogs('app.ai.visual_agent', level='ERROR'):
                await agent._hud_read_loop()
        else:
            await agent._hud_read_loop()
            self.assertIs(agent._latest_hud_state, hud)
        self.assertEqual(agent._epoch, 7)
        self.assertFalse(agent._processing_halted)
        agent.scheduler.submit_emergency.assert_not_called()
        agent._recheck_hud.assert_not_called()
        agent._try_hud_probe_move.assert_not_called()
        agent._halt_processing.assert_not_called()
        return agent

    async def test_missing_hud_does_not_interrupt_movement(self):
        agent = await self.read_hud()
        self.assertFalse(agent._hud_ready)

    async def test_low_health_is_read_without_emergency_input(self):
        agent = await self.read_hud(valid=True)
        self.assertTrue(agent._hud_ready)

    async def test_hud_read_failure_does_not_interrupt_movement(self):
        agent = await self.read_hud(error=True)
        self.assertFalse(agent._hud_ready)

    async def test_both_modes_use_same_movement_workers(self):
        for hud_mode in (False, True):
            with self.subTest(hud_mode=hud_mode):
                agent = VisualAgent.__new__(VisualAgent)
                agent.movement_test_mode = True
                agent.hud_mode = hud_mode
                agent._console_enabled = False
                agent.statistics = SimpleNamespace(flush=Mock())
                called = []

                def worker(name):
                    async def loop():
                        called.append(name)
                        if name == '_capture_loop':
                            await asyncio.sleep(.001)
                            agent._running = False
                    loop.__name__ = name
                    return loop

                for name in ('_capture_loop', '_hud_loop', '_yolo_loop',
                             '_action_loop', '_calibration_loop', '_learning_loop',
                             '_refresh_loop', '_conversation_loop', '_statistics_loop',
                             '_navigation_loop', '_navigation_replan_loop',
                             '_emergency_loop', '_combat_recheck_loop',
                             '_idle_sensor_loop', '_movement_test_loop', '_hud_read_loop'):
                    setattr(agent, name, worker(name))
                agent.stop = Mock()
                await agent.run()
                self.assertIn('_movement_test_loop', called)
                self.assertIn('_navigation_loop', called)
                self.assertNotIn('_action_loop', called)
                self.assertNotIn('_calibration_loop', called)
                self.assertNotIn('_hud_loop', called)
                self.assertEqual('_hud_read_loop' in called, hud_mode)


if __name__ == '__main__':
    unittest.main()
