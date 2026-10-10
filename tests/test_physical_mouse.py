import unittest
from unittest.mock import Mock
from app.core.physical_mouse import PhysicalMouse


class PhysicalMouseTests(unittest.TestCase):
    def test_esp32_hold_is_not_a_user_click_even_with_native_button_down(self):
        mouse=PhysicalMouse()
        mouse.identify('esp32',r'\\?\HID#VID&02303A_PID&4001#device')
        mouse.update('esp32',1)
        self.assertFalse(mouse.down(lambda:True))
        mouse.update('user',1)
        self.assertTrue(mouse.down(lambda:True))
        mouse.update('esp32',2)
        self.assertTrue(mouse.down(lambda:True))
        mouse.update('user',2)
        mouse.update('esp32',1)
        self.assertFalse(mouse.down(lambda:True))

    def test_missing_raw_release_recovers_after_confirmed_native_release(self):
        mouse=PhysicalMouse();mouse.update(1,1)
        mouse.reconcile(True,10)
        mouse.reconcile(False,11)
        self.assertTrue(mouse.down(lambda:False))
        mouse.reconcile(False,11.21)
        self.assertFalse(mouse.down(lambda:False))

    def test_native_hold_and_brief_false_read_never_resume(self):
        mouse=PhysicalMouse();mouse.update(1,1)
        mouse.reconcile(True,10);mouse.reconcile(False,10.1)
        mouse.reconcile(True,10.2);mouse.reconcile(False,10.3)
        self.assertTrue(mouse.down(lambda:False))

    def test_zero_native_state_without_confirmed_access_does_not_clear_hold(self):
        mouse=PhysicalMouse();mouse.update(1,1)
        mouse.reconcile(False,10);mouse.reconcile(False,11)
        self.assertTrue(mouse.down(lambda:False))
    def test_other_device_release_cannot_release_user_mouse(self):
        mouse=PhysicalMouse();mouse.update('user',1)
        mouse.update('esp32',2)
        fallback=Mock(return_value=False)
        self.assertTrue(mouse.down(fallback))
        fallback.assert_not_called()
        mouse.update('user',2)
        self.assertFalse(mouse.down(fallback))

    def test_hold_survives_movement_and_repeated_release_on_other_device(self):
        mouse=PhysicalMouse();mouse.update(1,1)
        for _ in range(10):
            mouse.update(1,0);mouse.update(2,2)
            self.assertTrue(mouse.down(lambda:False))
        mouse.update(1,2)
        self.assertFalse(mouse.down(lambda:True))

    def test_fallback_only_until_first_button_event(self):
        mouse=PhysicalMouse()
        self.assertTrue(mouse.down(lambda:True))
        mouse.update(1,2)
        self.assertFalse(mouse.down(lambda:True))
