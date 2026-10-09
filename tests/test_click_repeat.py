import unittest
from types import SimpleNamespace

import numpy as np

from app.core.click_journey import ClickJourney


class ClickRepeatTests(unittest.TestCase):
    def setUp(self):
        self.journey=ClickJourney()
        self.command=SimpleNamespace(action_type='MOVE',source='USER_COMMAND',
            target=(.7,.5),execute_at=1.,decision_epoch=1)
        self.journey.dispatched(self.command,position=np.array([96.,96.]),
            segment=1,player_screen=(.5,.5),shape=(192,192),scale=1,
            rotation=0,destination=np.array([174.,96.]))
        self.journey.on_input(self.command,'sent',10.)

    def continues(self,now=10.1,valid=True,sampled_at=10.08,epoch=1):
        return self.journey.continues(epoch=epoch,segment=1,
            position=np.array([96.,96.]),origin=np.zeros(2),
            mask=np.full((192,192),255,np.uint8),valid=valid,
            stuck=False,now=now,sampled_at=sampled_at)

    def test_click_repeats_without_feedback_mode(self):
        self.assertFalse(self.journey.feedback_steps)
        self.assertFalse(self.continues())
        self.assertEqual(self.journey.last_release,'step_refresh')
        self.command.execute_at=2.
        self.journey.dispatched(self.command,position=np.array([96.,96.]),
            segment=1,player_screen=(.5,.5),shape=(192,192),scale=1,rotation=0)
        self.journey.on_input(self.command,'sent',10.11)
        self.assertEqual(self.journey.record['goal_id'],1)
        np.testing.assert_allclose(self.journey.record['destination'],[174.,96.])

    def test_no_repeat_before_interval(self):
        self.assertTrue(self.continues(now=10.02,sampled_at=10.01))

    def test_missing_or_stale_map_cannot_refresh(self):
        self.assertTrue(self.continues(valid=False))
        self.assertTrue(self.continues(sampled_at=9.))
        self.assertIsNone(self.journey.last_release)

    def test_new_command_interrupts_old_destination(self):
        self.assertFalse(self.continues(epoch=2))
        self.assertEqual(self.journey.last_release,'epoch_changed')

    def test_passing_destination_does_not_trigger_return_click(self):
        self.journey.feedback_steps=True
        self.assertFalse(self.journey.continues(epoch=1,segment=1,
            position=np.array([190.,96.]),origin=np.zeros(2),
            mask=np.full((192,192),255,np.uint8),valid=True,stuck=False,
            now=10.3,sampled_at=10.3))
        self.assertEqual(self.journey.last_release,'near_goal')

    def test_arrival_only_keeps_destination_when_still_ten_pixels_away(self):
        self.journey.feedback_steps=True
        self.journey.arrival_only=True
        self.assertFalse(self.journey._near_destination(np.array([164.,96.]),10.3))
        self.assertTrue(self.journey._near_destination(np.array([173.,96.]),10.3))

    def test_near_goal_handoff_releases_before_exact_arrival(self):
        self.journey.feedback_steps=True
        self.journey.arrival_only=False
        self.assertFalse(self.journey.continues(epoch=1,segment=1,
            position=np.array([164.,96.]),origin=np.zeros(2),
            mask=np.full((192,192),255,np.uint8),valid=True,stuck=False,
            now=10.3,sampled_at=10.3))
        self.assertEqual(self.journey.last_release,'near_goal')

    def test_arrival_only_refreshes_but_keeps_destination_ten_pixels_away(self):
        self.journey.feedback_steps=True
        self.journey.arrival_only=True
        self.assertFalse(self.journey.continues(epoch=1,segment=1,
            position=np.array([164.,96.]),origin=np.zeros(2),
            mask=np.full((192,192),255,np.uint8),valid=True,stuck=False,
            now=10.3,sampled_at=10.3))
        self.assertNotIn(self.journey.last_release,{'near_goal','arrived','destination_passed'})
        np.testing.assert_allclose(self.journey.record['destination'],[174.,96.])

    def test_arrival_only_passed_destination_is_not_reported_as_intermediate_arrival(self):
        self.journey.feedback_steps=True
        self.journey.arrival_only=True
        self.assertFalse(self.journey.continues(epoch=1,segment=1,
            position=np.array([190.,96.]),origin=np.zeros(2),
            mask=np.full((192,192),255,np.uint8),valid=True,stuck=False,
            now=10.3,sampled_at=10.3))
        self.assertEqual(self.journey.last_release,'destination_passed')

    def test_repeat_does_not_reset_stall_timer(self):
        self.assertFalse(self.continues())
        self.command.execute_at=2.
        self.journey.dispatched(self.command,position=np.array([96.,96.]),
            segment=1,player_screen=(.5,.5),shape=(192,192),scale=1,rotation=0)
        self.journey.on_input(self.command,'sent',10.11)
        self.assertFalse(self.continues(now=11.31,sampled_at=11.3))
        self.assertEqual(self.journey.last_release,'stalled')
