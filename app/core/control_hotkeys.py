"""Receive Windows hotkeys independently of capture and navigation work."""
import ctypes
import logging
import time
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)
WM_HOTKEY = 0x0312
MODIFIERS = 0x0004 | 0x0002 | 0x4000  # Shift, Ctrl, no auto-repeat
HOTKEY_INTERVAL_SECONDS = 1.0
_bot_key_lock = threading.Lock()
_bot_keys = {}


def begin_bot_keys(hid_keys):
    numbers=[key-29 for key in hid_keys if 30<=key<=34]
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
    pending = None
    last_pressed = {}
    def dispatch(number):
        now=time.monotonic()
        if now-last_pressed.get(number,float('-inf'))<HOTKEY_INTERVAL_SECONDS:return
        last_pressed[number]=now
        pressed(number)
    try:
        for number in range(1, 6):
            if user32.RegisterHotKey(None, number, MODIFIERS, 0x30 + number):
                registered.add(number)
                log.info('[HOTKEY] Ctrl+Shift+%s registered', number)
            else:
                log.warning('[HOTKEY] Ctrl+Shift+%s registration failed (Windows error %s); using key-state fallback',
                            number, getattr(ctypes, 'get_last_error', lambda: 0)())
        message = wintypes.MSG()
        while not stop.is_set():
            active = foreground()
            down = lambda key: bool(user32.GetAsyncKeyState(key) & 0x8000)
            modifiers_down = down(0x11) or down(0x10)
            def accept(number):
                nonlocal chord_locked, pending
                if bot_key_active(number):
                    log.debug('[HOTKEY] ignored Agent keyboard echo: %s',number)
                    return
                # Delayed WM_HOTKEY messages from a skill are not a fresh stop chord.
                if number==2 and not (down(0x11) and down(0x10)):return
                if chord_locked or not foreground():return
                if modifiers_down:
                    chord_locked=True
                    if number!=2:
                        pending=number
                        return
                dispatch(number)
            while user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1):
                if message.wParam in registered and foreground():
                    accept(int(message.wParam))
            # Dedicated polling also keeps unavailable hotkeys off the busy AI loop.
            held = ({n for n in range(1, 6) if n not in registered and down(0x30 + n)}
                    if down(0x11) and down(0x10) else set())
            if active and was_foreground:
                for number in sorted(held - previous):
                    accept(number)
            if not modifiers_down:
                if pending is not None and foreground():dispatch(pending)
                pending=None
                chord_locked=False
            previous, was_foreground = held, active
            stop.wait(.01)
    finally:
        for number in registered:
            user32.UnregisterHotKey(None, number)
