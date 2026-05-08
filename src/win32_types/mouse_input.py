import ctypes
from ctypes import wintypes

from win32_types.aliases import ULONG_PTR


class MOUSEINPUT(ctypes.Structure):
    # The mouse structure is not used directly, but it makes INPUT_UNION match
    # Win32's real size. Removing it caused SendInput to fail with WinError 87.
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]
