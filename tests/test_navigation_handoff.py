import time
import asyncio
import unittest
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, Mock

import numpy as np

from app.ai.non_yolo_agent import NonYoloAgent
from app.ai.visual_agent import VisualAgent
from app.core.action_command import ActionCommand
from app.core.local_navigation import LocalNavigator
from app.core.minimap_memory import MinimapMemory
from app.core.click_journey import ClickJourney
from app.core.action_executor import ActionExecutor
from app.core.action_scheduler import ActionScheduler


class NavigationHandoffTests(unittest.TestCase):
    def agent(self):
        agent=NonYoloAgent.__new__(NonYoloAgent)
        agent._move_only=False
        agent.profile=SimpleNamespace(name='generic')
        agent._hud_probe_command=None
        agent._docs={'input.json':{'movement':{'mode':'click'},'movement_skill':{'enabled':True,'key':'SPACE'}},
            'navigation.json':{'minimap':{'enabled':True,
                'rotation_degrees':0,'player':[.5,.5],
                'mapping':{'enabled':True}}}}
        agent._world_click_exclusions=lambda:[]
        agent._world_player_origin=lambda:(.5,.5)
        agent._latest_frame=np.full((1080,1920,3),60,np.uint8)
        agent._objects=[]
        agent.scene=SimpleNamespace(scene='play',last_success=time.monotonic())
        agent.navigator=LocalNavigator()
        agent.combat_feedback=SimpleNamespace(annotate=Mock())
        memory=agent.minimap_memory=MinimapMemory()
        memory.valid=True
        memory.last_update=time.monotonic()
        memory.grid=np.ones((48,48),np.uint8)
        memory.mask=np.full((192,192),255,np.uint8)
        memory.player=[.5,.5]
        memory.rotation=0
        # A stale/raw terrain publication must not veto the current verified grid.
        agent._minimap_mask=np.zeros((192,192),np.uint8)
        return agent

    def test_input_guard_uses_current_verified_grid_after_handoff(self):
        agent=self.agent()
        move=ActionCommand('MOVE',10,time.monotonic(),target=(.53,.5),direction=(1.,0.))
        with patch.object(VisualAgent,'can_execute',return_value=True):
            self.assertTrue(agent.can_execute(move))

    def test_input_guard_still_rejects_wall_on_verified_grid(self):
        agent=self.agent()
        agent.minimap_memory.grid[:,25]=0
        move=ActionCommand('MOVE',10,time.monotonic(),target=(.55,.5),direction=(1.,0.))
        with patch.object(VisualAgent,'can_execute',return_value=True):
            self.assertFalse(agent.can_execute(move))

    def test_movement_test_allows_verified_move_without_hud(self):
        agent=self.agent()
        agent.movement_test_mode=True
        agent._paused=False
        agent._processing_halted=False
        agent._epoch=0
        agent._capture_at=time.monotonic()
        agent._foreground=lambda:True
        agent._hud_ready=False
        agent._hud_at=0
        move=ActionCommand('MOVE',10,time.monotonic(),target=(.53,.5),direction=(1.,0.))
        with patch.object(VisualAgent,'can_execute',side_effect=AssertionError('HUD guard must not run')):
            self.assertTrue(agent.can_execute(move))
        agent._capture_at=0
        self.assertFalse(agent.can_execute(move))

    def test_movement_test_blocks_buff_and_skill_commands(self):
        agent=self.agent()
        agent.movement_test_mode=True
        for action in ('ATTACK','USE_SKILL','CAST_BUFF','USE_POTION'):
            self.assertFalse(agent.can_execute(ActionCommand(action,10,time.monotonic())))

    def test_movement_hunt_attacks_confirmed_enemy_then_resumes_travel(self):
        agent=self.agent();agent.profile.name='diablo4'
        agent._epoch=0;agent._hunt_active=True;agent._paused=False
        agent._movement_hunt_requested=True
        enemy=SimpleNamespace(track_id=7,bbox=(900,450,1000,650),enemy_bar_confirmed=True,relation='hostile')
        agent._confirm_diablo_enemies=Mock(return_value=[enemy])
        agent.combat_guard=SimpleNamespace(observe=Mock(),is_blocked=lambda o:False,permits=lambda tid:True,
                                          request=lambda o,s:True,blocked={})
        agent.scheduler=SimpleNamespace(_is_cooldown_ready=lambda c:True)
        agent._docs['input.json']['attack_skills']=[{'id':'registered','enabled':True,'key':'2','cooldown_ms':1000}]
        agent._docs['input.json']['bindings']={'CAST_BUFF':'4'}
        attack=agent._movement_hunt_command()
        self.assertEqual(attack.action_type,'USE_SKILL')
        self.assertEqual(attack.track_id,7)
        self.assertEqual(attack.source,'MOVEMENT_HUNT')
        agent.movement_test_mode=True;agent._processing_halted=False
        agent._capture_at=time.monotonic();agent._foreground=lambda:True
        self.assertTrue(agent.can_execute(attack))
        agent._movement_hunt_requested=False
        self.assertFalse(agent.can_execute(attack))
        agent._movement_hunt_requested=True
        agent._confirm_diablo_enemies.return_value=[]
        self.assertEqual(agent._movement_hunt_command().reason,'MONSTER_RESULT_WAIT')
        agent.combat_guard.blocked[7]='HEALTH_DEPLETED_CONFIRMED'
        self.assertIsNone(agent._movement_hunt_command())

    def test_hunt_start_switches_move_only_to_move_and_attack(self):
        for message in ('사냥 시작','자동사냥 시작해줘','사냥해','/hunt'):
            agent=self.agent();agent.profile.name='diablo4'
            agent._paused=False;agent._move_only=True;agent._hunt_active=True
            agent.click_journey=ClickJourney();agent.emit_web_event=Mock()
            agent.vl=SimpleNamespace();agent.chat_vl=agent.vl
            agent._scene_scan_task=None
            with patch.object(VisualAgent,'handle_control',new=AsyncMock(return_value=True)) as control:
                self.assertTrue(asyncio.run(agent.handle_control(message)))
            control.assert_awaited_once_with('/hunt')
            self.assertFalse(agent._move_only)
            self.assertTrue(agent._movement_hunt_enabled())
            self.assertTrue(agent._repeat_skills_hunting)

    def test_repeat_skill_button_command_enables_interval_mode(self):
        agent=self.agent();agent.profile.name='diablo4'
        agent._epoch=0
        agent._paused=False;agent._move_only=True;agent._hunt_active=True
        agent.click_journey=ClickJourney();agent.emit_web_event=Mock()
        agent.vl=SimpleNamespace();agent.chat_vl=agent.vl
        agent._scene_scan_task=None
        agent.scheduler=SimpleNamespace(submit_emergency=AsyncMock())
        with patch.object(VisualAgent,'handle_control',new=AsyncMock(return_value=True)):
            self.assertTrue(asyncio.run(agent.handle_control('반복 스킬')))
        self.assertTrue(agent._manual_skill_mode)
        self.assertTrue(agent._stationary_skill_mode)
        self.assertFalse(agent._repeat_skills_hunting)
        self.assertEqual(agent.scheduler.submit_emergency.call_args.args[0].action_type,'STOP')
        self.assertFalse(agent.can_execute(ActionCommand('MOVE',10,time.monotonic())))
        self.assertFalse(agent._move_only)

    def test_hunt_rotation_uses_basic_and_every_enabled_registered_skill(self):
        agent=self.agent()
        agent._docs['input.json']['attack_skills']=[
            {'id':'first','enabled':True,'cooldown_ms':0},
            {'id':'second','enabled':True,'cooldown_ms':3200},
            {'id':'off','enabled':False,'cooldown_ms':0}]
        agent.scheduler=SimpleNamespace(_is_cooldown_ready=lambda c:True)
        attack=ActionCommand('ATTACK',50,time.monotonic(),source='MOVEMENT_HUNT')
        sequence=[agent._hunt_attack_rotation(attack) for _ in range(6)]
        self.assertEqual([c.skill_id for c in sequence],['first','second','first','second','first','second'])
        agent._hunt_rotation_index=2
        agent.scheduler._is_cooldown_ready=lambda c:c.skill_id!='second'
        self.assertEqual(agent._hunt_attack_rotation(attack).skill_id,'first')

    def test_missing_enemy_bar_does_not_keep_cached_attack_confirmation(self):
        agent=self.agent()
        enemy=SimpleNamespace(enemy_bar_confirmed=True,enemy_health_valid=True)
        agent.scene=SimpleNamespace(lock=threading.RLock(),objects=[enemy],
                                    seed_enemy_bars=Mock(return_value=[enemy]))
        agent.resolver=SimpleNamespace(protect_named_ally=Mock())
        agent._docs['vision.json']={'red_enemy_bar':{'enabled':True}}
        agent.combat_feedback=SimpleNamespace(red_bar_boxes=Mock(return_value=[]),
            tracked_black_bars=Mock(return_value=[]),confirm_red_bars=Mock())
        live=agent._confirm_diablo_enemies(agent._latest_frame)
        self.assertFalse(live[0].enemy_bar_confirmed)
        self.assertFalse(live[0].enemy_health_valid)

    def test_sustained_monster_loss_returns_to_movement(self):
        agent=self.agent();agent.profile.name='diablo4'
        agent._epoch=0;agent._hunt_active=True;agent._paused=False
        agent._movement_hunt_requested=True;agent._movement_hunt_target=7
        agent._movement_target_missing=(time.monotonic()-1.1,2)
        agent._confirm_diablo_enemies=Mock(return_value=[])
        agent.combat_guard=SimpleNamespace(observe=Mock(),blocked={})
        self.assertIsNone(agent._movement_hunt_command())
        self.assertIsNone(agent._movement_hunt_target)
        self.assertTrue(agent._movement_hunt_enabled())

    def test_manual_skill_uses_registered_interval_without_enemy(self):
        agent=self.agent();agent.movement_test_mode=True
        agent._manual_skill_mode=True;agent._paused=False;agent._processing_halted=False
        agent._epoch=0;agent._capture_at=time.monotonic();agent._foreground=lambda:True
        agent._docs['input.json']['attack_skills']=[{'id':'timed','enabled':True,'key':'2','cooldown_ms':3200}]
        agent._docs['input.json']['bindings']={'CAST_BUFF':'4'}
        agent.scheduler=ActionScheduler(SimpleNamespace())
        base=ActionCommand('ATTACK',50,time.monotonic(),source='MANUAL_SKILL')
        skill=agent._hunt_attack_rotation(base)
        self.assertEqual(skill.cooldown,3.2)
        self.assertTrue(agent.can_execute(skill))
        agent.scheduler._last_execution[('USE_SKILL','timed')]=time.monotonic()
        self.assertIsNone(agent._hunt_attack_rotation(base))
        self.assertFalse(agent.can_execute(replace(skill,cooldown=0)))
        agent.scheduler._last_execution[('USE_SKILL','timed')]-=3.3
        self.assertTrue(agent.can_execute(skill))

    def test_basic_attack_is_blocked_and_skill_uses_its_own_interval(self):
        agent=self.agent()
        self.assertFalse(agent.can_execute(ActionCommand('ATTACK',50,time.monotonic())))
        scheduler=ActionScheduler(SimpleNamespace())
        skill=ActionCommand('USE_SKILL',50,10.,source='MOVEMENT_HUNT',skill_id='timed',cooldown=.1)
        scheduler._last_execution[('USE_SKILL','timed')]=10.
        scheduler._last_execution['combat_input']=10.08
        with patch('app.core.action_scheduler.time.monotonic',return_value=10.09):
            self.assertFalse(scheduler._is_cooldown_ready(skill))
        with patch('app.core.action_scheduler.time.monotonic',return_value=10.11):
            self.assertTrue(scheduler._is_cooldown_ready(skill))

    def test_hunt_includes_basic_attack_and_unconditional_skill_timer(self):
        agent=self.agent();agent._repeat_skills_hunting=True
        agent._docs['input.json']['attack_skills']=[{'id':'one','enabled':True,'cooldown_ms':1000}]
        agent.scheduler=SimpleNamespace(_is_cooldown_ready=lambda c:True)
        attack=ActionCommand('ATTACK',50,time.monotonic(),source='MOVEMENT_HUNT')
        self.assertEqual(agent._hunt_attack_rotation(attack).action_type,'ATTACK')
        self.assertEqual(agent._hunt_attack_rotation(attack).skill_id,'one')
        timer=replace(attack,source='MANUAL_SKILL')
        self.assertEqual(agent._hunt_attack_rotation(timer).skill_id,'one')

    def test_object_click_block_replans_after_two_maps(self):
        agent=self.agent();agent.click_journey=ClickJourney()
        memory=agent.minimap_memory;memory.position=np.array([96.,96.]);memory.goal=np.array([174.,96.])
        agent.navigator.last_block_reason='클릭 위치가 객체와 겹치거나 캐릭터에 너무 가까움'
        move=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        agent._navigation_click_failed(move)
        self.assertIsNotNone(memory.goal)
        memory.last_update+=.1
        agent._navigation_click_failed(move)
        self.assertTrue(memory.replan_requested)
        self.assertIsNotNone(memory.planned_target())

    def test_visible_route_with_rejected_click_recovers_after_one_second(self):
        agent=self.agent();agent._epoch=0;agent.click_journey=ClickJourney()
        agent.emit_web_event=Mock()
        agent.scheduler=SimpleNamespace(executor=SimpleNamespace(active_command=None))
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.]);memory.goal=np.array([174.,96.])
        memory.route=[[.5,.5],[.8,.5]]
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        agent._remember_rejected_movement(c)
        started=agent._rejected_move_sample[1]
        self.assertFalse(agent._recover_rejected_movement(started+.9))
        self.assertFalse(agent._recover_rejected_movement(started+1.1))
        memory.last_update+=.1
        self.assertTrue(agent._recover_rejected_movement(started+1.1))
        self.assertTrue(memory.replan_requested)
        self.assertIsNotNone(memory.planned_target())

    def test_manual_skill_requires_movement_before_next_skill(self):
        agent=self.agent();agent._epoch=0;agent.click_journey=ClickJourney()
        agent.scheduler=SimpleNamespace(submit=AsyncMock())
        skill=ActionCommand('USE_SKILL',50,time.monotonic(),source='MANUAL_SKILL',skill_id='one')
        agent._hunt_attack_rotation=Mock(return_value=skill)
        memory=agent.minimap_memory;memory.goal=np.array([174.,96.])
        self.assertTrue(asyncio.run(agent._manual_skill_tick()))
        self.assertTrue(asyncio.run(agent._manual_skill_tick()))
        self.assertEqual(agent.scheduler.submit.await_count,1)
        with patch.object(VisualAgent,'_record_input'):
            agent._record_input(skill,'sent')
        self.assertFalse(asyncio.run(agent._manual_skill_tick()))
        np.testing.assert_allclose(memory.goal,[174.,96.])
        move=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        with patch.object(VisualAgent,'_record_input'):
            agent._record_input(move,'sent')
        self.assertTrue(asyncio.run(agent._manual_skill_tick()))
        self.assertEqual(agent.scheduler.submit.await_count,2)

    def test_explicit_skill_repeats_even_when_movement_cannot_be_sent(self):
        agent=self.agent();agent._epoch=0
        agent.scheduler=SimpleNamespace(submit=AsyncMock())
        skill=ActionCommand('USE_SKILL',50,time.monotonic(),source='MANUAL_SKILL',skill_id='one')
        agent._hunt_attack_rotation=Mock(return_value=skill)
        agent._manual_skill_move_required=True
        agent._manual_skill_resume_until=time.monotonic()-1
        agent.minimap_memory.valid=False
        self.assertTrue(asyncio.run(agent._manual_skill_tick()))
        agent.scheduler.submit.assert_awaited_once_with(skill)
        agent._move_only=True
        self.assertFalse(agent._movement_hunt_enabled())
        self.assertIsNone(agent._movement_hunt_command())

    def test_stall_attempts_escape_once_then_replans(self):
        agent=self.agent()
        agent._epoch=0
        agent.click_journey=ClickJourney()
        agent.click_journey.last_release='stalled'
        agent.emit_web_event=Mock()
        agent.scheduler=SimpleNamespace(submit=AsyncMock(),executor=SimpleNamespace(active_command=None))
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.])
        memory.goal=np.array([174.,96.])
        self.assertTrue(asyncio.run(agent._try_navigation_escape()))
        escape=agent.scheduler.submit.call_args.args[0]
        self.assertEqual(escape.action_type,'DODGE')
        self.assertEqual(escape.source,'NAVIGATION_ESCAPE')
        self.assertIsNotNone(escape.target)
        self.assertGreater(escape.target[0],.9)
        self.assertTrue(asyncio.run(agent._try_navigation_escape()))
        self.assertEqual(agent.scheduler.submit.await_count,1)
        agent._navigation_escape['at']-=2
        agent._navigation_escape['valid_since']-=2
        self.assertFalse(asyncio.run(agent._try_navigation_escape()))
        self.assertTrue(memory.replan_requested)
        self.assertIsNone(memory.goal)

    def test_successful_escape_keeps_destination(self):
        agent=self.agent()
        agent._epoch=0
        agent.click_journey=ClickJourney()
        memory=agent.minimap_memory
        memory.position=np.array([98.,96.])
        memory.goal=np.array([174.,96.])
        agent.scheduler=SimpleNamespace(executor=SimpleNamespace(active_command=None))
        agent._navigation_escape={'epoch':0,'segment':memory.segment,
            'position':np.array([96.,96.]),'at':time.monotonic()}
        self.assertFalse(asyncio.run(agent._try_navigation_escape()))
        np.testing.assert_allclose(memory.goal,[174.,96.])
        self.assertIsNone(agent._navigation_escape)

    def test_movement_skill_followed_by_dodge_after_one_second(self):
        agent=self.agent();agent._running=True;agent._paused=False
        agent._processing_halted=False;agent._epoch=0
        agent._foreground=lambda:True;agent._fresh=lambda:True
        agent.scheduler=SimpleNamespace(submit=AsyncMock())
        skill=ActionCommand('DODGE',80,time.monotonic(),source='NAVIGATION_ESCAPE',target=(.95,.5))
        with patch('app.ai.non_yolo_agent.asyncio.sleep',new=AsyncMock()) as delay:
            asyncio.run(agent._dodge_after_movement_skill(skill))
        delay.assert_awaited_once_with(1)
        dodge=agent.scheduler.submit.call_args.args[0]
        self.assertEqual(dodge.action_type,'DODGE')
        self.assertEqual(dodge.source,'NAVIGATION_DODGE')
        self.assertEqual(dodge.target,skill.target)
        agent.scheduler.submit.reset_mock();agent._epoch=1
        with patch('app.ai.non_yolo_agent.asyncio.sleep',new=AsyncMock()):
            asyncio.run(agent._dodge_after_movement_skill(skill))
        agent.scheduler.submit.assert_not_awaited()

    def test_missing_route_replans_once_after_one_second_and_keeps_display(self):
        agent=self.agent();agent._epoch=0
        agent.click_journey=ClickJourney();agent.emit_web_event=Mock()
        agent.scheduler=SimpleNamespace(executor=SimpleNamespace(active_command=None))
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.]);memory.goal=np.array([174.,96.])
        now=time.monotonic()
        self.assertFalse(agent._review_missing_navigation_route(now))
        self.assertFalse(agent._review_missing_navigation_route(now+.9))
        self.assertTrue(agent._review_missing_navigation_route(now+1.01))
        self.assertTrue(memory.replan_requested)
        self.assertIsNotNone(memory.planned_target())
        self.assertFalse(agent._review_missing_navigation_route(now+2))
        memory.valid=True;memory.last_update=now+2
        memory.route=[[.5,.5],[.8,.5]]
        self.assertFalse(agent._review_missing_navigation_route(now+2))
        memory.route=[]
        self.assertFalse(agent._review_missing_navigation_route(now+2.1))
        self.assertTrue(agent._review_missing_navigation_route(now+3.11))

    def test_invalid_map_time_does_not_count_as_failed_skill(self):
        agent=self.agent()
        agent._epoch=0
        agent.click_journey=ClickJourney()
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.]);memory.goal=np.array([174.,96.])
        agent.scheduler=SimpleNamespace(executor=SimpleNamespace(active_command=None))
        agent._navigation_escape={'epoch':0,'segment':memory.segment,
            'position':memory.position.copy(),'at':time.monotonic()-10,'valid_since':time.monotonic()-5}
        memory.valid=False
        self.assertTrue(asyncio.run(agent._try_navigation_escape()))
        self.assertIsNone(agent._navigation_escape['valid_since'])
        memory.valid=True
        self.assertTrue(asyncio.run(agent._try_navigation_escape()))
        self.assertIsNotNone(memory.goal)
        self.assertFalse(memory.replan_requested)

    def test_player_blocked_skill_wait_is_limited_to_one_second(self):
        agent=self.agent();agent._epoch=0;agent.click_journey=ClickJourney()
        agent.emit_web_event=Mock()
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.]);memory.goal=np.array([174.,96.])
        memory.valid=False;memory.reason='player_blocked'
        agent._navigation_escape={'epoch':0,'segment':memory.segment,
            'position':memory.position.copy(),'at':time.monotonic()}
        self.assertTrue(asyncio.run(agent._try_navigation_escape()))
        agent._navigation_escape['map_wait_since']-=1.01
        self.assertFalse(asyncio.run(agent._try_navigation_escape()))
        self.assertIsNone(agent._navigation_escape)
        self.assertTrue(memory.replan_requested)
        self.assertIsNotNone(memory.planned_target())

    def test_wall_probe_aims_far_without_requiring_wall_to_be_walkable(self):
        agent=self.agent()
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.])
        memory.last_direction=(1.,0.)
        memory.grid[:,25]=0
        target=agent._escape_cursor_target()
        self.assertIsNotNone(target)
        self.assertGreater(target[0],.95)
        agent.movement_test_mode=True
        agent._paused=False;agent._processing_halted=False;agent._epoch=0
        agent._capture_at=time.monotonic();agent._foreground=lambda:True
        probe=ActionCommand('DODGE',10,time.monotonic(),source='NAVIGATION_ESCAPE',target=target)
        self.assertTrue(agent.can_execute(probe))
        move=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE',target=target,direction=(1.,0.))
        self.assertFalse(agent.can_execute(move))

    def test_detected_wall_starts_skill_probe_before_replanning(self):
        agent=self.agent()
        agent.movement_test_mode=True
        agent._epoch=0
        agent.click_journey=ClickJourney()
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.]);memory.goal=np.array([174.,96.])
        memory.last_direction=(1.,0.);memory.grid[:,25]=0
        agent.scheduler=SimpleNamespace(submit=AsyncMock(),executor=SimpleNamespace(active_command=None))
        agent.navigator.last_block_reason='지도에서 클릭까지의 통로가 차단됨'
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE',direction=(1.,0.))
        agent._navigation_click_failed(c)
        memory.last_update+=.1
        agent._navigation_click_failed(c)
        self.assertTrue(agent._wall_probe_pending)
        self.assertFalse(memory.replan_requested)
        self.assertTrue(asyncio.run(agent._try_navigation_escape()))
        self.assertEqual(agent.scheduler.submit.call_args.args[0].action_type,'DODGE')
        np.testing.assert_allclose(memory.goal,[174.,96.])

    def test_map_arrival_releases_goal_without_active_click_journey(self):
        agent=self.agent()
        agent.click_journey=ClickJourney()
        agent.emit_web_event=Mock()
        memory=agent.minimap_memory
        memory.goal=np.array([98.,98.]);memory.position=np.array([96.5,96.5])
        memory.visits[(24,24)]=3
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        self.assertFalse(agent._check_map_goal_arrival(c))
        self.assertFalse(agent._check_map_goal_arrival(c))
        memory.last_update+=.1
        self.assertTrue(agent._check_map_goal_arrival(c))
        self.assertIsNone(memory.goal)
        self.assertEqual(memory.visits[(24,24)],3)
        self.assertEqual(agent.click_journey.last_release,'map_goal_arrived')

    def test_map_arrival_does_not_release_target_before_arrival(self):
        agent=self.agent()
        agent.click_journey=ClickJourney()
        memory=agent.minimap_memory
        memory.goal=np.array([106.,96.]);memory.position=np.array([96.,96.])
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        for _ in range(3):
            self.assertFalse(agent._check_map_goal_arrival(c))
            memory.last_update+=.1
        self.assertIsNotNone(memory.goal)

    def test_map_arrival_accepts_near_endpoint_across_grid_boundary(self):
        agent=self.agent()
        agent.click_journey=ClickJourney()
        agent.emit_web_event=Mock()
        memory=agent.minimap_memory
        memory.goal=np.array([98.,98.]);memory.position=np.array([95.,98.])
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        self.assertFalse(agent._check_map_goal_arrival(c))
        memory.last_update+=.1
        self.assertTrue(agent._check_map_goal_arrival(c))
        self.assertIsNone(memory.goal)
        np.testing.assert_allclose(memory.completed_goals[-1],[98.,98.])

    def test_near_goal_handoff_requires_two_fresh_samples_and_clear_corridor(self):
        agent=self.agent();agent.click_journey=ClickJourney();agent.emit_web_event=Mock()
        memory=agent.minimap_memory
        memory.goal=np.array([101.,96.]);memory.position=np.array([96.,96.])
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE')
        memory.grid[:,25]=0
        self.assertFalse(agent._check_map_goal_arrival(c))
        memory.last_update+=.1
        self.assertFalse(agent._check_map_goal_arrival(c))
        memory.grid[:,25]=1
        self.assertFalse(agent._check_map_goal_arrival(c))
        self.assertFalse(agent._check_map_goal_arrival(c))
        memory.last_update+=.1
        self.assertTrue(agent._check_map_goal_arrival(c))
        self.assertIsNone(memory.goal)

    def test_basic_movement_click_failure_probes_wall_without_erasing_map(self):
        agent=self.agent()
        agent.click_journey=ClickJourney()
        agent.emit_web_event=Mock()
        memory=agent.minimap_memory
        memory.position=np.array([96.,96.])
        memory.goal=np.array([96.,60.])
        memory.visits[(24,24)]=2
        agent.navigator.last_block_reason='지도에서 클릭까지의 통로가 차단됨'
        move=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE',direction=(0.,-1.))
        agent._navigation_click_failed(move)
        self.assertIsNotNone(memory.goal)
        agent._navigation_click_failed(move)
        self.assertIsNotNone(memory.goal)
        memory.last_update+=.1
        agent._navigation_click_failed(move)
        self.assertIsNotNone(memory.goal)
        self.assertTrue(agent._wall_probe_pending)
        self.assertFalse(memory.replan_requested)
        self.assertEqual(memory.visits[(24,24)],2)
        self.assertTrue(memory.valid)

    def test_hud_click_rejection_does_not_change_heading_in_movement_test(self):
        agent=self.agent()
        agent.movement_test_mode=True
        agent.click_journey=ClickJourney()
        memory=agent.minimap_memory
        memory.goal=np.array([174.,96.])
        memory.last_direction=(1.,0.)
        agent.navigator.last_block_reason='클릭 종점이 HUD·캐릭터·객체 영역과 겹침'
        c=ActionCommand('MOVE',10,time.monotonic(),source='HUNT_EXPLORE',direction=(1.,0.))
        agent._navigation_click_failed(c)
        memory.last_update+=.1
        agent._navigation_click_failed(c)
        self.assertFalse(memory.replan_requested)
        self.assertEqual(memory.last_direction,(1.,0.))

    def test_step_progress_handoff_dispatches_next_three_clicks(self):
        agent=self.agent()
        agent.click_journey=ClickJourney()
        agent._next_navigation_plan=None
        memory=agent.minimap_memory
        memory.position=np.array([98.,96.])
        memory.rotation=0
        memory.goal=np.array([174.,96.])
        move=ActionCommand('MOVE',10,time.monotonic(),source='USER_COMMAND',
            target=(.53,.5),direction=(1.,0.))
        journey=agent.click_journey
        journey.feedback_steps=True
        journey.dispatched(move,position=np.array([96.,96.]),segment=memory.segment,
            player_screen=(.5,.5),shape=(1080,1920),scale=12,rotation=0,destination=memory.goal)
        journey.on_input(move,'sent',time.monotonic()-.3)
        self.assertFalse(journey.continues(epoch=0,segment=memory.segment,
            position=memory.position,origin=memory.origin,mask=memory.mask,valid=True,
            stuck=False,now=time.monotonic(),sampled_at=memory.last_update))
        self.assertEqual(journey.last_release,'step_progress')
        agent.scheduler=SimpleNamespace(executor=SimpleNamespace(active_command=None))
        repeated=agent._finalize_navigation_command(move)
        self.assertEqual(repeated.move_clicks,3)
        self.assertTrue(agent._dispatch_action(repeated))
        controller=AsyncMock()
        controller.move.return_value=True
        executor=ActionExecutor(controller)
        executor.result_observer=lambda c,status,error:journey.on_input(c,status,time.monotonic())
        self.assertTrue(asyncio.run(executor.execute(repeated)))
        self.assertEqual(controller.move.await_count,3)
        self.assertIsNotNone(journey.current)
        self.assertEqual(journey.record['goal_id'],1)
