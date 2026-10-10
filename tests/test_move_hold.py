import time
import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.main_agent import MainAgent
from app.controller.esp32_input_controller import ESP32InputController
from app.core.action_command import ActionCommand
from app.core.action_executor import ActionExecutor
from app.core.action_scheduler import ActionScheduler


class MoveHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_mouse_watch_releases_esp32_without_waiting_for_event_loop(self):
        import threading
        from unittest.mock import Mock
        controller=self.controller();controller.attack_held=True
        released=threading.Event();paused=threading.Event()
        controller.transport=SimpleNamespace(request=Mock(side_effect=lambda *args,**kwargs:released.set()))
        controller.start_manual_mouse_watch(lambda:True,paused.set)
        try:
            # Deliberately block the asyncio thread, as synchronous targeting can.
            self.assertTrue(released.wait(.5))
            self.assertTrue(paused.is_set())
            self.assertFalse(controller.attack_held)
            self.assertEqual(controller._epoch,1)
            controller.transport.request.assert_called_once_with('RELEASE')
        finally:
            controller._manual_watch_stop.set()
            controller._manual_watch_thread.join(.5)

    async def test_manual_pause_guard_rejects_hold_before_serial_write(self):
        from unittest.mock import Mock
        controller=self.controller();controller.manual_pause_check=lambda:True
        def request(op,*args,guard=None):
            self.assertFalse(guard())
            raise ConnectionError('manual mouse pause')
        controller.transport=SimpleNamespace(request=Mock(side_effect=request))
        with self.assertRaises(ConnectionError):
            await ESP32InputController._send(controller,'HOLD',2,1000)

    async def test_skill_is_blocked_if_validation_changes_during_cursor_move(self):
        controller=self.controller();valid=[True]
        controller.hold_validator=lambda _:valid[0]
        controller.settings_provider=lambda:{'tap_ms':30,'attack_skills':[{'id':'one','enabled':True,'key':'2'}]}
        async def point(*args,**kwargs):
            valid[0]=False
            return True
        controller._point.side_effect=point
        skill=ActionCommand('USE_SKILL',40,time.monotonic(),source='MANUAL_SKILL',skill_id='one',target=(.7,.4))
        self.assertFalse(await controller._action('USE_SKILL',skill))
        controller._send.assert_not_awaited()

    async def test_mode_hold_stays_down_without_target_until_stop(self):
        controller=self.controller();executor=ActionExecutor(controller)
        attack=ActionCommand('ATTACK',50,time.monotonic(),source='STATIONARY_HUNT',
                             reason='STATIONARY_MODE_HOLD',maintain_attack=True)
        try:
            self.assertTrue(await executor.execute(attack))
            controller._point.assert_not_awaited()
            await asyncio.sleep(.65)
            await executor.check_held_attack()
            self.assertTrue(controller.attack_held)
            self.assertGreaterEqual(controller._send.await_count,3)
            self.assertTrue(all(c.args==('HOLD',2,1000) for c in controller._send.await_args_list))
            self.assertTrue(await executor.execute(ActionCommand('STOP',95,time.monotonic())))
            self.assertFalse(controller.attack_held)
            controller._send.assert_awaited_with('STOP')
        finally:
            await executor.release_held_attack()

    async def test_stationary_hold_survives_cursor_delay_and_repeated_updates(self):
        controller=self.controller();executor=ActionExecutor(controller)
        clock=[time.monotonic()]
        async def point(*args,**kwargs):
            clock[0]+=.3
            return True
        controller._point.side_effect=point
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            try:
                for _ in range(2):
                    attack=ActionCommand('ATTACK',50,clock[0],expires_at=clock[0]+.1,
                                         reason='STATIONARY_HOLD',target=(.7,.4),maintain_attack=True)
                    self.assertTrue(await executor.execute(attack))
                    await executor.check_held_attack()
                    self.assertTrue(controller.attack_held)
                self.assertEqual([c.args for c in controller._send.await_args_list],
                                 [('HOLD',2,1000),('HOLD',2,1000)])
            finally:
                await executor.release_held_attack()

    async def test_stop_does_not_wait_for_cancelled_action_cleanup(self):
        controller=self.controller();executor=ActionExecutor(controller)
        scheduler=ActionScheduler(executor);scheduler._running=True
        started=asyncio.Event();cleanup=asyncio.Event();release=asyncio.Event()
        async def old_action():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()
        task=asyncio.create_task(old_action())
        scheduler._execution_task=task
        await started.wait()
        try:
            await asyncio.wait_for(scheduler.submit_emergency(
                ActionCommand('STOP',95,time.monotonic())),timeout=.2)
            await cleanup.wait()
            controller._send.assert_awaited_once_with('STOP')
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)

    async def test_stationary_attack_targets_fast_and_holds_right_without_taps(self):
        controller=self.controller();executor=ActionExecutor(controller)
        attack=ActionCommand('ATTACK',50,time.monotonic(),expires_at=time.monotonic()+.5,
                             source='MOVEMENT_HUNT',reason='STATIONARY_HOLD',
                             target=(.7,.4),maintain_attack=True)
        try:
            self.assertTrue(await executor.execute(attack))
            self.assertTrue(controller.attack_held)
            controller._point.assert_awaited_once_with((.7,.4),0,fast=True)
            controller._send.assert_awaited_once_with('HOLD',2,1000)
        finally:
            await executor.release_held_attack()
        self.assertFalse(controller.attack_held)

    async def test_stop_is_sent_even_if_scheduler_is_idle_and_cleanup_fails(self):
        controller=self.controller();executor=ActionExecutor(controller)
        scheduler=ActionScheduler(executor)
        scheduler._cancel_execution=AsyncMock(side_effect=ConnectionError('RELEASE failed'))
        await scheduler.submit_emergency(ActionCommand('STOP',95,time.monotonic()))
        controller._send.assert_awaited_once_with('STOP')

    async def test_stop_survives_slow_cancellation_and_bypasses_input_validation(self):
        controller=self.controller();executor=ActionExecutor(controller)
        executor.validator=lambda _:False
        scheduler=ActionScheduler(executor);scheduler._running=True
        async def cancel():
            await asyncio.sleep(.02)
        scheduler._cancel_execution=AsyncMock(side_effect=cancel)
        stop=ActionCommand('STOP',95,time.monotonic(),expires_at=time.monotonic()+.001)
        await scheduler.submit_emergency(stop)
        controller._send.assert_awaited_once_with('STOP')
        self.assertEqual(scheduler.queue_size,0)
        self.assertIsNone(executor.last_command.expires_at)

    async def test_stop_sends_one_release_all_even_when_release_helpers_fail(self):
        controller=self.controller()
        controller.move_held=True;controller.attack_held=True
        controller.release_move=AsyncMock(side_effect=ConnectionError('RELEASE failed'))
        controller.release_attack=AsyncMock(side_effect=ConnectionError('RELEASE failed'))
        executor=ActionExecutor(controller)
        self.assertTrue(await executor.execute(ActionCommand('STOP',95,time.monotonic())))
        controller._send.assert_awaited_once_with('STOP')
        self.assertFalse(controller.move_held)
        self.assertFalse(controller.attack_held)

    def controller(self):
        controller=ESP32InputController.__new__(ESP32InputController)
        controller.capture=SimpleNamespace(can_input=lambda:True)
        controller._epoch=0;controller._hold_capable=True;controller._move_hold_capable=True
        controller.attack_held=False;controller._hold_task=None
        controller._send=AsyncMock();controller._point=AsyncMock(return_value=True)
        return controller

    def command(self):
        return ActionCommand('MOVE',30,time.monotonic(),target=(.6,.4),direction=(0.,-1.),
                             expires_at=time.monotonic()+.35,maintain_move=True)

    async def test_esp32_holds_left_button_and_updates_cursor_without_click_taps(self):
        controller=self.controller()
        self.assertTrue(await controller.move(self.command()))
        self.assertTrue(await controller.move(self.command()))
        self.assertEqual([call.args for call in controller._send.await_args_list],
                         [('HOLD',1,600),('HOLD',1,600)])
        await controller.release_move()
        controller._send.assert_awaited_with('RELEASE')
        self.assertFalse(controller.move_held)

    async def test_old_firmware_does_not_receive_unsupported_left_hold(self):
        controller=self.controller();controller._move_hold_capable=False
        executor=ActionExecutor(controller)
        for _ in range(2):
            self.assertFalse(await executor.execute(self.command()))
        controller._send.assert_not_awaited()
        controller._point.assert_not_awaited()
        self.assertIn('1.2',executor.last_error)

    async def test_executor_releases_hold_on_focus_loss_and_expiry(self):
        for expired in (False,True):
            controller=self.controller();executor=ActionExecutor(controller)
            valid=[True];executor.validator=lambda _:valid[0]
            self.assertTrue(await executor.execute(self.command()))
            if expired:executor._held_move_command=replace(executor._held_move_command,expires_at=time.monotonic()-1)
            else:valid[0]=False
            await executor.check_held_attack()
            self.assertFalse(controller.move_held)
            self.assertIsNone(executor._held_move_command)

    async def test_failed_pointer_update_releases_left_button(self):
        controller=self.controller()
        self.assertTrue(await controller.move(self.command()))
        controller._point.return_value=False
        self.assertFalse(await controller.move(self.command()))
        self.assertFalse(controller.move_held)
        controller._send.assert_awaited_with('RELEASE')

    async def test_stop_releases_button(self):
        controller=self.controller();executor=ActionExecutor(controller)
        self.assertTrue(await executor.execute(self.command()))
        self.assertTrue(await executor.execute(ActionCommand('STOP',95,time.monotonic())))
        self.assertFalse(controller.move_held)
        controller._send.assert_awaited_with('STOP')

    async def test_keyboard_skill_preserves_left_hold_and_ends_only_key_tap(self):
        controller=self.controller();executor=ActionExecutor(controller)
        controller.settings_provider=lambda:{'tap_ms':30}
        controller._wait=AsyncMock(return_value=True)
        controller.use_skill=lambda c:controller._tap('1')
        self.assertTrue(await executor.execute(self.command()))
        controller._send.reset_mock()
        skill=ActionCommand('USE_SKILL',40,time.monotonic(),source='MANUAL_SKILL')
        self.assertTrue(await executor.execute(skill))
        self.assertTrue(controller.move_held)
        self.assertEqual([call.args for call in controller._send.await_args_list],
                         [('KEY',30,30,0),('END_TAP',)])

    async def test_mouse_skill_restores_movement_hold_after_tap(self):
        controller=self.controller();executor=ActionExecutor(controller)
        controller.settings_provider=lambda:{'tap_ms':30}
        controller._wait=AsyncMock(return_value=True)
        controller.use_skill=lambda c:controller._tap('mouse_right')
        self.assertTrue(await executor.execute(self.command()))
        controller._send.reset_mock()
        skill=ActionCommand('USE_SKILL',40,time.monotonic(),source='MANUAL_SKILL')
        self.assertTrue(await executor.execute(skill))
        self.assertTrue(controller.move_held)
        self.assertEqual([call.args for call in controller._send.await_args_list],
                         [('RELEASE',),('CLICK',30,2),('STOP',),('HOLD',1,600)])

    async def test_new_movement_cannot_replace_queued_repeat_skill(self):
        executor=SimpleNamespace(active_command=None,renew_held_attack=lambda _:None)
        scheduler=ActionScheduler(executor);scheduler._running=True
        skill=ActionCommand('USE_SKILL',40,time.monotonic(),source='MANUAL_SKILL')
        await scheduler.submit(skill)
        await scheduler.submit(self.command())
        self.assertEqual(scheduler.queue_size,1)
        _,queued=await scheduler._queue.get()
        self.assertIs(queued,skill)
        scheduler._queue.task_done()

    async def test_pending_repeat_skill_does_not_pause_arrow_tracking(self):
        agent=MainAgent.__new__(MainAgent);agent._move_only=True
        agent._manual_skill_pending_at=time.monotonic()
        self.assertFalse(await agent._manual_skill_tick())

    async def test_slow_ble_cursor_rechecks_current_state_before_hold(self):
        controller=self.controller();executor=ActionExecutor(controller)
        valid=[True];executor.validator=lambda _:valid[0]
        clock=[time.monotonic()];cmd=self.command()
        async def point(*args,**kwargs):
            clock[0]=cmd.expires_at+.1
            return True
        controller._point.side_effect=point
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            self.assertTrue(await executor.execute(cmd))
            self.assertFalse(executor._held_move_command.is_expired())
            await executor.check_held_attack()
            self.assertTrue(controller.move_held)
            valid[0]=False
            self.assertFalse(await controller.move(cmd))
            self.assertFalse(controller.move_held)


