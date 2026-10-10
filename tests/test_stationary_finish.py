import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from app.ai.main_agent import MainAgent
from app.controller.input_bindings import binding_for_command
from app.core.combat_guard import CombatGuard
from app.core.fast_policy import command


class StationaryManualMoveTests(unittest.IsolatedAsyncioTestCase):
    async def test_move_and_hunt_pause_all_inputs_and_resume_on_release(self):
        for move_only in (True,False):
            with self.subTest(move_only=move_only):
                agent=MainAgent.__new__(MainAgent)
                agent._hunt_active=True;agent._move_only=move_only
                agent._stationary_hunt_mode=False
                agent.scheduler=SimpleNamespace(clear=AsyncMock(),_cancel_execution=AsyncMock(),
                    executor=SimpleNamespace(release_held_attack=AsyncMock(),release_held_move=AsyncMock()))
                with patch('app.ai.main_agent.mouse_left_down',return_value=True):
                    for action in ('USE_SKILL','ATTACK','MOVE','DODGE','HEAL','INTERACT'):
                        self.assertFalse(agent.can_execute(command(action)))
                    self.assertTrue(await agent._stationary_manual_move_tick())
                    self.assertTrue(await agent._stationary_manual_move_tick())
                agent.scheduler.clear.assert_awaited_once()
                agent.scheduler._cancel_execution.assert_awaited_once()
                agent.scheduler.executor.release_held_attack.assert_awaited_once()
                agent.scheduler.executor.release_held_move.assert_awaited_once()
                with patch('app.ai.main_agent.mouse_left_down',return_value=False):
                    self.assertFalse(await agent._stationary_manual_move_tick())
                self.assertFalse(agent._stationary_left_down)
                self.assertTrue(agent._hunt_active)

    async def test_left_move_clears_target_and_cancels_active_attack_once(self):
        agent=MainAgent.__new__(MainAgent)
        agent._movement_hunt_target=7;agent._movement_target_missing=(9,1)
        agent._stationary_attack_record={'track_id':7,'until':12}
        agent._manual_skill_pending_at=9.9
        agent._manual_skill_move_required=True
        agent.combat_guard=CombatGuard();agent.combat_guard.focus=7
        agent.combat_guard.sessions[7]={'pending':True}
        agent.combat_guard.blocked[8]='HEALTH_DEPLETED_CONFIRMED'
        agent.scheduler=SimpleNamespace(clear=AsyncMock(),_cancel_execution=AsyncMock(),
                                        executor=SimpleNamespace(release_held_attack=AsyncMock()))
        with patch('app.ai.main_agent.mouse_left_down',return_value=True), \
             patch('app.ai.main_agent.time.monotonic',return_value=10):
            self.assertTrue(await agent._stationary_manual_move_tick())
            self.assertTrue(await agent._stationary_manual_move_tick())
        self.assertIsNone(agent._movement_hunt_target)
        self.assertIsNone(agent._movement_target_missing)
        self.assertIsNone(agent._stationary_attack_record)
        self.assertIsNone(agent.combat_guard.focus)
        self.assertIsNone(agent._manual_skill_pending_at)
        self.assertFalse(agent._manual_skill_move_required)
        self.assertNotIn(7,agent.combat_guard.sessions)
        self.assertIn(8,agent.combat_guard.blocked)
        agent.scheduler.clear.assert_awaited_once()
        agent.scheduler._cancel_execution.assert_awaited_once()
        agent.scheduler.executor.release_held_attack.assert_awaited_once()
        with patch('app.ai.main_agent.mouse_left_down',return_value=False), \
             patch('app.ai.main_agent.time.monotonic',return_value=10.01):
            self.assertFalse(await agent._stationary_manual_move_tick())
            self.assertEqual(agent._stationary_manual_move_until,0)


