import unittest
from unittest.mock import Mock, patch

from app.core.control_hotkeys import MODIFIERS, WM_HOTKEY, watch_control_hotkeys, begin_bot_keys, end_bot_keys, bot_key_active


class WindowsHotkeysTests(unittest.TestCase):
    def watch(self, frames, registration=True, frame_seconds=1, echo_frames=()):
        index = 0
        messages = [list(frame[1]) for frame in frames]
        pressed = []
        stop = Mock()
        stop.is_set.side_effect = lambda: index == len(frames)
        def advance(_):
            nonlocal index
            index += 1
        stop.wait.side_effect = advance
        user32 = Mock()
        user32.RegisterHotKey.return_value = registration
        def peek(pointer, *_):
            if not messages[index]:return False
            pointer._obj.message = WM_HOTKEY
            pointer._obj.wParam = messages[index].pop(0)
            return True
        user32.PeekMessageW.side_effect = peek
        user32.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in frames[index][2] else 0
        with patch('app.core.control_hotkeys.time.monotonic',side_effect=lambda:index*frame_seconds), \
             patch('app.core.control_hotkeys.bot_key_active',side_effect=lambda n:n==2 and index in echo_frames):
            watch_control_hotkeys(stop, lambda: frames[index][0], pressed.append, user32=user32)
        return pressed, user32

    def test_same_hotkey_is_limited_to_once_per_second_without_blocking_combinations(self):
        frames=[(True,[4],set()),(True,[4,3],set())]
        frames.extend((True,[],set()) for _ in range(8))
        frames.append((True,[4],set()))
        pressed,_=self.watch(frames,frame_seconds=.1)
        self.assertEqual(pressed,[4,3,4])

    def test_registered_notifications_are_received_only_in_game(self):
        pressed, user32 = self.watch([(True, [1], set()),(True,[2],{0x11,0x10,0x32}),
                                     (False, [3], set()), (True, [4, 5], set())])
        self.assertEqual(pressed, [1, 2, 4, 5])
        self.assertEqual(user32.RegisterHotKey.call_count, 5)
        user32.RegisterHotKey.assert_any_call(None, 1, MODIFIERS, 0x31)
        self.assertTrue(MODIFIERS & 0x4000)
        self.assertTrue(MODIFIERS & 0x0004)
        self.assertTrue(MODIFIERS & 0x0002)
        self.assertFalse(MODIFIERS & 0x0001)
        user32.RegisterHotKey.assert_any_call(None, 5, MODIFIERS, 0x35)
        self.assertEqual(user32.UnregisterHotKey.call_count, 5)

    def test_failed_registration_fallback_ignores_hold_and_focus_return(self):
        chord = {0x11, 0x10, 0x31}
        with self.assertLogs('app.core.control_hotkeys', level='WARNING'):
            pressed, user32 = self.watch([(True, [], set()), (True, [], chord),
                                         (True, [], chord), (True, [], set()), (False, [], set()),
                                         (False, [], chord), (True, [], chord),
                                         (True, [], set()), (True, [], chord),
                                         (True, [], set())], registration=False)
        self.assertEqual(pressed, [1, 1])
        user32.UnregisterHotKey.assert_not_called()

    def test_repeat_skill_waits_for_modifier_release_and_ignores_following_skill_two(self):
        modifiers={0x11,0x10}
        pressed,_=self.watch([(True,[4],modifiers|{0x34}),
                              (True,[2],modifiers|{0x32}),
                              (True,[],set()),
                              (True,[2],modifiers|{0x32})])
        self.assertEqual(pressed,[4,2])

    def test_stop_remains_immediate_and_pending_start_is_dropped_outside_game(self):
        modifiers={0x11,0x10}
        pressed,_=self.watch([(True,[2],modifiers|{0x32}),
                              (True,[],set()),
                              (True,[4],modifiers|{0x34}),
                              (False,[],set())])
        self.assertEqual(pressed,[2])

    def test_delayed_stop_echo_is_ignored_and_real_stop_still_works(self):
        pressed,_=self.watch([(True,[4],{0x11,0x10,0x34}),
                              (True,[],set()),(True,[2],set()),
                              (True,[2],{0x11,0x10,0x32}),
                              (True,[],set()),(True,[2],{0x11,0x10,0x32})],
                              echo_frames={3})
        self.assertEqual(pressed,[4,2])

    def test_bot_numeric_key_is_marked_until_short_transmission_tail_expires(self):
        with patch.dict('app.core.control_hotkeys._bot_keys',{},clear=True), \
             patch('app.core.control_hotkeys.time.monotonic',return_value=10):
            keys=begin_bot_keys([31,0])
            self.assertTrue(bot_key_active(2))
            self.assertFalse(bot_key_active(4))
            end_bot_keys(keys,30)
            self.assertTrue(bot_key_active(2))
            with patch('app.core.control_hotkeys.time.monotonic',return_value=10.181):
                self.assertFalse(bot_key_active(2))
