import unittest
from types import SimpleNamespace

import numpy as np

from app.core.local_navigation import LocalNavigator


class NavigationClickLimitTests(unittest.TestCase):
    def settings(self):
        return {'obstacle_margin':.025,'step_fraction':.03,
            'player_screen':(.5,.5),'projection':'isotropic',
            'strict_route':True,'exact_click_distance':True,
            'minimum_move_pixels':18,'minimum_click_distance':18/1920,
            'map_screen_scale':12,
            'minimap':{'enabled':True,'player':[.5,.5],
                'rotation_degrees':0,'mapping':{'enabled':True},
                'verified_route':True,'lookahead':.03*1920/12/192}}

    def test_short_verified_step_is_not_forced_to_ten_percent(self):
        settings=self.settings()
        mask=np.zeros((192,192),np.uint8)
        mask[92:104,92:102]=255
        result=LocalNavigator().choose((1,0),[],(1080,1920,3),settings,mask)
        self.assertIsNotNone(result)
        self.assertAlmostEqual((result[1][0]-.5)*1920,57.6)

    def test_obscured_endpoint_can_use_shorter_click_on_same_route(self):
        settings=self.settings()
        obstacle=SimpleNamespace(object_type='npc',detector_type='npc',
            confidence=1.,bbox=(1020,535,1030,545))
        # A narrow excluded HUD region blocks only the full-distance endpoint.
        settings['excluded_regions']=[(.529,.49,.531,.51)]
        result=LocalNavigator().choose((1,0),[],(1080,1920,3),settings,
            np.full((192,192),255,np.uint8))
        self.assertIsNotNone(result)
        self.assertLess(result[1][0],.529)

    def test_tiny_verified_step_is_rejected_without_extending_through_wall(self):
        settings=self.settings()
        settings['step_fraction']=.005
        mask=np.full((192,192),255,np.uint8)
        self.assertIsNone(LocalNavigator().choose((1,0),[],(1080,1920,3),settings,mask))
