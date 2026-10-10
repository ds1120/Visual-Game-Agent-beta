import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from app.ai.main_agent import MainAgent
from app.ai.visual_agent import VisualAgent
from app.core.fast_policy import command
from app.controller.input_bindings import binding_for_command
from app.core.minimap_memory import MinimapMemory


class ArrowEscapeTests(unittest.IsolatedAsyncioTestCase):
    def agent(self):
        a=MainAgent.__new__(MainAgent)
        a._epoch=1;a._move_only=True;a._hunt_active=True
        a.movement_test_mode=True;a._paused=False;a._processing_halted=False
        a._screen_guide_enabled=True;a.profile=SimpleNamespace(name='diablo4')
        a._latest_frame=np.zeros((600,960,3),np.uint8)
        a._world_player_origin=lambda:(.5,.5)
        a._fresh=lambda:True;a._foreground=lambda:True
        a._docs={'input.json':{'movement_skill':{'enabled':True,'key':'3'}}}
        a._screen_move_guide={'marker':(.7,.5)};a._screen_guide_seen_at=10
        a._last_arrow_move_sent=command('MOVE',source='SCREEN_ARROW_MOVE',epoch=1)
        a._last_arrow_move_sent_at=10
        a.minimap_memory=MinimapMemory()
        a.minimap_memory.valid=True;a.minimap_memory.stuck=True;a.minimap_memory.last_update=10
        a.minimap_memory.mask=np.ones((192,192),np.uint8)*255
        a.minimap_memory.grid=np.ones((48,48),np.uint8)
        a.minimap_memory.player=[.5,.5];a.minimap_memory.rotation=0
        a.scheduler=SimpleNamespace(submit_emergency=AsyncMock())
        return a

    async def test_stalled_arrow_uses_registered_skill_and_validates_target(self):
        a=self.agent()
        with patch('app.ai.main_agent.time.monotonic',return_value=10):
            move=a._arrow_move_command(a._screen_move_guide)
            self.assertTrue(await a._arrow_escape_tick(move))
            escape=a.scheduler.submit_emergency.await_args.args[0]
            self.assertEqual(binding_for_command(a._docs['input.json'],escape),'3')
            self.assertTrue(a.can_execute(escape),a._input_block_reason)
            self.assertLess(escape.direction[0],0)  # Open space behind the stalled rightward move.
            a._foreground=lambda:False
            self.assertFalse(a.can_execute(escape))
            a._foreground=lambda:True
            with patch.object(VisualAgent,'_record_input'):
                a._record_input(escape,'sent')
            self.assertFalse(a.minimap_memory.stuck)
            a.minimap_memory.stuck=True
            self.assertFalse(await a._arrow_escape_tick(move))
        a.scheduler.submit_emergency.assert_awaited_once()

    async def test_wall_blocks_old_heading_and_escape_uses_open_corridor(self):
        a=self.agent();m=a.minimap_memory
        m.grid[:]=0;m.grid[23:26,23:26]=1;m.grid[4:26,23:26]=1
        with patch('app.ai.main_agent.time.monotonic',return_value=10):
            move=a._arrow_move_command(a._screen_move_guide)
            self.assertTrue(await a._arrow_escape_tick(move))
            escape=a.scheduler.submit_emergency.await_args.args[0]
            self.assertLess(escape.direction[1],-.9)
            self.assertTrue(a.can_execute(escape),a._input_block_reason)
            m.grid[:]=0
            self.assertFalse(a.can_execute(escape))

    async def test_enclosed_player_does_not_send_blind_escape(self):
        a=self.agent();a.minimap_memory.grid[:]=0
        a.minimap_memory.grid[23:26,23:26]=1
        with patch('app.ai.main_agent.time.monotonic',return_value=10):
            self.assertTrue(await a._arrow_escape_tick(a._arrow_move_command(a._screen_move_guide)))
        a.scheduler.submit_emergency.assert_not_awaited()

    async def test_no_skill_for_unconfirmed_stall_stale_map_or_disabled_registration(self):
        for change in ('moving','stale','disabled','no_move'):
            a=self.agent()
            if change=='moving':a.minimap_memory.stuck=False
            if change=='stale':a.minimap_memory.last_update=8
            if change=='disabled':a._docs['input.json']['movement_skill']['enabled']=False
            if change=='no_move':a._last_arrow_move_sent=None
            with patch('app.ai.main_agent.time.monotonic',return_value=10):
                move=a._arrow_move_command(a._screen_move_guide)
                self.assertFalse(await a._arrow_escape_tick(move))
            a.scheduler.submit_emergency.assert_not_awaited()