class ArrowStabilityTests(unittest.TestCase):
    def test_cursor_is_100_pixels_from_arrow_and_rotated_5_degrees_on_both_sides(self):
        import numpy as np
        agent=MainAgent.__new__(MainAgent);agent._epoch=1
        agent._latest_frame=np.zeros((600,960,3),np.uint8)
        agent._world_player_origin=lambda:(.5,.5)
        agent._arrow_hold_distance=80
        for side in (-1,1):
            distances=[]
            for degrees in (30,15,0):
                angle=np.deg2rad(degrees)
                heading=np.array([side*np.cos(angle),-np.sin(angle)])
                marker=np.array([.5,.5])+heading*100/[960,600]
                move=agent._arrow_move_command({'marker':tuple(marker)})
                offset=(np.array(move.target)-marker)*[960,600]
                distances.append(np.linalg.norm(offset))
                self.assertAlmostEqual(np.degrees(np.arccos(np.clip(offset@heading/100,-1,1))),5)
                if degrees==0:
                    self.assertGreater(offset[1],0)
                    self.assertAlmostEqual(np.degrees(np.arctan2(offset[1],abs(offset[0]))),5)
            np.testing.assert_allclose(distances,[100,100,100])

    def test_passed_arrow_keeps_previous_heading_and_correction_expires(self):
        import numpy as np
        agent=MainAgent.__new__(MainAgent);agent._epoch=1
        agent._latest_frame=np.zeros((600,960,3),np.uint8)
        agent._world_player_origin=lambda:(.5,.5)
        agent._arrow_hold_distance=75
        with patch('app.ai.main_agent.time.monotonic',return_value=10):
            agent._stabilize_arrow({'marker':(.5,.3)})
            rear=agent._stabilize_arrow({'marker':(.5,.7)})
            move=agent._arrow_move_command(rear)
            self.assertTrue(rear['planned_recovery'])
            self.assertLess(move.target[1],.7)
            self.assertAlmostEqual(np.linalg.norm((np.array(move.target)-[.5,.7])*[960,600]),100)
        with patch('app.ai.main_agent.time.monotonic',return_value=10.7):
            self.assertIsNone(agent._stabilize_arrow({'marker':(.5,.7)}))
            forward=agent._stabilize_arrow({'marker':(.5,.3)})
            self.assertIsNotNone(forward)
            self.assertFalse(forward.get('planned_recovery',False))

    def test_shape_direction_does_not_change_position_based_movement(self):
        import numpy as np
        agent=MainAgent.__new__(MainAgent);agent._epoch=1
        agent._latest_frame=np.zeros((600,960,3),np.uint8)
        agent._world_player_origin=lambda:(.5,.5)
        agent._arrow_hold_distance=75
        results=[]
        for orientation in ((0.,1.),(0.,-1.),(-1.,0.),(1.,0.)):
            guide=agent._stabilize_arrow({'marker':(.7,.3),'arrow_direction':orientation})
            results.append(agent._arrow_move_command(guide).target)
        for result in results:np.testing.assert_allclose(result,results[0])
        expected=np.array([.2*960,-.2*600]);expected/=np.linalg.norm(expected)
        theta=np.deg2rad(5)
        expected=np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])@expected
        np.testing.assert_allclose((np.array(results[0])-[.7,.3])*[960,600],expected*100)

    def test_cursor_offset_does_not_randomly_change_angle_or_distance(self):
        agent=MainAgent.__new__(MainAgent);agent._epoch=1
        import numpy as np
        agent._latest_frame=np.zeros((600,960,3),np.uint8)
        agent._world_player_origin=lambda:(.5,.5)
        guide={'arrow_tip':(.6,.4),'arrow_direction':(0.,-1.)}
        with patch('app.ai.main_agent.random.uniform',return_value=75) as random:
            first=agent._arrow_move_command(guide);second=agent._arrow_move_command(guide)
        self.assertEqual(first.target,second.target)
        self.assertTrue(first.maintain_move)
        random.assert_not_called()
