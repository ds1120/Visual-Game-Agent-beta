import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.ai.main_agent import MainAgent
from app.ai.visual_agent import VisualAgent
from app.core.fast_policy import command
from app.controller.input_bindings import binding_for_command
import numpy as np


class ControlShortcutsTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_game_focus_does_not_start_hunting(self):
        agent = self.agent()
        agent._focus_was_active = False
        agent._focus_paused = False
        agent._manual_control = False
        self.assertFalse(agent.default_auto_hunt)
        await agent._check_focus_transition()
        agent.handle_control.assert_not_awaited()
        self.assertTrue(agent._paused)
        self.assertFalse(agent._hunt_active)

    def agent(self):
        agent = MainAgent.__new__(MainAgent)
        agent._foreground = lambda: True
        agent._hunt_active = False
        agent._paused = True
        agent._move_only = False
        agent.handle_control = AsyncMock()
        return agent

    async def test_start_stop_and_other_shortcuts(self):
        agent = self.agent()
        await agent._handle_control_shortcut(1)
        agent.handle_control.assert_awaited_with('/hunt')
        agent._hunt_active = True
        agent._paused = False
        await agent._handle_control_shortcut(1)
        agent.handle_control.assert_awaited_with('/hunt')
        await agent._handle_control_shortcut(2)
        agent.handle_control.assert_awaited_with('/stop')
        for number, message in ((3, '이동'), (4, '반복스킬'), (5, '제자리사냥')):
            await agent._handle_control_shortcut(number)
            agent.handle_control.assert_awaited_with(message)

    async def test_shortcuts_ignored_outside_game(self):
        agent = self.agent()
        agent._foreground = lambda: False
        await agent._handle_control_shortcut(1)
        agent.handle_control.assert_not_awaited()

    async def test_stationary_hunt_can_be_stopped_with_stop_shortcut(self):
        agent = self.agent()
        agent._hunt_active = True
        agent._paused = False
        agent._manual_skill_mode = True
        agent._stationary_hunt_mode = True
        await agent._handle_control_shortcut(2)
        agent.handle_control.assert_awaited_with('/stop')

    def test_current_function_status_covers_every_mode(self):
        agent = self.agent()
        agent._processing_halted = False
        self.assertEqual(agent._current_function_status()[0], '사냥중단')
        agent._paused = False
        agent._hunt_active = True
        self.assertEqual(agent._current_function_status()[0], '사냥시작')
        agent._move_only = True
        self.assertEqual(agent._current_function_status()[0], '이동')
        agent._stationary_skill_mode = True
        self.assertEqual(agent._current_function_status()[0], '반복스킬')
        agent._stationary_hunt_mode = True
        self.assertEqual(agent._current_function_status()[0], '제자리사냥')
        agent._foreground = lambda: False
        self.assertEqual(agent._current_function_status()[1], '게임 창 활성화 대기')
        agent._processing_halted = True
        self.assertEqual(agent._current_function_status(), ('사냥중단', '모든 작업 일시정지'))

    def test_stationary_hunt_targets_monster_with_right_click_only(self):
        agent = self.agent()
        agent.profile = SimpleNamespace(name='diablo4')
        agent._hunt_active = True
        agent._paused = False
        agent._movement_hunt_requested = True
        agent._stationary_hunt_mode = True
        agent._repeat_skills_hunting = False
        agent._epoch = 1
        agent._latest_frame = np.zeros((100, 100, 3), np.uint8)
        enemy = SimpleNamespace(track_id=9, bbox=(60, 50, 80, 70),
                                enemy_bar_confirmed=True, relation='hostile')
        agent._confirm_diablo_enemies = Mock(return_value=[enemy])
        agent.combat_feedback = SimpleNamespace(annotate=Mock())
        agent.combat_guard = SimpleNamespace(observe=Mock(), is_blocked=lambda _: False,
                                            permits=lambda _: True, request=lambda *_: True)
        agent._world_player_origin = lambda: (.5, .5)
        agent._docs = {'vision.json': {}, 'input.json': {'attack_skills': [
            {'id': 'one', 'enabled': True, 'cooldown_ms': 1000}], 'bindings': {'ATTACK': '1'}}}
        agent._hunt_attack_rotation = Mock(side_effect=AssertionError('No skill rotation'))
        attack = agent._movement_hunt_command()
        self.assertEqual(attack.action_type, 'ATTACK')
        self.assertEqual(attack.track_id, 9)
        self.assertEqual(attack.target, (.7, .6))
        self.assertFalse(attack.maintain_attack)
        self.assertEqual(binding_for_command(agent._docs['input.json'], attack), 'mouse_right')

    async def test_shortcut_notifications_are_queued_during_ai_work(self):
        agent = self.agent()
        agent._running = True
        async def handle(number):
            if number == 2:agent._running = False
        agent._handle_control_shortcut = AsyncMock(side_effect=handle)
        def watch(stop, foreground, pressed):
            pressed(1)
            pressed(2)
            stop.wait(1)
        with patch('app.ai.visual_agent.watch_control_hotkeys', side_effect=watch):
            await agent._control_shortcuts_loop()
        self.assertEqual([c.args[0] for c in agent._handle_control_shortcut.await_args_list], [1, 2])

    async def test_stationary_hunt_configures_attacks_without_movement(self):
        agent = self.agent()
        agent.handle_control = MainAgent.handle_control.__get__(agent)
        agent.scheduler = SimpleNamespace(submit_emergency=AsyncMock())
        agent.emit_web_event = Mock()
        agent._epoch = 1
        # Isolate the existing hunt-start command; exercise the new mode setup.
        original = agent.handle_control

        async def control(message):
            if message == '/hunt':
                agent._stationary_skill_mode = False
                return True
            return await original(message)

        agent.handle_control = control
        self.assertTrue(await agent.handle_control('제자리 사냥'))
        self.assertTrue(agent._stationary_hunt_mode)
        self.assertFalse(agent._repeat_skills_hunting)
        self.assertFalse(agent._manual_skill_mode)
        self.assertFalse(agent.can_execute(SimpleNamespace(action_type='MOVE')))
        self.assertFalse(agent.can_execute(SimpleNamespace(action_type='DODGE')))
        self.assertFalse(agent.can_execute(SimpleNamespace(action_type='USE_SKILL')))
        agent.scheduler.submit_emergency.assert_awaited_once()

    async def test_stationary_hunt_loop_never_plans_movement(self):
        agent = self.agent()
        agent._running = True
        agent._paused = False
        agent._processing_halted = False
        agent._fresh = lambda: True
        agent._stationary_hunt_mode = True
        attack = object()
        agent._movement_hunt_command = Mock(return_value=attack)
        agent.scheduler = SimpleNamespace(submit=AsyncMock())
        agent._prepare_navigation = Mock(side_effect=AssertionError('Must not move'))

        async def tick(_):agent._running = False

        with patch('app.ai.main_agent.asyncio.sleep', new=tick):
            await agent._movement_test_loop()
        agent.scheduler.submit.assert_awaited_once_with(attack)
        agent._prepare_navigation.assert_not_called()

    async def test_stationary_hunt_does_not_use_skills_without_a_target(self):
        agent = self.agent()
        agent._running = True
        agent._paused = False
        agent._processing_halted = False
        agent._fresh = lambda: True
        agent._stationary_hunt_mode = True
        agent._movement_hunt_command = Mock(return_value=None)
        agent._hunt_attack_rotation = Mock(side_effect=AssertionError('No skills'))
        agent.scheduler = SimpleNamespace(submit=AsyncMock())
        async def tick(_):agent._running = False
        with patch('app.ai.main_agent.asyncio.sleep', new=tick):
            await agent._movement_test_loop()
        agent.scheduler.submit.assert_not_awaited()

    async def test_stop_pauses_all_processing_and_start_resumes(self):
        agent = self.agent()
        agent.handle_control = MainAgent.handle_control.__get__(agent)
        agent._paused = False
        agent._hunt_active = True
        agent._focus_stopping = False
        agent._startup_hud_inflight = False
        agent._epoch = 1
        agent.statistics = SimpleNamespace(suspend=Mock())
        agent.navigation_monitor = SimpleNamespace(reset=Mock())
        agent.combat_guard = SimpleNamespace(reset=Mock())
        agent.click_journey = SimpleNamespace(reset=Mock())
        agent.minimap_memory = SimpleNamespace(resume=Mock())
        agent.scheduler = SimpleNamespace(submit_emergency=AsyncMock(),
            executor=SimpleNamespace(release_held_attack=AsyncMock()))
        agent.emit_web_event = Mock()
        class Client:
            def __init__(self):self.cancelled = False
            def cancel_pending(self):self.cancelled = True
            def resume_requests(self):self.cancelled = False
        agent.vl = agent.chat_vl = Client()
        agent.profile = SimpleNamespace(layout='classic')
        agent._docs = {'hud.json': {'calibration': {'validated': True}}}
        self.assertTrue(await agent.handle_control('사냥 중단'))
        self.assertTrue(agent._processing_halted)
        self.assertTrue(agent._paused)
        self.assertTrue(agent._manual_control)
        self.assertTrue(agent._qwen_stopped)
        self.assertFalse(agent._hunt_active)
        self.assertTrue(agent.vl.cancelled)
        self.assertEqual(agent.scheduler.submit_emergency.await_args.args[0].action_type, 'STOP')
        agent.scheduler.executor.release_held_attack.assert_awaited_once()
        self.assertTrue(await agent.handle_control('사냥 시작'))
        self.assertFalse(agent._processing_halted)
        self.assertFalse(agent._paused)
        self.assertFalse(agent._qwen_stopped)
        self.assertFalse(agent._manual_control)
        self.assertTrue(agent._hunt_active)
        self.assertFalse(agent.vl.cancelled)
        self.assertIsNone(agent._latest_frame)