class StationaryFinishTests(unittest.TestCase):
    def test_combined_mode_allows_registered_skills_but_left_move_blocks_them(self):
        agent=self.agent();agent._manual_skill_mode=True;agent._move_only=False
        agent.profile=SimpleNamespace(name='generic')
        agent._latest_frame=object();agent._capture_at=10
        agent.scheduler=SimpleNamespace(_is_cooldown_ready=lambda _:True)
        agent._docs['input.json']['attack_skills']=[{'id':'one','enabled':True,'key':'2','cooldown_ms':500}]
        skill=command('USE_SKILL',source='MANUAL_SKILL',skill_id='one',epoch=agent._epoch)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.1), \
             patch('app.ai.main_agent.mouse_left_down',return_value=False):
            self.assertTrue(agent.can_execute(skill))
            self.assertFalse(agent.can_execute(replace(skill,skill_id='unknown')))
        with patch('app.ai.main_agent.mouse_left_down',return_value=True):
            self.assertFalse(agent.can_execute(skill))
    def test_left_move_blocks_target_grace_and_post_death_attacks(self):
        agent=self.agent()
        attack=agent._stationary_post_death_command(10)
        with patch('app.ai.main_agent.mouse_left_down',return_value=True):
            self.assertFalse(agent.can_execute(attack))
            self.assertFalse(agent.can_execute(replace(attack,reason='STATIONARY_TARGET_GRACE')))
    def test_mode_hold_does_not_depend_on_monster_and_stops_with_mode(self):
        agent=self.agent()
        attack=agent._stationary_hold_command()
        self.assertIsNone(attack.target)
        self.assertIsNone(attack.expires_at)
        self.assertTrue(agent.can_execute(attack))
        self.assertTrue(agent.can_execute(replace(attack,target=(.7,.4),track_id=999)))
        agent._paused=True
        self.assertFalse(agent.can_execute(attack))
        agent._paused=False;agent._foreground=lambda:False
        self.assertFalse(agent.can_execute(attack))
        agent._foreground=lambda:True;agent._epoch+=1
        self.assertFalse(agent.can_execute(attack))

    def test_mode_hold_is_blocked_during_manual_left_movement(self):
        agent=self.agent();attack=agent._stationary_hold_command()
        with patch('app.ai.main_agent.mouse_left_down',return_value=True):
            self.assertFalse(agent.can_execute(attack))
        agent._stationary_manual_move_until=12
        with patch('app.ai.main_agent.mouse_left_down',return_value=False), \
             patch('app.ai.main_agent.time.monotonic',return_value=11):
            self.assertFalse(agent.can_execute(attack))
        with patch('app.ai.main_agent.mouse_left_down',return_value=False), \
             patch('app.ai.main_agent.time.monotonic',return_value=12.1):
            self.assertTrue(agent.can_execute(attack))

    def test_brief_target_loss_keeps_hold_then_expires(self):
        agent=self.agent();agent.combat_guard.blocked={}
        agent._stationary_attack_record['seen_at']=10
        with patch('app.ai.main_agent.time.monotonic',return_value=10.2):
            attack=agent._stationary_target_grace_command(10.2)
            self.assertTrue(attack.maintain_attack)
            self.assertEqual(binding_for_command(agent._docs['input.json'],attack),'mouse_right')
            self.assertTrue(agent.can_execute(attack))
            agent._paused=True
            self.assertFalse(agent.can_execute(attack))
        self.assertIsNone(agent._stationary_target_grace_command(10.5))
        agent.combat_guard.blocked={7:'HEALTH_DEPLETED_CONFIRMED'}
        self.assertIsNone(agent._stationary_target_grace_command(10.2))

    def agent(self):
        agent=MainAgent.__new__(MainAgent)
        agent._stationary_hunt_mode=True
        agent._epoch=3
        agent._stationary_attack_record={'track_id':7,'target':(.7,.4),'epoch':3,'until':None}
        agent.combat_guard=SimpleNamespace(blocked={7:'HEALTH_DEPLETED_CONFIRMED'})
        agent._hunt_active=True
        agent._paused=False
        agent._move_only=False
        agent._movement_hunt_requested=True
        agent._processing_halted=False
        agent._fresh=lambda:True
        agent._foreground=lambda:True
        agent._world_click_exclusions=lambda:[]
        agent._docs={'input.json':{'bindings':{'ATTACK':'1'}}}
        return agent

    def test_confirmed_death_keeps_same_target_for_two_seconds(self):
        agent=self.agent()
        first=agent._stationary_post_death_command(10)
        self.assertEqual(first.action_type,'ATTACK')
        self.assertEqual(first.target,(.7,.4))
        self.assertEqual(first.track_id,7)
        self.assertTrue(first.maintain_attack)
        self.assertEqual(binding_for_command(agent._docs['input.json'],first),'mouse_right')
        self.assertEqual(agent._stationary_attack_record['until'],12)
        self.assertIsNotNone(agent._stationary_post_death_command(11.99))
        self.assertEqual(agent._stationary_attack_record['until'],12)
        self.assertIsNone(agent._stationary_post_death_command(12))
        self.assertIsNone(agent._stationary_post_death_command(12.1))

    def test_missing_or_blocked_target_is_not_treated_as_death(self):
        agent=self.agent()
        for blocked in ({},{7:'NO_DAMAGE_NONCOMBAT_CANDIDATE'}):
            agent.combat_guard.blocked=blocked
            self.assertIsNone(agent._stationary_post_death_command(10))
            self.assertIsNone(agent._stationary_attack_record['until'])

    def test_additional_attack_still_requires_focus_fresh_capture_and_active_mode(self):
        agent=self.agent()
        attack=agent._stationary_post_death_command(10)
        with patch('app.ai.main_agent.time.monotonic',return_value=11):
            self.assertTrue(agent.can_execute(attack))
            self.assertFalse(agent.can_execute(replace(attack,target=(.8,.4))))
            agent._foreground=lambda:False
            self.assertFalse(agent.can_execute(attack))
            agent._foreground=lambda:True
            agent._fresh=lambda:False
            self.assertFalse(agent.can_execute(attack))
            agent._fresh=lambda:True
            agent._paused=True
            self.assertFalse(agent.can_execute(attack))
            agent._paused=False
            agent._stationary_hunt_mode=False
            self.assertFalse(agent.can_execute(attack))

    def test_deadline_and_new_epoch_invalidate_queued_attack(self):
        agent=self.agent()
        attack=agent._stationary_post_death_command(10)
        with patch('app.ai.main_agent.time.monotonic',return_value=12):
            self.assertFalse(agent.can_execute(attack))
        with patch('app.ai.main_agent.time.monotonic',return_value=11):
            agent._epoch+=1
            self.assertFalse(agent.can_execute(attack))
