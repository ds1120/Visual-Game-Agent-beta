import unittest
from unittest.mock import patch
import cv2
import numpy as np

from app.ai.main_agent import MainAgent
from app.vision.screen_route_guide import screen_route_guide


class ScreenRouteGuideTests(unittest.TestCase):
    def test_template_matching_accepts_red_arrow(self):
        frame=self.frame()
        frame[np.all(frame==[0,0,0],axis=2)]=[0,0,255]
        self.assertIsNotNone(screen_route_guide(frame,arrow_only=True))

    def test_near_black_antialiasing_is_detected(self):
        frame=self.frame()
        frame[np.all(frame==[0,0,0],axis=2)]=[20,20,20]
        self.assertIsNotNone(screen_route_guide(frame,arrow_only=True))

    def test_template_matching_accepts_white_arrow(self):
        frame=self.frame()
        frame[np.all(frame==[0,0,0],axis=2)]=[225,225,225]
        self.assertIsNotNone(screen_route_guide(frame,arrow_only=True))

    def test_template_matching_handles_rotation_size_and_ignores_plain_objects(self):
        polygon=np.array([[20,35],[48,56],[77,35],[69,51],[48,70],[27,51]],np.float32)
        for angle in range(0,360,30):
            for scale in (.7,1.,1.3):
                frame=np.full((600,960,3),80,np.uint8)
                transform=cv2.getRotationMatrix2D((48,48),angle,scale)
                points=cv2.transform(polygon.reshape(-1,1,2),transform)
                points+=(np.array([550,200])-np.array([48,48]))
                cv2.fillPoly(frame,[points.round().astype(np.int32)],(0,0,0))
                self.assertIsNotNone(screen_route_guide(frame,arrow_only=True),msg=f'{angle=} {scale=}')
        frame=np.full((600,960,3),80,np.uint8)
        cv2.rectangle(frame,(540,180),(590,210),(0,0,0),-1)
        cv2.circle(frame,(630,230),25,(0,0,0),-1)
        self.assertIsNone(screen_route_guide(frame,arrow_only=True))

    def test_forward_arrow_wins_over_previous_passed_marker(self):
        frame=self.frame()
        polygon=np.array([[520,460],[548,439],[577,460],[569,444],[548,425],[527,444]],np.int32)
        cv2.fillPoly(frame,[polygon],(0,0,0))
        guide=screen_route_guide(frame,arrow_only=True,previous_marker=(548/960,442/600),
                                 preferred_heading=(0.,-1.))
        self.assertLess(guide['marker'][1],.5)

    def test_position_only_mode_never_reads_shape_polarity(self):
        with patch('app.vision.screen_route_guide.cv2.convexityDefects',
                   side_effect=AssertionError('Shape direction must not be read')):
            guide=screen_route_guide(self.frame(),arrow_only=True)
        self.assertIsNotNone(guide)
        delta=(np.array(guide['marker'])-[.5,.5])*[960,600]
        np.testing.assert_allclose(guide['direction'],delta/np.linalg.norm(delta))
        self.assertEqual(guide['arrow_tip'],guide['marker'])

    def test_self_intersecting_arrow_candidate_does_not_crash_detection(self):
        with patch('app.vision.screen_route_guide.cv2.convexityDefects',
                   side_effect=cv2.error('The convex hull indices are not monotonous')):
            guide,marker,dots=screen_route_guide(self.frame(),with_presence=True)
        self.assertFalse(marker)
        self.assertTrue(dots)
        self.assertEqual(guide['source'],'white_dots')

    def test_bad_contour_does_not_discard_other_valid_arrow_candidates(self):
        frame=self.frame()
        polygon=np.array([[640,290],[668,269],[697,290],[689,274],[668,255],[647,274]],np.int32)
        cv2.fillPoly(frame,[polygon],(0,0,0))
        original=cv2.convexityDefects
        calls=[0]
        def defects(contour,hull):
            calls[0]+=1
            if calls[0]==1:raise cv2.error('The convex hull indices are not monotonous')
            return original(contour,hull)
        with patch('app.vision.screen_route_guide.cv2.convexityDefects',side_effect=defects):
            guide,marker,_=screen_route_guide(frame,with_presence=True)
        self.assertGreaterEqual(calls[0],2)
        self.assertTrue(marker)
        self.assertEqual(guide['source'],'black_arrow')

    def test_arrow_polarity_stays_correct_through_eight_rotations(self):
        polygon=np.array([[520,210],[548,231],[577,210],[569,226],
                          [548,245],[527,226]],np.float32)
        for angle in range(0,360,45):
            frame=np.full((600,960,3),50,np.uint8)
            transform=cv2.getRotationMatrix2D((548,220),angle,1)
            rotated=cv2.transform(polygon.reshape(-1,1,2),transform).round().astype(np.int32)
            cv2.fillPoly(frame,[rotated],(0,0,0))
            guide=screen_route_guide(frame)
            self.assertIsNotNone(guide,msg=f'angle={angle}')
            expected=transform[:,:2]@np.array([0.,1.])
            self.assertGreater(np.array(guide['arrow_direction'])@expected,.97,msg=f'angle={angle}')
            expected_tip=transform@np.array([548.,245.,1.])
            np.testing.assert_allclose(np.array(guide['arrow_tip'])*[960,600],expected_tip,atol=2)

    def frame(self):
        frame=np.full((600,960,3),50,np.uint8)
        polygon=np.array([[520,210],[548,189],[577,210],[569,194],[548,175],[527,194]],np.int32)
        cv2.fillPoly(frame,[polygon],(0,0,0))
        for point in ((548,320),(590,220),(620,150)):
            cv2.circle(frame,point,5,(240,240,240),-1)
        return frame

    def test_black_arrow_and_chain_select_outermost_breadcrumb(self):
        guide=screen_route_guide(self.frame())
        self.assertIsNotNone(guide)
        self.assertAlmostEqual(guide['target'][0],620/960,places=3)
        self.assertAlmostEqual(guide['target'][1],150/600,places=3)
        self.assertEqual(len(guide['dots']),2)
        self.assertGreater(guide['direction'][0],0)

    def test_resolution_scaling_keeps_direction_and_target(self):
        original=screen_route_guide(self.frame())
        large=screen_route_guide(cv2.resize(self.frame(),(1920,1200)))
        self.assertIsNotNone(large)
        np.testing.assert_allclose(original['target'],large['target'],atol=.003)

    def test_black_chevron_uses_pointing_direction_and_is_not_a_dot(self):
        frame=np.full((600,960,3),50,np.uint8)
        chevron=np.array([[520,210],[548,231],[577,210],[569,226],
                          [548,245],[527,226]],np.int32)
        cv2.fillPoly(frame,[chevron],(0,0,0))
        for point in ((548,320),(590,400),(620,480),(600,130),(640,70)):
            cv2.circle(frame,point,5,(240,240,240),-1)
        guide,marker,dots=screen_route_guide(frame,with_presence=True)
        self.assertTrue(marker);self.assertTrue(dots)
        self.assertIsNotNone(guide)
        np.testing.assert_allclose(guide['target'],(620/960,480/600),atol=.004)
        self.assertGreater(guide['direction'][1],0)
        np.testing.assert_allclose(np.array(guide['arrow_tip'])*[960,600],[548,245],atol=1)
        np.testing.assert_allclose(guide['arrow_direction'],[0,1],atol=.03)
        self.assertTrue(all(point[1]>.5 for point in guide['dots']))

    def test_black_arrow_alone_is_not_random_movement_or_breadcrumb(self):
        frame=np.full((600,960,3),50,np.uint8)
        chevron=np.array([[520,210],[548,231],[577,210],[569,226],
                          [548,245],[527,226]],np.int32)
        cv2.fillPoly(frame,[chevron],(0,0,0))
        guide,marker,dots=screen_route_guide(frame,with_presence=True)
        self.assertEqual((marker,dots),(True,False))
        self.assertEqual(guide['source'],'black_arrow')
        np.testing.assert_allclose(guide['direction'],(0,1),atol=.03)

    def test_black_arrow_overrides_conflicting_breadcrumbs(self):
        frame=np.full((600,960,3),50,np.uint8)
        chevron=np.array([[520,210],[548,231],[577,210],[569,226],
                          [548,245],[527,226]],np.int32)
        cv2.fillPoly(frame,[chevron],(0,0,0))
        for point in ((500,200),(520,120),(540,55)):
            cv2.circle(frame,point,5,(240,240,240),-1)
        guide=screen_route_guide(frame)
        self.assertEqual(guide['source'],'black_arrow')
        self.assertEqual(guide['dots'],[])
        np.testing.assert_allclose(guide['direction'],(0,1),atol=.03)

    def test_animated_elongated_irregular_and_faded_breadcrumbs(self):
        frame=self.frame()
        for point in ((548,320),(590,220),(620,150)):
            cv2.circle(frame,point,9,(50,50,50),-1)
        cv2.ellipse(frame,(548,320),(8,3),15,0,360,(240,240,240),-1)
        cv2.fillPoly(frame,[np.array([[585,218],[592,215],[596,219],
                                     [591,226],[588,223]],np.int32)],(185,185,185))
        faded=np.zeros_like(frame)
        cv2.circle(faded,(620,150),5,(110,110,110),-1)
        frame=cv2.add(frame,cv2.GaussianBlur(faded,(7,7),0))
        guide=screen_route_guide(frame)
        self.assertIsNotNone(guide)
        self.assertEqual(len(guide['dots']),2)
        np.testing.assert_allclose(guide['target'],(620/960,150/600),atol=.004)

    def test_opposite_edge_dots_do_not_override_arrow_condition(self):
        frame=self.frame()
        for point in ((420,350),(300,410),(160,460),(65,490)):
            cv2.circle(frame,point,5,(240,240,240),-1)
        guide=screen_route_guide(frame)
        np.testing.assert_allclose(guide['target'],(620/960,150/600),atol=.004)

    def test_presence_distinguishes_no_guides_from_partial_guides(self):
        frame=np.full_like(self.frame(),50)
        self.assertEqual(screen_route_guide(frame,with_presence=True),(None,False,False))
        cv2.circle(frame,(590,220),5,(240,240,240),-1)
        guide,marker,dots=screen_route_guide(frame,with_presence=True)
        self.assertEqual((marker,dots),(False,True))
        self.assertEqual(guide['source'],'white_dots')
        frame=self.frame();frame[140:331,580:631]=50;frame[310:331,538:560]=50
        guide,marker,dots=screen_route_guide(frame,with_presence=True)
        self.assertEqual((marker,dots),(True,False))
        self.assertEqual(guide['dots'],[])

    def test_random_heading_is_stable_then_changes(self):
        agent=MainAgent.__new__(MainAgent)
        with patch('app.ai.main_agent.random.uniform',side_effect=[0,np.pi/2]) as draw:
            with patch('app.ai.main_agent.time.monotonic',return_value=10):
                self.assertEqual(agent._random_move_heading(),(1.,0.))
            with patch('app.ai.main_agent.time.monotonic',return_value=11):
                self.assertEqual(agent._random_move_heading(),(1.,0.))
            with patch('app.ai.main_agent.time.monotonic',return_value=12.1):
                np.testing.assert_allclose(agent._random_move_heading(),(0.,1.),atol=1e-6)
            self.assertEqual(draw.call_count,2)

    def test_large_bright_effect_does_not_become_breadcrumb(self):
        frame=np.full_like(self.frame(),50)
        cv2.ellipse(frame,(600,200),(35,25),0,0,360,(230,230,230),-1)
        self.assertIsNone(screen_route_guide(frame))

    def test_whites_without_arrow_supply_second_priority_route(self):
        frame=self.frame()
        frame[170:214,518:580]=50
        guide=screen_route_guide(frame)
        self.assertEqual(guide['source'],'white_dots')
        np.testing.assert_allclose(guide['target'],(620/960,150/600),atol=.004)

    def test_hud_exclusion_removes_marker(self):
        self.assertIsNone(screen_route_guide(self.frame(),excluded=[(.54,.29,.60,.36)])['marker'])

    def test_misaligned_white_dot_does_not_override_arrow(self):
        frame=self.frame()
        frame[140:231,580:631]=50
        self.assertEqual(screen_route_guide(frame)['dots'],[])

    def test_short_occlusion_keeps_guide_then_expires(self):
        agent=MainAgent.__new__(MainAgent)
        agent._latest_frame=self.frame()
        agent._world_player_origin=lambda:(.5,.5)
        agent._world_click_exclusions=lambda:[]
        with patch('app.ai.main_agent.time.monotonic',return_value=10):
            first=agent._update_screen_move_guide()
        self.assertIsNotNone(first)
        agent._latest_frame=np.full_like(agent._latest_frame,50)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.3):
            self.assertIs(agent._update_screen_move_guide(),first)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.81):
            self.assertIsNone(agent._update_screen_move_guide())
