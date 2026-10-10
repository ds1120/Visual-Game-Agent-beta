import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.main_agent import MainAgent
from app.controller.input_bindings import binding_for_command


class StationaryFinishTests(unittest.TestCase):
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
        self.assertFalse(first.maintain_attack)
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
