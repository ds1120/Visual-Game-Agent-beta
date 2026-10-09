import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from app.core.action_command import ActionCommand
from app.core.action_executor import ActionExecutor
from app.core.action_scheduler import ActionScheduler


class MoveBurstTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller=AsyncMock()
        self.controller.move.return_value=True
        self.executor=ActionExecutor(self.controller)
        self.command=ActionCommand('MOVE',10,time.monotonic(),
            direction=(1.,0.),target=(.7,.5),duration_ms=0,move_clicks=3)

    async def test_three_actual_move_calls_and_three_sent_events(self):
        self.assertTrue(await self.executor.execute(self.command))
        self.assertEqual(self.controller.move.await_count,3)
        self.assertEqual([e['status'] for e in self.executor.input_events],['sent']*3)

    async def test_map_guard_stops_remaining_clicks(self):
        self.executor.validator=lambda c:self.controller.move.await_count==0
        self.assertTrue(await self.executor.execute(self.command))
        self.assertEqual(self.controller.move.await_count,1)
        self.assertEqual(len(self.executor.input_events),1)

    async def test_failed_click_is_not_repeated(self):
        self.controller.move.return_value=False
        self.assertFalse(await self.executor.execute(self.command))
        self.assertEqual(self.controller.move.await_count,1)

    async def test_stop_cancels_burst_before_next_click(self):
        scheduler=ActionScheduler(self.executor)
        await scheduler.start()
        try:
            await scheduler.submit(self.command)
            for _ in range(100):
                if self.controller.move.await_count:break
                await asyncio.sleep(.001)
            await scheduler.submit(ActionCommand('STOP',100,time.monotonic()))
            await asyncio.sleep(.08)
            self.assertEqual(self.controller.move.await_count,1)
            self.assertEqual(self.controller.stop.await_count,1)
        finally:
            await scheduler.stop()
