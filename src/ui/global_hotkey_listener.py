import ctypes
from collections.abc import Callable
from ctypes import wintypes

from win32_types import KBDLLHOOKSTRUCT, LRESULT, MSG


HOTKEY_ID_TOGGLE_LISTENING = 1
# Ctrl+Alt+Space was the original workflow shortcut and has been restored after
# a short Ctrl+Alt+V test build. Keeping the toggle away from Ctrl+V reduces the
# chance of confusing normal paste behavior while dictating into active apps.
HOTKEY_DISPLAY_NAME = "Ctrl+Alt+Space"
PREVIEW_COMMIT_KEY_DISPLAY_NAME = "Enter"
HC_ACTION = 0
WH_KEYBOARD_LL = 13
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_RETURN = 0x0D
VK_SPACE = 0x20
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

HotkeyCallback = Callable[[], None]
PreviewCommitKeyCallback = Callable[[bool], bool]
LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class GlobalHotkeyListener:
    # This listener owns only Win32 hotkey registration and message dispatch.
    # It does not know about dictation state, tray UI, or Google STT; callers
    # decide what a hotkey press means by passing on_toggle.
    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._thread_id: int | None = None
        self._on_preview_commit_key: PreviewCommitKeyCallback | None = None
        self._keyboard_proc = LowLevelKeyboardProc(self._handle_keyboard_event)

        # ctypes signatures are declared here because this module owns hotkey
        # registration, keyboard-hook dispatch, and teardown. MSG and
        # KBDLLHOOKSTRUCT come from shared modules because ctypes.windll.user32
        # stores argtypes process-wide.
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
        self._user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            LowLevelKeyboardProc,
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._user32.SetWindowsHookExW.restype = wintypes.HANDLE
        self._user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._user32.CallNextHookEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.CallNextHookEx.restype = LRESULT
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HANDLE

    def run(
        self,
        on_toggle: HotkeyCallback,
        on_preview_commit_key: PreviewCommitKeyCallback | None = None,
    ) -> None:
        self._thread_id = self._kernel32.GetCurrentThreadId()
        self._on_preview_commit_key = on_preview_commit_key
        hotkey_registered = False
        keyboard_hook = None

        try:
            # RegisterHotKey asks Windows to deliver the configured shortcut
            # even when another app has focus. The listener raises immediately
            # if Windows rejects the hotkey, because silently choosing another
            # shortcut would change user control behavior.
            if not self._user32.RegisterHotKey(
                None,
                HOTKEY_ID_TOGGLE_LISTENING,
                MOD_CONTROL | MOD_ALT,
                VK_SPACE,
            ):
                raise ctypes.WinError()
            hotkey_registered = True

            if self._on_preview_commit_key is not None:
                # The low-level hook is installed for the app lifetime, but its
                # callback only consumes Enter while DictationController reports
                # an active Listening session. Idle key events are passed to
                # Windows unchanged, so normal Enter behavior is not affected.
                keyboard_hook = self._user32.SetWindowsHookExW(
                    WH_KEYBOARD_LL,
                    self._keyboard_proc,
                    self._kernel32.GetModuleHandleW(None),
                    0,
                )
                if not keyboard_hook:
                    raise ctypes.WinError()

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
            if keyboard_hook:
                self._user32.UnhookWindowsHookEx(keyboard_hook)
            if hotkey_registered:
                self._user32.UnregisterHotKey(None, HOTKEY_ID_TOGGLE_LISTENING)
            self._on_preview_commit_key = None
            self._thread_id = None

    def _handle_keyboard_event(
        self,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        if n_code == HC_ACTION:
            keyboard_event = ctypes.cast(
                l_param,
                ctypes.POINTER(KBDLLHOOKSTRUCT),
            ).contents
            is_enter_event = (
                keyboard_event.vkCode == VK_RETURN
                and w_param
                in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
            )
            if is_enter_event and self._on_preview_commit_key is not None:
                should_commit = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self._on_preview_commit_key(should_commit):
                    return 1
        return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

    def stop(self) -> None:
        if self._thread_id is None:
            return

        # Posting WM_QUIT is the normal Win32 way to unblock GetMessageW from a
        # different thread. This lets the tray app exit without killing the
        # process or leaving the hotkey registered.
        if not self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0):
            raise ctypes.WinError()
