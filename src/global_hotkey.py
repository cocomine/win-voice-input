import ctypes
from collections.abc import Callable
from ctypes import wintypes

from win32_message_types import MSG


HOTKEY_ID_TOGGLE_LISTENING = 1
# Ctrl+Alt+V was chosen for the current tray workflow because it is close to
# Windows paste muscle memory while the extra Alt modifier prevents the app from
# intercepting normal Ctrl+V paste operations in the active application.
HOTKEY_DISPLAY_NAME = "Ctrl+Alt+V"
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_V = 0x56
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

HotkeyCallback = Callable[[], None]


class GlobalHotkeyListener:
    # This listener owns only Win32 hotkey registration and message dispatch.
    # It does not know about dictation state, tray UI, or Google STT; callers
    # decide what a hotkey press means by passing on_toggle.
    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._thread_id: int | None = None

        # ctypes signatures are declared here because this module owns hotkey
        # registration and dispatch. MSG comes from a shared module because
        # GetMessageW argtypes are shared with the listening indicator through
        # ctypes.windll.user32.
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.PostThreadMessageW.restype = wintypes.BOOL
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def run(self, on_toggle: HotkeyCallback) -> None:
        self._thread_id = self._kernel32.GetCurrentThreadId()
        hotkey_registered = False

        try:
            # RegisterHotKey asks Windows to deliver the configured shortcut
            # even when another app has focus. The listener raises immediately
            # if Windows rejects the hotkey, because silently choosing another
            # shortcut would change user control behavior.
            if not self._user32.RegisterHotKey(
                None,
                HOTKEY_ID_TOGGLE_LISTENING,
                MOD_CONTROL | MOD_ALT,
                VK_V,
            ):
                raise ctypes.WinError()
            hotkey_registered = True

            msg = MSG()
            while True:
                # GetMessageW blocks without CPU polling until Windows delivers
                # a hotkey message or stop() posts WM_QUIT to this thread.
                message_result = self._user32.GetMessageW(
                    ctypes.byref(msg), None, 0, 0
                )
                if message_result == -1:
                    raise ctypes.WinError()
                if message_result == 0:
                    break
                if (
                    msg.message == WM_HOTKEY
                    and msg.wParam == HOTKEY_ID_TOGGLE_LISTENING
                ):
                    on_toggle()
        finally:
            if hotkey_registered:
                self._user32.UnregisterHotKey(None, HOTKEY_ID_TOGGLE_LISTENING)
            self._thread_id = None

    def stop(self) -> None:
        if self._thread_id is None:
            return

        # Posting WM_QUIT is the normal Win32 way to unblock GetMessageW from a
        # different thread. This lets the tray app exit without killing the
        # process or leaving the hotkey registered.
        if not self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0):
            raise ctypes.WinError()
