import unittest
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.ai.main_agent import MainAgent
from app.ai.visual_agent import VisualAgent
from app.core.fast_policy import command
from app.controller.input_bindings import binding_for_command
from app.vision.qwen_vl_client import QwenVLClient
import numpy as np


class ControlShortcutsTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_hunt_holds_right_and_repeats_skills_without_target_detection(self):
        agent=MainAgent.__new__(MainAgent)
        agent._epoch=1;agent._untargeted_hunt_mode=True
        agent._stationary_manual_move_tick=AsyncMock(return_value=False)
        agent._movement_hunt_command=Mock(side_effect=AssertionError('No monster targeting'))
        agent._manual_skill_tick=AsyncMock(return_value=True)
        executor=SimpleNamespace(_held_attack_command=None,execute=AsyncMock())
        agent.scheduler=SimpleNamespace(executor=executor,submit=AsyncMock())
        await agent._stationary_hunt_tick()
        attack=executor.execute.await_args.args[0]
        self.assertTrue(attack.maintain_attack)
        self.assertIsNone(attack.target)
        self.assertIsNone(attack.track_id)
        agent._manual_skill_tick.assert_awaited_once_with(combat_hold=attack)

    def setUp(self):
        mouse=patch('app.ai.main_agent.mouse_left_down',return_value=False)
        mouse.start()
        self.addCleanup(mouse.stop)
    async def test_pending_combat_skill_renews_attack_without_replacing_queued_skill(self):
        agent=MainAgent.__new__(MainAgent)
        agent._hunt_active=True;agent._move_only=False
        agent._manual_skill_pending_at=10
        attack=command('ATTACK',source='MOVEMENT_HUNT',reason='STATIONARY_HOLD',epoch=1)
        agent.scheduler=SimpleNamespace(submit=AsyncMock(),executor=SimpleNamespace(renew_held_attack=Mock()))
        with patch('app.ai.main_agent.mouse_left_down',return_value=False), patch('app.ai.main_agent.time.monotonic',return_value=10.6):
            self.assertTrue(await agent._manual_skill_tick(combat_hold=attack))
        agent.scheduler.executor.renew_held_attack.assert_called_once_with(attack)
        agent.scheduler.submit.assert_not_awaited()
        self.assertEqual(agent._manual_skill_pending_at,10)

    async def test_release_pause_preserves_modes_and_toggles_from_button_and_wheel(self):
        agent=MainAgent.__new__(MainAgent)
        agent._hunt_active=True;agent._move_only=True;agent._manual_skill_mode=True
        agent._foreground=lambda:True
        controller=SimpleNamespace(release_inputs=AsyncMock(),stop=AsyncMock())
        agent.scheduler=SimpleNamespace(clear=AsyncMock(),_cancel_execution=AsyncMock(),
            executor=SimpleNamespace(_input=controller,release_held_attack=AsyncMock(),release_held_move=AsyncMock()))
        agent.emit_web_event=Mock()
        self.assertTrue(await agent.handle_control('/release-pause'))
        self.assertTrue(agent._release_paused)
        controller.release_inputs.assert_awaited_once()
        for action in ('MOVE','ATTACK','USE_SKILL','CAST_BUFF','USE_POTION','INTERACT'):
            self.assertFalse(agent.can_execute(command(action)))
        await agent._handle_control_shortcut(6)
        self.assertFalse(agent._release_paused)
        self.assertTrue(agent._hunt_active)
        self.assertTrue(agent._move_only)
        self.assertTrue(agent._manual_skill_mode)
        controller.release_inputs.assert_awaited_once()

    def test_qwen_cancel_wakes_reader_without_closing_response_or_holding_lock(self):
        client=QwenVLClient.__new__(QwenVLClient)
        client._cancel_lock=threading.Lock();client._cancel_epoch=0
        client._requests_paused=False
        def shutdown(_):
            self.assertTrue(client._cancel_lock.acquire(blocking=False))
            client._cancel_lock.release()
        connection=Mock();connection._cancel_socket=Mock(shutdown=Mock(side_effect=shutdown))
        client._active_connections={connection}
        client.cancel_pending()
        self.assertTrue(client._requests_paused)
        self.assertEqual(client._cancel_epoch,1)
        connection._cancel_socket.shutdown.assert_called_once()
        connection.close.assert_not_called()

    async def test_hotkey_listener_survives_stop_transport_error(self):
        agent=self.agent();agent._running=True
        attempts=[]
        async def handle(number):
            attempts.append(number)
            if len(attempts)==1:raise ConnectionError('serial failure')
            agent._running=False
        agent._handle_control_shortcut=AsyncMock(side_effect=handle)
        def watch(stop,foreground,pressed):
            pressed(2);pressed(2);stop.wait(1)
        with patch('app.ai.visual_agent.watch_control_hotkeys',side_effect=watch), self.assertLogs('app.ai.visual_agent',level='ERROR'):
            await agent._control_shortcuts_loop()
        self.assertEqual(attempts,[2,2])
    async def test_tab_restores_movement_and_repeat_skill_combination(self):
        agent=self.agent();agent._paused=False;agent._processing_halted=False
        agent._hunt_active=True;agent._move_only=True;agent._manual_skill_mode=True
        agent._screen_guide_enabled=True;agent._planned_move_heading=(1.,0.)
        async def control(message):
            self.assertEqual(message,'/release-pause')
            agent._release_paused=not getattr(agent,'_release_paused',False)
            return True
        agent.handle_control=AsyncMock(side_effect=control)
        await agent._handle_control_shortcut(0)
        self.assertTrue(agent._release_paused)
        self.assertFalse(agent._paused)
        self.assertTrue(agent._manual_skill_mode)
        await agent._handle_control_shortcut(0)
        self.assertFalse(agent._paused)
        self.assertFalse(agent._release_paused)
        self.assertTrue(agent._move_only)
        self.assertTrue(agent._manual_skill_mode)
        self.assertTrue(agent._screen_guide_enabled)
        self.assertEqual(agent._planned_move_heading,(1.,0.))
        self.assertEqual([call.args[0] for call in agent.handle_control.await_args_list],['/release-pause','/release-pause'])

    async def test_tab_ignored_outside_game(self):
        agent=self.agent();agent._foreground=lambda:False
        await agent._handle_control_shortcut(0)
        agent.handle_control.assert_not_awaited()

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
        for number, message in ((3, '이동'), (4, '반복스킬'), (5, '제자리사냥'), (6, '/release-pause'), (7, '/replan')):
            await agent._handle_control_shortcut(number)
            agent.handle_control.assert_awaited_with(message)

    async def test_shortcuts_ignored_outside_game(self):
        agent = self.agent()
        agent._foreground = lambda: False
        await agent._handle_control_shortcut(1)
        agent.handle_control.assert_not_awaited()

    async def test_queued_stop_still_runs_after_game_loses_focus(self):
        agent=self.agent()
        agent._foreground=lambda:False
        await agent._handle_control_shortcut(2)
        agent.handle_control.assert_awaited_once_with('/stop')

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
        self.assertEqual(agent._current_function_status()[0], '대기')
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
        self.assertTrue(attack.maintain_attack)
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
            if message == '/activate-control':
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

    async def test_stationary_and_repeat_modes_combine_in_either_order(self):
        for order in (('반복스킬','제자리사냥'),('제자리사냥','반복스킬')):
            agent=self.agent();agent._epoch=1;agent._move_only=False
            agent.scheduler=SimpleNamespace(submit_emergency=AsyncMock())
            agent.emit_web_event=Mock()
            original=MainAgent.handle_control.__get__(agent)
            async def control(message):
                if message=='/activate-control':
                    agent._paused=False;agent._processing_halted=False
                    agent._hunt_active=True;agent._stationary_skill_mode=False
                    return True
                return await original(message)
            agent.handle_control=control
            for message in order:self.assertTrue(await control(message))
            self.assertTrue(agent._stationary_hunt_mode)
            self.assertTrue(agent._manual_skill_mode)
            self.assertFalse(agent._stationary_skill_mode)

    async def test_stationary_loop_dispatches_registered_repeat_skill(self):
        agent=self.agent();agent._running=True;agent._epoch=1
        agent._paused=False;agent._processing_halted=False
        agent._stationary_hunt_mode=True;agent._manual_skill_mode=True
        agent._fresh=lambda:True
        agent._stationary_manual_move_tick=AsyncMock(return_value=False)
        agent.scheduler=SimpleNamespace(submit=AsyncMock(),_is_cooldown_ready=lambda c:True)
        agent._docs={'input.json':{'attack_skills':[{'id':'one','enabled':True,'cooldown_ms':500}]}}
        async def tick(_):agent._running=False
        with patch('app.ai.main_agent.asyncio.sleep',new=tick):
            await agent._movement_test_loop()
        skill=agent.scheduler.submit.await_args.args[0]
        self.assertEqual(skill.source,'MANUAL_SKILL')
        self.assertEqual(skill.skill_id,'one')

    async def test_normal_action_loop_stops_targets_and_skills_while_left_is_held(self):
        agent=self.agent();agent._running=True;agent._epoch=1
        agent._paused=False;agent._processing_halted=False
        agent._stationary_hunt_mode=True;agent._manual_skill_mode=True
        agent._fresh=lambda:True;agent._foreground=lambda:True
        agent._focus_work_allowed=lambda:True
        agent.scheduler=SimpleNamespace(clear=AsyncMock(),_cancel_execution=AsyncMock(),
                                        executor=SimpleNamespace(release_held_attack=AsyncMock()))
        agent._movement_hunt_target=9
        agent._movement_hunt_command=Mock(side_effect=AssertionError('No targeting while held'))
        agent._hunt_attack_rotation=Mock(side_effect=AssertionError('No skills while held'))
        async def tick(_):agent._running=False
        with patch('app.ai.main_agent.mouse_left_down',return_value=True), \
             patch('app.ai.visual_agent.asyncio.sleep',new=tick):
            await VisualAgent._action_loop(agent)
        self.assertIsNone(agent._movement_hunt_target)
        agent.scheduler.executor.release_held_attack.assert_awaited_once()
        agent._movement_hunt_command.assert_not_called()
        agent._hunt_attack_rotation.assert_not_called()

    async def test_stationary_hunt_loop_never_plans_movement(self):
        agent = self.agent()
        agent._running = True
        agent._paused = False
        agent._processing_halted = False
        agent._fresh = lambda: True
        agent._stationary_hunt_mode = True
        agent._epoch=1
        attack = SimpleNamespace(action_type='ATTACK',target=(.7,.4),track_id=9)
        agent._movement_hunt_command = Mock(return_value=attack)
        agent.scheduler = SimpleNamespace(submit=AsyncMock(),executor=SimpleNamespace(execute=AsyncMock()))
        events=[]
        agent.scheduler.executor.execute.side_effect=lambda _:events.append('hold')
        agent._movement_hunt_command.side_effect=lambda:events.append('detect') or attack
        agent._prepare_navigation = Mock(side_effect=AssertionError('Must not move'))

        async def tick(_):agent._running = False

        with patch('app.ai.main_agent.asyncio.sleep', new=tick):
            await agent._movement_test_loop()
        self.assertEqual(events,['hold','detect'])
        updated=agent.scheduler.submit.await_args.args[0]
        self.assertEqual(updated.target,attack.target)
        self.assertEqual(updated.reason,'STATIONARY_MODE_HOLD')
        self.assertTrue(updated.maintain_attack)
        self.assertIsNone(updated.expires_at)
        agent._prepare_navigation.assert_not_called()

    async def test_manual_left_move_releases_right_once_and_resumes_on_release(self):
        agent=self.agent()
        agent.scheduler=SimpleNamespace(clear=AsyncMock(),_cancel_execution=AsyncMock(),executor=SimpleNamespace(release_held_attack=AsyncMock()))
        with patch('app.ai.main_agent.mouse_left_down',return_value=True), \
             patch('app.ai.main_agent.time.monotonic',return_value=10):
            self.assertTrue(await agent._stationary_manual_move_tick())
            self.assertTrue(await agent._stationary_manual_move_tick())
        agent.scheduler.clear.assert_awaited_once()
        agent.scheduler.executor.release_held_attack.assert_awaited_once()
        with patch('app.ai.main_agent.mouse_left_down',return_value=False), \
             patch('app.ai.main_agent.time.monotonic',return_value=10.1):
            self.assertFalse(await agent._stationary_manual_move_tick())
            self.assertEqual(agent._stationary_manual_move_until,0)
        with patch('app.ai.main_agent.mouse_left_down',return_value=False), \
             patch('app.ai.main_agent.time.monotonic',return_value=10.21):
            self.assertFalse(await agent._stationary_manual_move_tick())

    async def test_stationary_hunt_does_not_use_skills_without_a_target(self):
        agent = self.agent()
        agent._running = True
        agent._paused = False
        agent._processing_halted = False
        agent._fresh = lambda: True
        agent._stationary_hunt_mode = True
        agent._epoch=1
        agent._movement_hunt_command = Mock(return_value=None)
        agent._hunt_attack_rotation = Mock(side_effect=AssertionError('No skills'))
        agent.scheduler = SimpleNamespace(submit=AsyncMock(),executor=SimpleNamespace(execute=AsyncMock(),release_held_attack=AsyncMock()))
        async def tick(_):agent._running = False
        with patch('app.ai.main_agent.asyncio.sleep', new=tick):
            await agent._movement_test_loop()
        agent.scheduler.submit.assert_not_awaited()
        agent.scheduler.executor.execute.assert_awaited_once()
        held=agent.scheduler.executor.execute.await_args.args[0]
        self.assertIsNone(held.target)
        self.assertTrue(held.maintain_attack)
        agent.scheduler.executor.release_held_attack.assert_not_awaited()

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
        # STOP owns the complete release; an earlier serial RELEASE must not
        # delay or prevent the emergency from reaching the scheduler.
        agent.scheduler.executor.release_held_attack.assert_not_awaited()
        self.assertTrue(await agent.handle_control('/activate-control'))
        self.assertFalse(agent._processing_halted)
        self.assertFalse(agent._paused)
        self.assertFalse(agent._qwen_stopped)
        self.assertFalse(agent._manual_control)
        self.assertTrue(agent._hunt_active)
        self.assertFalse(agent.vl.cancelled)
        self.assertIsNone(agent._latest_frame)
