import ctypes
import time
from ctypes import wintypes


VK_BACK = 0x08
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    # SendInput requires the same memory layout as the Win32 INPUT union.
    # The mouse and hardware branches below are included even though dictation
    # only sends keyboard events, because Windows validates the full structure
    # size before accepting any simulated key press.
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    # The mouse and hardware structures are not used directly, but they make the
    # INPUT union match Win32's real size. Removing them caused SendInput to fail
    # with WinError 87 on 64-bit Windows.
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    # Windows validates this full INPUT layout, not just the KEYBDINPUT branch.
    # On 64-bit Windows ctypes.sizeof(INPUT) should be 40 bytes.
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


class WindowsTextOutput:
    # This class owns all Windows clipboard and simulated-keyboard side effects.
    # Keeping those calls out of the STT session makes it clear exactly where the
    # app can alter the user's active window or clipboard.
    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        # ctypes does not know Win32 function signatures by default. Defining
        # argtypes/restype here prevents accidental truncation of handles and
        # makes Windows API failures surface as real errors instead of corrupt
        # clipboard or keyboard state.
        self._user32.OpenClipboard.argtypes = [wintypes.HWND]
        self._user32.OpenClipboard.restype = wintypes.BOOL
        self._user32.EmptyClipboard.argtypes = []
        self._user32.EmptyClipboard.restype = wintypes.BOOL
        self._user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self._user32.SetClipboardData.restype = wintypes.HANDLE
        self._user32.CloseClipboard.argtypes = []
        self._user32.CloseClipboard.restype = wintypes.BOOL
        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT
        self._kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        self._kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalUnlock.restype = wintypes.BOOL
        self._kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def paste_text(self, text: str) -> None:
        # Chinese text is written to the clipboard as CF_UNICODETEXT, then
        # pasted with Ctrl+V. This avoids per-character keyboard simulation,
        # which is unreliable for IME and CJK input on Windows.
        encoded = text.encode("utf-16le") + b"\x00\x00"
        # SetClipboardData takes ownership of a movable global-memory handle
        # after success. Until that point this method remains responsible for
        # freeing the handle on every error path.
        handle = self._kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            raise ctypes.WinError()

        locked = self._kernel32.GlobalLock(handle)
        if not locked:
            self._kernel32.GlobalFree(handle)
            raise ctypes.WinError()

        ctypes.memmove(locked, encoded, len(encoded))
        self._kernel32.GlobalUnlock(handle)

        opened = False
        try:
            # Windows exposes the clipboard as a process-wide shared resource.
            # A short bounded retry handles normal contention from other apps
            # without hiding persistent clipboard failures.
            for _ in range(20):
                if self._user32.OpenClipboard(None):
                    opened = True
                    break
                time.sleep(0.025)
            if not opened:
                raise ctypes.WinError()

            if not self._user32.EmptyClipboard():
                raise ctypes.WinError()
            if not self._user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise ctypes.WinError()
            # After SetClipboardData succeeds, Windows owns the memory handle.
            # Setting it to None prevents the finally block from freeing memory
            # that no longer belongs to this process.
            handle = None
        finally:
            if opened:
                self._user32.CloseClipboard()
            if handle:
                self._kernel32.GlobalFree(handle)

        time.sleep(0.08)
        # Ctrl+V is sent as explicit down/up events in one SendInput batch so the
        # target app receives a normal paste shortcut rather than raw characters.
        control_down = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(VK_CONTROL, 0, 0, 0, 0)
            ),
        )
        v_down = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(VK_V, 0, 0, 0, 0)),
        )
        v_up = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(VK_V, 0, KEYEVENTF_KEYUP, 0, 0)),
        )
        control_up = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, 0)
            ),
        )
        events = (INPUT * 4)(control_down, v_down, v_up, control_up)
        if self._user32.SendInput(4, events, ctypes.sizeof(INPUT)) != 4:
            raise ctypes.WinError()

    def press_backspace(self) -> None:
        # Backspace is only used when optional command words are enabled. It is
        # kept here because simulated keyboard events belong to the same Windows
        # side-effect boundary as paste_text().
        down = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(VK_BACK, 0, 0, 0, 0)),
        )
        up = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(VK_BACK, 0, KEYEVENTF_KEYUP, 0, 0)),
        )
        events = (INPUT * 2)(down, up)
        if self._user32.SendInput(2, events, ctypes.sizeof(INPUT)) != 2:
            raise ctypes.WinError()
