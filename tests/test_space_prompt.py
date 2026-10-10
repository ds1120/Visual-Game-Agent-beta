import unittest
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import cv2
import numpy as np

from app.ai.main_agent import MainAgent
from app.ai.visual_agent import VisualAgent
from app.controller.input_bindings import binding_for_command
from app.core.fast_policy import command
from app.core.action_scheduler import ActionScheduler
from app.vision.space_prompt import space_prompt


class SpacePromptDetectionTests(unittest.TestCase):
    def test_small_beige_keycap_keeps_glyph_at_native_resolution(self):
        for width,height in ((1716,1074),(2560,1440)):
            frame=np.full((height,width,3),(50,70,100),np.uint8)
            x,y=width//2,height//2
            cv2.rectangle(frame,(x-1,y-1),(x+33,y+21),(35,35,35),-1)
            cv2.rectangle(frame,(x,y),(x+32,y+20),(150,170,190),-1)
            cv2.line(frame,(x+6,y+6),(x+6,y+13),(35,35,35),2)
            cv2.line(frame,(x+26,y+6),(x+26,y+13),(35,35,35),2)
            cv2.line(frame,(x+6,y+13),(x+26,y+13),(35,35,35),2)
            self.assertIsNotNone(space_prompt(frame))

    def image(self,y=350):
        frame=np.full((600,960,3),50,np.uint8)
        cv2.rectangle(frame,(470,y),(499,y+18),(180,180,180),-1)
        cv2.line(frame,(476,y+5),(476,y+12),(30,30,30),2)
        cv2.line(frame,(493,y+5),(493,y+12),(30,30,30),2)
        cv2.line(frame,(476,y+12),(493,y+12),(30,30,30),2)
        return frame

    def test_world_prompt_is_detected(self):
        self.assertIsNotNone(space_prompt(self.image()))

    def test_permanent_bottom_hud_keycap_is_ignored(self):
        self.assertIsNone(space_prompt(self.image(y=520)))

    def test_black_bars_do_not_put_permanent_hud_inside_prompt_area(self):
        world=self.image(y=505)
        frame=cv2.copyMakeBorder(world,83,83,0,0,cv2.BORDER_CONSTANT,value=0)
        self.assertLess((505+83)/frame.shape[0],.78)
        self.assertIsNone(space_prompt(frame))
        world=self.image(y=350)
        frame=cv2.copyMakeBorder(world,83,83,0,0,cv2.BORDER_CONSTANT,value=0)
        self.assertIsNotNone(space_prompt(frame))

    def test_excluded_region_and_plain_grey_rectangle_are_ignored(self):
        self.assertIsNone(space_prompt(self.image(),excluded=[(.45,.55,.55,.65)]))
        frame=self.image()
        frame[350:369,470:500]=180
        self.assertIsNone(space_prompt(frame))


class SpacePromptActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_space_survives_slow_cancellation_of_serial_movement(self):
        scheduler=ActionScheduler(SimpleNamespace());scheduler._running=True
        now=[10.]
        async def cancel():now[0]=11.
        scheduler._cancel_execution=AsyncMock(side_effect=cancel)
        with patch('time.monotonic',side_effect=lambda:now[0]):
            c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
            await scheduler.submit_emergency(c)
            _,queued=await scheduler._queue.get()
            self.assertFalse(queued.is_expired())
            self.assertEqual(queued.expires_at,11.5)
            scheduler._queue.task_done()

    def test_visible_prompt_is_rechecked_after_old_detection_timestamp(self):
        agent=self.agent();agent._space_prompt_seen_at=10
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
        with patch('app.ai.main_agent.time.monotonic',return_value=11), \
             patch('app.ai.main_agent.space_prompt',return_value=(.2,.4,.3,.5)):
            self.assertTrue(agent.can_execute(c))
            agent._foreground=lambda:False
            self.assertFalse(agent.can_execute(c))

    async def test_persistent_prompt_fires_once_then_requires_sustained_absence(self):
        agent=self.agent()
        box=(.4,.4,.5,.5)
        with patch('app.ai.main_agent.space_prompt',return_value=box), \
             patch('app.ai.main_agent.time.monotonic',return_value=10):
            self.assertTrue(await agent._space_prompt_tick())
            c=agent.scheduler.submit_emergency.await_args.args[0]
            with patch.object(VisualAgent,'_record_input'):agent._record_input(c,'sent')
        with patch('app.ai.main_agent.space_prompt',return_value=box), \
             patch('app.ai.main_agent.time.monotonic',return_value=12.1):
            self.assertFalse(await agent._space_prompt_tick())
        with patch('app.ai.main_agent.space_prompt',return_value=None), \
             patch('app.ai.main_agent.time.monotonic',return_value=12.3):
            self.assertFalse(await agent._space_prompt_tick())
        with patch('app.ai.main_agent.space_prompt',return_value=box), \
             patch('app.ai.main_agent.time.monotonic',return_value=12.5):
            self.assertFalse(await agent._space_prompt_tick())
        for now in (12.7,13.4):
            with patch('app.ai.main_agent.space_prompt',return_value=None), \
                 patch('app.ai.main_agent.time.monotonic',return_value=now):
                self.assertFalse(await agent._space_prompt_tick())
        with patch('app.ai.main_agent.space_prompt',return_value=box), \
             patch('app.ai.main_agent.time.monotonic',return_value=13.6):
            self.assertTrue(await agent._space_prompt_tick())
        self.assertEqual(agent.scheduler.submit_emergency.await_count,2)

    async def test_movement_resumes_immediately_even_with_prompt_still_visible(self):
        agent=self.agent();agent._running=True;agent._hunt_active=True
        agent._move_only=True
        agent._screen_guide_enabled=True
        guide={'arrow_tip':(.6,.4),'arrow_direction':(0.,-1.),'direction':(0.,-1.)}
        agent._screen_move_guide=guide
        agent._update_screen_move_guide=AsyncMock(return_value=guide)
        agent._movement_hunt_enabled=lambda:False
        agent.scheduler.submit=AsyncMock()
        async def sent(c):
            with patch.object(VisualAgent,'_record_input'):agent._record_input(c,'sent')
        agent.scheduler.submit_emergency=AsyncMock(side_effect=sent)
        now=[10]
        async def tick(_):
            if now[0]==10:now[0]=10.01
            else:agent._running=False
        with patch('app.ai.main_agent.space_prompt',return_value=(.4,.4,.5,.5)), \
             patch('app.ai.main_agent.time.monotonic',side_effect=lambda:now[0]), \
             patch('app.ai.main_agent.asyncio.sleep',new=tick):
            await agent._movement_test_loop()
        agent.scheduler.submit_emergency.assert_awaited_once()
        self.assertEqual(agent.scheduler.submit.await_args.args[0].source,'SCREEN_ARROW_MOVE')

    def agent(self):
        agent=MainAgent.__new__(MainAgent)
        agent._epoch=3
        agent._move_only=True
        agent._paused=False
        agent._processing_halted=False
        agent.profile=SimpleNamespace(name='diablo4')
        agent._latest_frame=np.zeros((600,960,3),np.uint8)
        agent._world_click_exclusions=lambda:[]
        agent._world_player_origin=lambda:(.5,.5)
        agent._fresh=lambda:True
        agent._foreground=lambda:True
        agent._space_heading_allows=lambda:True
        agent.scheduler=SimpleNamespace(submit_emergency=AsyncMock())
        return agent

    def test_space_requires_movement_within_thirty_degrees_of_current_arrow(self):
        agent=self.agent()
        del agent._space_heading_allows
        agent._last_arrow_move_sent_at=10
        with patch('app.ai.main_agent.time.monotonic',return_value=10.1), \
             patch('app.ai.main_agent.screen_route_guide',return_value={'marker':(.8,.5),'arrow_direction':(-1.,0.)}):
            for degrees,allowed in ((0,True),(30,True),(-30,True),(31,False),(-31,False),(180,False)):
                angle=math.radians(degrees)
                agent._last_arrow_move_sent=command('MOVE',source='SCREEN_ARROW_MOVE',
                    direction=(math.cos(angle),math.sin(angle)),epoch=3)
                self.assertEqual(agent._space_heading_allows(),allowed)
            agent._last_arrow_move_sent_at=9
            self.assertFalse(agent._space_heading_allows())
            agent._last_arrow_move_sent_at=10
            agent._last_arrow_move_sent=None
            self.assertFalse(agent._space_heading_allows())

    async def test_prompt_near_player_is_not_hidden_by_click_exclusion(self):
        agent=self.agent()
        agent._world_click_exclusions=lambda:[(.47,.43,.53,.6),(.8,.1,.95,.25)]
        with patch('app.ai.main_agent.space_prompt',return_value=(.49,.49,.53,.53)) as detect:
            self.assertTrue(await agent._space_prompt_tick())
        self.assertEqual(len(detect.call_args.args),1)
        agent.scheduler.submit_emergency.assert_awaited_once()

    def test_space_prompt_is_allowed_during_hunting_movement(self):
        agent=self.agent();agent._move_only=False
        agent._space_prompt_seen_at=10
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.1):
            self.assertTrue(agent.can_execute(c))
            agent._stationary_hunt_mode=True
            self.assertFalse(agent.can_execute(c))

    async def test_successful_space_immediately_allows_research_without_repeating_prompt(self):
        agent=self.agent()
        with patch('app.ai.main_agent.space_prompt',return_value=(.4,.4,.5,.5)), \
             patch('app.ai.main_agent.time.monotonic',return_value=10):
            self.assertTrue(await agent._space_prompt_tick())
        c=agent.scheduler.submit_emergency.await_args.args[0]
        self.assertEqual(binding_for_command({},c),'SPACE')
        self.assertEqual(getattr(agent,'_space_prompt_wait_until',0),0)
        with patch.object(VisualAgent,'_record_input'), \
             patch('app.ai.main_agent.time.monotonic',return_value=10.4):
            agent._record_input(c,'sent')
        self.assertEqual(agent._space_prompt_wait_until,0)
        self.assertIsNone(agent._screen_move_guide)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.41), \
             patch('app.ai.main_agent.space_prompt',return_value=(.4,.4,.5,.5)):
            self.assertFalse(await agent._space_prompt_tick())
        agent.scheduler.submit_emergency.assert_awaited_once()

    def test_prompt_input_requires_fresh_detection_and_game_focus(self):
        agent=self.agent()
        agent._space_prompt_seen_at=10
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.1):
            self.assertTrue(agent.can_execute(c))
            agent._foreground=lambda:False
            self.assertFalse(agent.can_execute(c))
            agent._foreground=lambda:True
            agent._paused=True
            self.assertFalse(agent.can_execute(c))
        agent._paused=False
        with patch('app.ai.main_agent.time.monotonic',return_value=10.6):
            self.assertFalse(agent.can_execute(c))

    def test_failed_input_does_not_start_two_second_delay(self):
        agent=self.agent()
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
        with patch.object(VisualAgent,'_record_input'):
            agent._record_input(c,'blocked')
        self.assertEqual(getattr(agent,'_space_prompt_wait_until',0),0)

    async def test_space_resumes_with_new_arrow_after_direction_changes(self):
        agent=self.agent()
        agent._planned_move_heading=(1.,0.)
        agent._planned_recovery_since=9
        agent._planned_turn_candidate=((0.,1.),1)
        agent._screen_move_guide={'marker':(.7,.5)}
        agent._screen_guide_seen_at=10
        agent._screen_guide_scanned_at=10
        agent._arrow_hold_distance=75
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
        with patch.object(VisualAgent,'_record_input'), \
             patch('app.ai.main_agent.time.monotonic',return_value=10):
            agent._record_input(c,'sent')
        diagnostic={}
        def detect(frame,player,**kwargs):
            diagnostic.update(kwargs)
            return {'marker':(.3,.5),'arrow_tip':(.3,.5)}
        with patch('app.ai.main_agent.time.monotonic',return_value=10.01), \
             patch('app.ai.main_agent.screen_route_guide',side_effect=detect):
            guide=await agent._update_screen_move_guide()
            self.assertIsNotNone(guide)
            move=agent._arrow_move_command(guide)
        self.assertIsNone(diagnostic['previous_marker'])
        self.assertIsNone(diagnostic['preferred_heading'])
        self.assertFalse(guide.get('planned_recovery',False))
        self.assertLess(move.direction[0],0)
        self.assertEqual(agent._space_prompt_wait_until,0)

    def test_failed_space_preserves_arrow_tracking(self):
        agent=self.agent()
        agent._planned_move_heading=(1.,0.)
        guide={'marker':(.7,.5)}
        agent._screen_move_guide=guide
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',epoch=3)
        with patch.object(VisualAgent,'_record_input'):
            agent._record_input(c,'blocked')
        self.assertEqual(agent._planned_move_heading,(1.,0.))
        self.assertIs(agent._screen_move_guide,guide)
