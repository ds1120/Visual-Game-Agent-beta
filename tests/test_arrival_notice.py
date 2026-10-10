import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import numpy as np
from app.ai.main_agent import MainAgent
from app.vision.arrival_notice import arrival_notice,is_arrival_text


class ArrivalNoticeTests(unittest.IsolatedAsyncioTestCase):
    def test_only_completed_destination_phrase_matches(self):
        self.assertTrue(is_arrival_text('목적지에 도착했습니다'))
        self.assertTrue(is_arrival_text('목적지에 도착 했습니다.'))
        self.assertFalse(is_arrival_text('목적지로 이동 중입니다'))
        self.assertFalse(is_arrival_text('목적지에'))

    async def test_arrival_stops_movement_but_preserves_repeat_skills(self):
        agent=MainAgent.__new__(MainAgent)
        agent._epoch=2;agent._arrival_notice_epoch=2
        agent._move_only=True;agent._paused=False;agent._processing_halted=False
        agent._manual_skill_mode=True;agent._foreground=lambda:True;agent._fresh=lambda:True
        agent.scheduler=SimpleNamespace(submit_emergency=AsyncMock())
        agent.emit_web_event=Mock();agent.handle_control=AsyncMock()
        task=asyncio.get_running_loop().create_future();task.set_result(True)
        agent._arrival_notice_task=task
        self.assertTrue(await agent._arrival_notice_tick())
        self.assertFalse(agent._move_only)
        self.assertTrue(agent._stationary_skill_mode)
        self.assertTrue(agent._manual_skill_mode)
        self.assertFalse(agent._paused)
        self.assertEqual(agent.scheduler.submit_emergency.await_args.args[0].action_type,'STOP')
        agent.handle_control.assert_not_awaited()

    async def test_movement_alone_uses_existing_stop_path(self):
        agent=MainAgent.__new__(MainAgent)
        agent._epoch=2;agent._arrival_notice_epoch=2
        agent._move_only=True;agent._paused=False;agent._processing_halted=False
        agent._foreground=lambda:True;agent._fresh=lambda:True
        agent.handle_control=AsyncMock()
        task=asyncio.get_running_loop().create_future();task.set_result(True)
        agent._arrival_notice_task=task
        self.assertTrue(await agent._arrival_notice_tick())
        agent.handle_control.assert_awaited_once_with('/stop')

    @unittest.skipUnless(os.name=='nt','Windows Korean OCR')
    async def test_windows_ocr_reads_notice_in_bottom_band_without_blocking_loop(self):
        from PIL import Image,ImageDraw,ImageFont
        frame=Image.new('RGB',(960,600),(25,25,25))
        font=ImageFont.truetype('C:/Windows/Fonts/batang.ttc',22)
        ImageDraw.Draw(frame).text((330,555),'목적지에 도착했습니다',font=font,fill=(230,230,230))
        self.assertTrue(await asyncio.to_thread(arrival_notice,np.asarray(frame)[:,:,::-1].copy()))
