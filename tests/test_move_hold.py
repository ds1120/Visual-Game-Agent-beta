import time
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
                         [('HOLD',1,350),('HOLD',1,350)])
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
                         [('RELEASE',),('CLICK',30,2),('STOP',),('HOLD',1,350)])

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
    def test_passed_arrow_never_reverses_click_and_correction_expires(self):
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
            self.assertLess(move.target[1],.5)
            self.assertAlmostEqual((.5-move.target[1])*600,75)
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
        np.testing.assert_allclose((np.array(results[0])-[.7,.3])*[960,600],expected*75)

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
        random.assert_called_once_with(50,100)
