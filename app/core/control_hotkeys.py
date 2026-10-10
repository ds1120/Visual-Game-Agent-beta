"""Receive Windows hotkeys independently of capture and navigation work."""
import ctypes
import logging
import time
import threading
from ctypes import wintypes
from app.core.physical_mouse import physical_mouse

log = logging.getLogger(__name__)
WM_HOTKEY = 0x0312
MODIFIERS = 0x0002 | 0x4000  # Ctrl, no auto-repeat
HOTKEY_INTERVAL_SECONDS = 1.0
_bot_key_lock = threading.Lock()
_bot_keys = {}
_mouse_user32 = None


def mouse_left_down():
    global _mouse_user32
    if _mouse_user32 is None:
        _mouse_user32=ctypes.WinDLL('user32',use_last_error=True)
        physical_mouse.start()
    return physical_mouse.down(lambda:bool(_mouse_user32.GetAsyncKeyState(0x01)&0x8000))


def begin_bot_keys(hid_keys):
    numbers=[key-29 if 30<=key<=34 else 0 for key in hid_keys if 30<=key<=34 or key==43]
    with _bot_key_lock:
        for number in numbers:
            count,until=_bot_keys.get(number,(0,0))
            _bot_keys[number]=(count+1,until)
    return numbers


def end_bot_keys(numbers, duration_ms):
    with _bot_key_lock:
        for number in numbers:
            count,until=_bot_keys.get(number,(0,0))
            _bot_keys[number]=(max(0,count-1),max(until,time.monotonic()+duration_ms/1000+.15))


def bot_key_active(number):
    with _bot_key_lock:
        count,until=_bot_keys.get(number,(0,0))
        return count>0 or time.monotonic()<until


def watch_control_hotkeys(stop, foreground, pressed, user32=None):
    if user32 is None:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
    registered = set()
    previous = set()
    was_foreground = False
    chord_locked = False
    last_pressed = {}
    tab_was_down=False
    middle_was_down=False
    def dispatch(number,mouse=False):
        now=time.monotonic()
        if not mouse and now-last_pressed.get(number,float('-inf'))<HOTKEY_INTERVAL_SECONDS:return
        if not mouse:last_pressed[number]=now
        log.debug('[HOTKEY] %s received', 'Mouse wheel' if mouse else 'Tab' if number==0 else f'Ctrl+{number}')
        pressed(number)
    try:
        for number in range(1, 8):
            if user32.RegisterHotKey(None, number, MODIFIERS, 0x30 + number):
                registered.add(number)
                log.debug('[HOTKEY] Ctrl+%s registered', number)
            else:
                log.debug('[HOTKEY] Ctrl+%s registration failed (Windows error %s); using key-state fallback',
                            number, getattr(ctypes, 'get_last_error', lambda: 0)())
        message = wintypes.MSG()
        while not stop.is_set():
            active = foreground()
            down = lambda key: bool(user32.GetAsyncKeyState(key) & 0x8000)
            modifiers_down = down(0x11)
            tab_down=down(0x09)
            if active and tab_down and not tab_was_down and not bot_key_active(0):
                dispatch(0)
            tab_was_down=tab_down
            middle_down=down(0x04)
            if active and middle_down and not middle_was_down:
                dispatch(6,mouse=True)
            middle_was_down=middle_down
            def accept(number):
                nonlocal chord_locked
                # Accept the first shortcut immediately. While Ctrl stays down,
                # allow other shortcuts but suppress matching active bot skills.
                if not foreground():return
                if chord_locked and bot_key_active(number):return
                if modifiers_down:
                    chord_locked=True
                dispatch(number)
            while user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1):
                if message.wParam in registered and foreground():
                    accept(int(message.wParam))
            # Dedicated polling also keeps unavailable hotkeys off the busy AI loop.
            held = ({n for n in range(1, 8) if down(0x30 + n)}
                    if down(0x11) and not down(0x10) and not down(0x12) else set())
            if active and was_foreground:
                for number in sorted(held - previous):
                    accept(number)
            if not modifiers_down:
                chord_locked=False
            previous, was_foreground = held, active
            stop.wait(.01)
    finally:
        for number in registered:
            user32.UnregisterHotKey(None, number)
