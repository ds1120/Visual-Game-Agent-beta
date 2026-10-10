"""Read the destination notification with the installed Windows Korean OCR."""
import asyncio
import re
import cv2
import numpy as np
from app.vision.game_viewport import game_viewport


def is_arrival_text(text):
    return '목적지에도착했습니다' in re.sub(r'\s+', '', text or '')


async def _read(image):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    from winrt.windows.graphics.imaging import BitmapDecoder
    engine=OcrEngine.try_create_from_language(Language('ko'))
    if engine is None:raise RuntimeError('Windows 한국어 OCR이 설치되어 있지 않습니다.')
    _,encoded=cv2.imencode('.png',image,[cv2.IMWRITE_PNG_COMPRESSION,0])
    stream=InMemoryRandomAccessStream()
    writer=DataWriter(stream)
    bitmap=None
    try:
        writer.write_bytes(encoded.tobytes())
        await writer.store_async()
        writer.detach_stream();stream.seek(0)
        decoder=await BitmapDecoder.create_async(stream)
        bitmap=await decoder.get_software_bitmap_async()
        result=await engine.recognize_async(bitmap)
        return result.text
    finally:
        if bitmap is not None:bitmap.close()
        writer.close();stream.close()


def arrival_notice(frame):
    left,top,right,bottom=game_viewport(frame)
    height=bottom-top;width=right-left
    # Notifications occupy the upper/lower screen bands. Preserve native text
    # pixels, remove black bars and omit side panels/chat rather than shrinking.
    x1=left+int(width*.1);x2=left+int(width*.9)
    image=np.concatenate((frame[top:top+int(height*.25),x1:x2],
                          frame[bottom-int(height*.3):bottom,x1:x2]),axis=0)
    text=asyncio.run(_read(image))
    return is_arrival_text(text)
