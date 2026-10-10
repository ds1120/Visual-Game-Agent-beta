import unittest
from unittest.mock import Mock, patch

from app.core.control_hotkeys import MODIFIERS, WM_HOTKEY, watch_control_hotkeys, begin_bot_keys, end_bot_keys, bot_key_active


class WindowsHotkeysTests(unittest.TestCase):
    def test_middle_button_toggles_on_each_click_without_hold_repeat(self):
        pressed,_=self.watch([(True,[],{0x04}),(True,[],{0x04}),
                              (True,[],set()),(True,[],{0x04}),
                              (False,[],set()),(False,[],{0x04})],frame_seconds=.1)
        self.assertEqual(pressed,[6,6])
    def test_tab_toggles_once_per_press_and_only_in_game(self):
        pressed,_=self.watch([(True,[],{0x09}),(True,[],{0x09}),
                              (True,[],set()),(True,[],{0x09}),
                              (False,[],set()),(False,[],{0x09}),
                              (True,[],{0x09}),(True,[],set()),(True,[],{0x09})])
        self.assertEqual(pressed,[0,0,0])

    def test_ctrl_without_shift_works_in_key_state_fallback(self):
        with self.assertLogs('app.core.control_hotkeys',level='WARNING'):
            pressed,_=self.watch([(True,[],set()),(True,[],{0x11,0x33}),
                                  (True,[],set()),(True,[],{0x11,0x32})],registration=False)
        self.assertEqual(pressed,[3,2])

    def watch(self, frames, registration=True, frame_seconds=1, echo_frames=(), echo_numbers=(2,)):
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
             patch('app.core.control_hotkeys.bot_key_active',side_effect=lambda n:n in echo_numbers and index in echo_frames):
            watch_control_hotkeys(stop, lambda: frames[index][0], pressed.append, user32=user32)
        return pressed, user32

    def test_same_hotkey_is_limited_to_once_per_second_without_blocking_combinations(self):
        frames=[(True,[4],set()),(True,[4,3],set())]
        frames.extend((True,[],set()) for _ in range(8))
        frames.append((True,[4],set()))
        pressed,_=self.watch(frames,frame_seconds=.1)
        self.assertEqual(pressed,[4,3,4])

    def test_registered_notifications_are_received_only_in_game(self):
        pressed, user32 = self.watch([(True, [1], set()),(True,[2],{0x11,0x32}),
                                     (False, [3], set()), (True, [4, 5], set())])
        self.assertEqual(pressed, [1, 2, 4, 5])
        self.assertEqual(user32.RegisterHotKey.call_count, 5)
        user32.RegisterHotKey.assert_any_call(None, 1, MODIFIERS, 0x31)
        self.assertTrue(MODIFIERS & 0x4000)
        self.assertFalse(MODIFIERS & 0x0004)
        self.assertTrue(MODIFIERS & 0x0002)
        self.assertFalse(MODIFIERS & 0x0001)
        user32.RegisterHotKey.assert_any_call(None, 5, MODIFIERS, 0x35)
        self.assertEqual(user32.UnregisterHotKey.call_count, 5)

    def test_failed_registration_fallback_ignores_hold_and_focus_return(self):
        chord = {0x11, 0x31}
        with self.assertLogs('app.core.control_hotkeys', level='WARNING'):
            pressed, user32 = self.watch([(True, [], set()), (True, [], chord),
                                         (True, [], chord), (True, [], set()), (False, [], set()),
                                         (False, [], chord), (True, [], chord),
                                         (True, [], set()), (True, [], chord),
                                         (True, [], set())], registration=False)
        self.assertEqual(pressed, [1, 1])
        user32.UnregisterHotKey.assert_not_called()

    def test_repeat_ignores_following_skill_two_until_ctrl_is_released(self):
        modifiers={0x11}
        pressed,_=self.watch([(True,[4],modifiers|{0x34}),
                              (True,[2],modifiers|{0x32}),
                              (True,[],set()),
                              (True,[2],modifiers|{0x32})],echo_frames={1})
        self.assertEqual(pressed,[4,2])

    def test_user_can_stop_after_mode_without_releasing_ctrl(self):
        pressed,_=self.watch([(True,[4],{0x11,0x34}),
                              (True,[2],{0x11,0x32}),
                              (True,[],set())])
        self.assertEqual(pressed,[4,2])

    def test_mode_is_immediate_before_focus_changes(self):
        modifiers={0x11}
        pressed,_=self.watch([(True,[2],modifiers|{0x32}),
                              (True,[],set()),
                              (True,[4],modifiers|{0x34}),
                              (False,[],set())])
        self.assertEqual(pressed,[2,4])

    def test_stop_is_never_suppressed_by_bot_key_activity(self):
        pressed,_=self.watch([(True,[4],{0x11,0x34}),
                              (True,[],set()),(True,[2],set()),
                              (True,[2],{0x11,0x32}),
                              (True,[],set()),(True,[2],{0x11,0x32})],
                              echo_frames={2,3})
        self.assertEqual(pressed,[4,2,2,2])

    def test_stop_message_survives_ctrl_release_before_message_is_read(self):
        pressed,_=self.watch([(True,[2],set())])
        self.assertEqual(pressed,[2])

    def test_registered_stop_also_polls_when_windows_message_is_missing(self):
        pressed,_=self.watch([(True,[],set()),(True,[],{0x11,0x32}),
                              (True,[],{0x11,0x32}),(True,[],set())])
        self.assertEqual(pressed,[2])

    def test_all_ctrl_shortcuts_work_during_matching_bot_skill_activity(self):
        frames=[]
        for number in range(1,6):
            frames.extend([(True,[number],{0x11,0x30+number}),
                           (True,[],set())])
        pressed,_=self.watch(frames,echo_frames=range(len(frames)),echo_numbers=range(1,6))
        self.assertEqual(pressed,[1,2,3,4,5])

    def test_all_registered_shortcuts_poll_without_windows_messages(self):
        frames=[(True,[],set())]
        for number in range(1,6):
            frames.extend([(True,[],{0x11,0x30+number}),
                           (True,[],{0x11,0x30+number}),
                           (True,[],set())])
        pressed,_=self.watch(frames)
        self.assertEqual(pressed,[1,2,3,4,5])

    def test_registered_message_and_polling_do_not_duplicate_mode(self):
        pressed,_=self.watch([(True,[],set()),(True,[3],{0x11,0x33}),
                              (True,[],{0x11,0x33}),(True,[],set())])
        self.assertEqual(pressed,[3])

    def test_all_modes_execute_without_waiting_for_ctrl_release(self):
        for number in (1,3,4,5):
            with self.subTest(number=number):
                pressed,_=self.watch([(True,[],set()),
                                      (True,[number],{0x11,0x30+number}),
                                      (True,[],{0x11,0x30+number})])
                self.assertEqual(pressed,[number])

    def test_every_shortcut_ignores_all_following_numbers_in_same_ctrl_hold(self):
        for first in range(1,6):
            with self.subTest(first=first):
                frames=[(True,[],set()),(True,[first],{0x11,0x30+first})]
                frames.extend((True,[number],{0x11,0x30+number}) for number in range(1,6))
                frames.extend([(True,[],set()),(True,[2],{0x11,0x32})])
                pressed,_=self.watch(frames,echo_frames=range(2,7),echo_numbers=range(1,6))
                self.assertEqual(pressed,[first,2])

    def test_ctrl_four_then_five_runs_immediately_in_same_ctrl_hold(self):
        pressed,_=self.watch([(True,[],set()),(True,[4],{0x11,0x34}),
                              (True,[],{0x11}),(True,[5],{0x11,0x35}),
                              (True,[2],{0x11,0x32})],echo_frames={4})
        self.assertEqual(pressed,[4,5])

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
