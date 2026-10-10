"""Track left-button holds per raw mouse device, independently of AI workers."""
import ctypes
from ctypes import wintypes
import logging
import threading
import time

log = logging.getLogger(__name__)

class PhysicalMouse:
    def __init__(self):
        self.lock=threading.Lock()
        self.held=set()
        self.observed=False
        self.ready=threading.Event()
        self.error=None
        self.thread=None
        self.native_down_seen=False
        self.up_since=None
        self.press_at=None
        self.native_warning=False
        self.agent_devices=set()

    def identify(self,device,name):
        name=name.upper()
        # Firmware BLE PnP identity: vendor 303A, product 4001.
        agent=('303A' in name and '4001' in name) or 'VISUALAGENT-ESP32' in name
        with self.lock:
            if agent:
                self.agent_devices.add(device)
                self.held.discard(device)
        log.debug('[MOUSE RAW] device=%s agent=%s name=%s',device,agent,name)

    def update(self,device,flags):
        with self.lock:
            if flags&3:self.observed=True
            if device in self.agent_devices:return
            if flags&1:
                self.held.add(device)
                self.up_since=None
                self.press_at=time.monotonic()
                self.native_warning=False
            if flags&2:self.held.discard(device)
        if flags&3:
            log.debug('[MOUSE RAW] 장치=%s 왼버튼=%s',device,'누름' if flags&1 else '해제')

    def reconcile(self,native_down,now):
        recovered=[]
        unavailable=False
        with self.lock:
            if native_down:
                self.native_down_seen=True
                self.up_since=None
            elif self.held and self.native_down_seen:
                if self.up_since is None:self.up_since=now
                elif now-self.up_since>=.2:
                    recovered=list(self.held)
                    self.held.clear()
                    self.native_down_seen=False
                    self.up_since=None
            elif self.held and not self.native_down_seen:
                if self.press_at is not None and now-self.press_at>=1 and not self.native_warning:
                    self.native_warning=True
                    unavailable=True
            elif not self.held:
                self.native_down_seen=False
                self.up_since=None
        for device in recovered:
            log.debug('[MOUSE RAW] 장치=%s 왼버튼=해제 · Windows 상태로 누락 복구',device)
        if unavailable:
            log.debug('[MOUSE RAW] Windows 버튼 누름 상태 미확인 · 게임 내 입력 접근/권한 확인 필요 · 자동 재개 보류')

    def down(self,fallback):
        with self.lock:
            return bool(self.held) if self.observed else fallback()

    def start(self):
        with self.lock:
            if self.thread is None:
                self.thread=threading.Thread(target=self._run,name='physical-mouse',daemon=True)
                self.thread.start()
        self.ready.wait(.1)

    def _run(self):
        u=ctypes.WinDLL('user32',use_last_error=True)
        class Device(ctypes.Structure):
            _fields_=[('page',wintypes.USHORT),('usage',wintypes.USHORT),('flags',wintypes.DWORD),('window',wintypes.HWND)]
        class Header(ctypes.Structure):
            _fields_=[('kind',wintypes.DWORD),('size',wintypes.DWORD),('device',wintypes.HANDLE),('param',wintypes.WPARAM)]
        class Buttons(ctypes.Structure):
            _fields_=[('flags',wintypes.USHORT),('data',wintypes.USHORT)]
        class ButtonUnion(ctypes.Union):
            _fields_=[('buttons',wintypes.ULONG),('split',Buttons)]
        class Mouse(ctypes.Structure):
            _fields_=[('flags',wintypes.USHORT),('buttons',ButtonUnion),('raw',wintypes.ULONG),('x',wintypes.LONG),('y',wintypes.LONG),('extra',wintypes.ULONG)]
        u.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
                                  ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
        u.CreateWindowExW.restype=wintypes.HWND
        u.RegisterRawInputDevices.argtypes=[ctypes.POINTER(Device),wintypes.UINT,wintypes.UINT]
        u.RegisterRawInputDevices.restype=wintypes.BOOL
        u.GetRawInputData.argtypes=[wintypes.HANDLE,wintypes.UINT,ctypes.c_void_p,ctypes.POINTER(wintypes.UINT),wintypes.UINT]
        u.GetRawInputData.restype=wintypes.UINT
        u.GetRawInputDeviceInfoW.argtypes=[wintypes.HANDLE,wintypes.UINT,ctypes.c_void_p,ctypes.POINTER(wintypes.UINT)]
        u.GetRawInputDeviceInfoW.restype=wintypes.UINT
        u.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        u.DispatchMessageW.argtypes=[ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype=ctypes.c_ssize_t
        u.DestroyWindow.argtypes=[wintypes.HWND]
        window=None
        try:
            window=u.CreateWindowExW(0,'STATIC','Agent raw mouse',0,0,0,0,0,wintypes.HWND(-3),None,None,None)
            if not window:raise ctypes.WinError(ctypes.get_last_error())
            device=Device(1,2,0x100|0x2000,window)  # INPUTSINK + DEVNOTIFY
            if not u.RegisterRawInputDevices(ctypes.byref(device),1,ctypes.sizeof(Device)):
                raise ctypes.WinError(ctypes.get_last_error())
            log.debug('[MOUSE RAW] 실제 마우스 장치별 버튼 감시 시작')
            self.ready.set()
            message=wintypes.MSG()
            known_devices=set()
            while True:
                while u.PeekMessageW(ctypes.byref(message),window,0,0,1):
                    if message.message==0xff:
                        size=wintypes.UINT()
                        handle=wintypes.HANDLE(message.lParam)
                        u.GetRawInputData(handle,0x10000003,None,ctypes.byref(size),ctypes.sizeof(Header))
                        buffer=ctypes.create_string_buffer(size.value)
                        if u.GetRawInputData(handle,0x10000003,buffer,ctypes.byref(size),ctypes.sizeof(Header))!=0xffffffff:
                            header=Header.from_buffer_copy(buffer)
                            if header.kind==0 and size.value>=ctypes.sizeof(Header)+ctypes.sizeof(Mouse):
                                if header.device not in known_devices:
                                    length=wintypes.UINT()
                                    u.GetRawInputDeviceInfoW(header.device,0x20000007,None,ctypes.byref(length))
                                    if length.value:
                                        name=ctypes.create_unicode_buffer(length.value+1)
                                        if u.GetRawInputDeviceInfoW(header.device,0x20000007,name,ctypes.byref(length))!=0xffffffff:
                                            self.identify(header.device,name.value)
                                            known_devices.add(header.device)
                                mouse=Mouse.from_buffer_copy(buffer,ctypes.sizeof(Header))
                                self.update(header.device,mouse.buttons.split.flags)
                    elif message.message==0xfe and message.wParam==2:
                        known_devices.discard(message.lParam)
                        with self.lock:
                            self.held.discard(message.lParam)
                            self.agent_devices.discard(message.lParam)
                    u.DispatchMessageW(ctypes.byref(message))
                self.reconcile(bool(u.GetAsyncKeyState(0x01)&0x8000),time.monotonic())
                threading.Event().wait(.002)
        except Exception as exc:
            self.error=str(exc)
            log.debug('[MOUSE] raw input unavailable: %s',exc)
        finally:
            self.ready.set()
            if window:u.DestroyWindow(window)


physical_mouse=PhysicalMouse()
