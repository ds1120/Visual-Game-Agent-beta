import unittest
from types import SimpleNamespace

from app.controller.input_bindings import binding_for_command
from app.profiles.runtime_settings import validate_settings


class MovementSkillBindingTests(unittest.TestCase):
    def test_escape_uses_registered_key(self):
        settings={'movement_skill':{'enabled':True,'key':'E'},'bindings':{'DODGE':'SPACE'}}
        c=SimpleNamespace(action_type='DODGE',source='NAVIGATION_ESCAPE',skill_id=None)
        self.assertEqual(binding_for_command(settings,c),'E')
        c.source='HP_RETREAT'
        self.assertEqual(binding_for_command(settings,c),'SPACE')

    def test_unregistered_or_disabled_escape_is_rejected(self):
        c=SimpleNamespace(action_type='DODGE',source='NAVIGATION_ESCAPE',skill_id=None)
        for settings in ({},{'movement_skill':{'enabled':False,'key':'E'}}):
            with self.assertRaises(ValueError):binding_for_command(settings,c)

    def test_invalid_registration_is_rejected(self):
        for skill in ({'enabled':'true','key':'E'},{'enabled':True,'key':'INVALID'}):
            with self.assertRaisesRegex(ValueError,'이동 스킬'):
                validate_settings('input.json',{'version':1,'movement_skill':skill})
