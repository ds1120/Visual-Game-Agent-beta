import unittest
from unittest.mock import patch

import numpy as np

from app.core.minimap_memory import MinimapMemory


class MinimapRefreshTests(unittest.TestCase):
    def setUp(self):
        self.memory = MinimapMemory()
        self.frame = np.zeros((192, 192, 3), np.uint8)
        self.settings = {'minimap': {
            'enabled': True, 'bbox': [0, 0, 1000, 1000],
            'player': [.5, .5], 'rotation_degrees': 0,
            'mapping': {'enabled': True},
        }}
        self.mask = np.full((192, 192), 255, np.uint8)

    def update(self, now, valid=True, shift=None):
        zeros = np.zeros_like(self.mask)
        with patch.object(MinimapMemory, '_frame_terrain', return_value=(self.mask, valid, zeros, zeros, 192)), \
             patch.object(MinimapMemory, '_translation', return_value=(shift, 'tracked' if shift is not None else 'unregistered')):
            return self.memory.update(self.frame, self.settings, now=now)

    def test_route_follows_scroll_without_moving_player(self):
        self.update(10)
        self.memory.route = [[.5, .5], [.75, .5], [.8, .6]]
        self.update(10.1, shift=np.array([-8., 4.]))
        np.testing.assert_allclose(self.memory.route[0], [.5, .5])
        np.testing.assert_allclose(self.memory.route[1], [.75-8/192, .5+4/192])
        self.assertTrue(self.memory.snapshot(now=10.1)['valid'])

    def test_failed_registration_does_not_publish_old_route(self):
        self.update(10)
        self.memory.route = [[.5, .5], [.75, .5]]
        self.assertIsNone(self.update(10.1))
        self.assertFalse(self.memory.snapshot(now=10.1)['valid'])
        self.assertEqual(self.memory.route, [])

    def test_registration_failure_retains_history_for_thirty_seconds(self):
        self.update(10)
        segment=self.memory.segment
        visits=self.memory.visits.copy()
        self.frame[:]=100
        self.assertIsNone(self.update(10.1))
        self.assertIsNone(self.update(20))
        self.assertEqual(self.memory.segment,segment)
        self.assertEqual(self.memory.visits,visits)
        self.assertIsNotNone(self.update(40.2))
        self.assertGreater(self.memory.segment,segment)
        self.assertTrue(self.memory.archives)

    def test_invalid_terrain_does_not_claim_partial_validity(self):
        self.update(10)
        self.assertIsNone(self.update(10.1, valid=False))
        self.assertFalse(self.memory.snapshot(now=10.1)['valid'])

    def test_new_wall_clears_retained_direction(self):
        self.update(10)
        self.memory.route = [[.5, .5], [.75, .5]]
        self.mask[:, 116:124] = 0
        self.update(10.1, shift=np.zeros(2))
        self.assertTrue(self.memory.valid)
        self.assertEqual(self.memory.route, [])

    def test_blocked_player_cannot_be_reported_valid(self):
        self.update(10)
        self.mask[92:104, 92:104] = 0
        self.assertIsNone(self.update(10.1, shift=np.zeros(2)))
        self.assertFalse(self.memory.valid)

    def test_short_click_still_inspects_distant_straight_target(self):
        self.update(10)
        result=self.memory.suggest((1, 0), explore=False, now=10.1,
                                   lookahead_px=12, target_world=np.array([174., 98.]))
        self.assertIsNotNone(result)
        np.testing.assert_allclose(self.memory.route[1], [174/192, 98/192])
        self.assertAlmostEqual(result[1]*192, 12)

    def test_default_inspection_is_not_limited_to_six_cells(self):
        self.update(10)
        result=self.memory.suggest((1, 0), explore=False, now=10.1,
                                   target_world=np.array([174., 98.]))
        self.assertIsNotNone(result)
        self.assertGreater((self.memory.route[1][0]-.5)*192, 60)

    def test_distant_inspection_does_not_cross_wall_at_corner(self):
        self.mask[:,:]=0
        self.mask[88:108,88:164]=255
        self.mask[88:164,144:164]=255
        self.update(10)
        result=self.memory.suggest((1, 0), explore=False, now=10.1,
                                   lookahead_px=12,target_world=np.array([154.,154.]))
        self.assertIsNotNone(result)
        from app.core.route_geometry import corridor_clear
        endpoint=np.array(self.memory.route[1])*192/self.memory.CELL
        self.assertTrue(corridor_clear(np.array([96.,96.])/self.memory.CELL,
                                       endpoint,self.memory.grid))
        self.assertLess(self.memory.route[1][1]*192, 120)

    def test_exploration_prefers_unvisited_over_forward_visited_floor(self):
        self.update(10)
        for y,x in np.ndindex(self.memory.grid.shape):
            if x>=24:
                self.memory.visits[self.memory._key((np.array([x,y])+.5)*4)]=10
        self.memory.direction_priority=True
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.1))
        self.assertNotIn(self.memory._key(self.memory.goal),self.memory.visits)

    def test_exploration_can_return_when_all_floor_has_been_visited(self):
        self.update(10)
        for y,x in np.ndindex(self.memory.grid.shape):
            self.memory.visits[self.memory._key((np.array([x,y])+.5)*4)]=10
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.1))

    def test_small_movements_accumulate_into_visited_cells(self):
        self.update(10)
        for index in range(1,21):
            self.update(10+index*.03,shift=np.array([-.3,0.]))
        self.assertIn(self.memory._key(np.array([102.,96.])),self.memory.visits)
        self.assertGreaterEqual(self.memory.recorded_position[0],101.5)

    def test_edge_priority_precedes_unvisited_interior(self):
        self.update(10)
        for y,x in np.ndindex(self.memory.grid.shape):
            if min(x,y,47-x,47-y)<3:
                self.memory.visits[self.memory._key((np.array([x,y])+.5)*4)]=2
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.1))
        x,y=np.floor(self.memory.goal/4).astype(int)
        self.assertLess(min(x,y,47-x,47-y),3)

    def test_recent_measured_trace_is_avoided_when_green_cells_are_equal(self):
        self.update(10)
        self.memory.visits.clear()
        self.memory.breadcrumbs.clear()
        for y in range(24):self.memory.breadcrumbs.append(np.array([190.,(y+.5)*4]))
        self.memory.direction_priority=True
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.1))
        recent={self.memory._key(point) for point in self.memory.breadcrumbs}
        self.assertNotIn(self.memory._key(self.memory.goal),recent)

    def test_clear_current_heading_wins_over_unvisited_return_route(self):
        self.update(10)
        self.memory.last_direction=(1.,0.)
        for y,x in np.ndindex(self.memory.grid.shape):
            if x>=24:self.memory.visits[self.memory._key((np.array([x,y])+.5)*4)]=4
        self.assertIsNotNone(self.memory.suggest((-1,0),explore=True,now=10.1))
        self.assertGreater(self.memory.goal[0],150)
        self.assertGreater(self.memory.last_direction[0],.98)

    def test_obstacle_allows_change_from_current_heading(self):
        self.mask[:]=0
        self.mask[88:172,88:108]=255
        self.update(10)
        self.memory.last_direction=(1.,0.)
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.1))
        self.assertGreater(self.memory.last_direction[1],.8)

    def test_locked_heading_does_not_change_for_new_frontier_or_scrolling(self):
        self.update(10)
        self.memory.last_direction=(1.,0.)
        self.memory.lock_current_heading=True
        destination=None
        for i in range(1,5):
            self.update(10+i*.05,shift=np.array([-2.,1.]))
            result=self.memory.suggest((0,-1),explore=True,now=10+i*.05)
            self.assertIsNotNone(result)
            self.assertGreater(result[0][0],.98)
            if destination is None:destination=self.memory.goal.copy()
            np.testing.assert_allclose(self.memory.goal,destination)

    def test_locked_heading_changes_only_when_forward_corridor_ends(self):
        self.mask[:]=0
        self.mask[88:172,88:100]=255
        self.update(10)
        self.memory.last_direction=(1.,0.)
        self.memory.lock_current_heading=True
        result=self.memory.suggest((1,0),explore=True,now=10.1)
        self.assertIsNotNone(result)
        self.assertGreater(result[0][1],.8)

    def test_locked_destination_is_not_replaced_as_map_scrolls(self):
        self.update(10)
        self.memory.last_direction=(1.,0.)
        self.memory.lock_current_heading=True
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.01))
        destination=self.memory.goal.copy()
        for i in range(1,5):
            self.update(10+i*.05,shift=np.array([-2.,0.]))
            self.assertIsNotNone(self.memory.suggest((0,-1),explore=True,now=10+i*.05))
            np.testing.assert_allclose(self.memory.goal,destination)

    def test_new_goal_avoids_visited_direction_even_if_previous_heading_is_clear(self):
        self.update(10)
        self.memory.last_direction=(1.,0.)
        self.memory.lock_current_heading=True
        self.memory.prefer_unvisited=True
        for y,x in np.ndindex(self.memory.grid.shape):
            if x>=24:self.memory.visits[self.memory._key((np.array([x,y])+.5)*4)]=5
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.1))
        self.assertLess(self.memory.goal[0],96)
        self.assertNotIn(self.memory._key(self.memory.goal),self.memory.visits)

    def test_distant_candidates_survive_rejected_shortcut(self):
        from app.core.minimap_memory import shortcut_cost
        self.update(10)
        def reject_near(start,end,clearance,weight):
            return 1e9 if np.linalg.norm(np.array(end)-start)<4 else shortcut_cost(start,end,clearance,weight)
        with patch('app.core.minimap_memory.shortcut_cost',side_effect=reject_near):
            result=self.memory.suggest((1,0),explore=False,now=10.1,
                                       target_world=np.array([174.,98.]))
        self.assertIsNotNone(result)
        np.testing.assert_allclose(self.memory.route[1],[174/192,98/192])

    def test_stuck_measurement_keeps_locked_destination_for_wall_probe(self):
        self.update(10)
        self.memory.lock_current_heading=True
        self.memory.goal=np.array([174.,98.])
        self.memory.pending.append((10.,self.memory.position.copy(),(1.,0.)))
        self.update(12.1,shift=np.zeros(2))
        self.assertTrue(self.memory.stuck)
        np.testing.assert_allclose(self.memory.goal,[174.,98.])
        self.assertEqual(self.memory.recovery_stage,'none')

    def test_player_near_floor_recovers_after_two_fresh_maps(self):
        self.mask[:,96:]=0
        self.assertIsNone(self.update(10))
        segment=self.memory.segment
        self.memory.goal=np.array([30.,98.])
        self.assertIsNotNone(self.update(10.1,shift=np.zeros(2)))
        self.assertTrue(self.memory.valid)
        self.assertLess(self.memory.player[0],.5)
        self.assertEqual(self.memory.segment,segment)
        np.testing.assert_allclose(self.memory.goal,[30.,98.])
        self.assertFalse(self.memory.grid[24,24])
        self.assertIsNotNone(self.memory.suggest((-1,0),now=10.1))

    def test_player_far_from_floor_is_not_projected_through_wall(self):
        self.mask[:,80:]=0
        self.assertIsNone(self.update(10))
        self.assertIsNone(self.update(10.1,shift=np.zeros(2)))
        self.assertFalse(self.memory.valid)

    def test_destination_display_survives_route_loss_after_skill(self):
        self.update(10)
        self.memory.goal=np.array([174.,98.])
        expected=self.memory.planned_target()
        self.memory.route=[]
        self.memory.valid=False
        self.assertEqual(self.memory.planned_target(),expected)
        self.memory.origin+=np.array([4.,0.])
        self.assertLess(self.memory.planned_target()[0],expected[0])
        self.memory.request_replan()
        retained=self.memory.planned_target()
        self.assertIsNotNone(retained)
        self.assertIsNone(self.memory.goal)
        self.update(10.1,valid=False)
        self.assertEqual(self.memory.planned_target(),retained)
        self.update(10.2,shift=np.zeros(2))
        self.assertIsNotNone(self.memory.suggest((-1,0),now=10.2))
        self.assertIsNone(self.memory.replan_goal)
        self.assertIsNotNone(self.memory.planned_target())

    def test_new_goal_avoids_recent_completed_destination(self):
        self.update(10)
        self.memory.prefer_unvisited=True
        self.memory.suggest((1,0),explore=True,now=10.1)
        previous=self.memory.goal.copy()
        self.memory.completed_goals.append(previous)
        self.memory.goal=None
        self.assertIsNotNone(self.memory.suggest((1,0),explore=True,now=10.2))
        self.assertGreater(np.linalg.norm(self.memory.goal-previous),self.memory.CELL*3)

    def test_guide_destination_is_not_cut_to_32_pixels(self):
        from app.vision.orange_route import orange_route_target
        mask=np.zeros((192,192),np.uint8)
        mask[96,96:180]=255
        result=orange_route_target(mask,np.array([96.,96.]),np.zeros(2),{})
        self.assertGreater(result[0]-96,70)

    def test_low_contrast_scroll_is_tracked_before_stationary_fallback(self):
        import cv2
        rng=np.random.default_rng(42)
        gray=cv2.GaussianBlur(rng.integers(60,160,(192,192),dtype=np.uint8),(7,7),0)
        current=cv2.warpAffine(gray,np.float32([[1,0,.4],[0,1,0]]),(192,192),borderMode=cv2.BORDER_REFLECT)
        shift,status=MinimapMemory._translation(gray,current,[.5,.5])
        self.assertIsNotNone(shift)
        self.assertNotEqual(status,'stationary')
        self.assertGreater(shift[0],.2)


if __name__ == '__main__':
    unittest.main()
