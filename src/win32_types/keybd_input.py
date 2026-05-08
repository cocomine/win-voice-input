import ctypes
from ctypes import wintypes

from win32_types.aliases import ULONG_PTR


class KEYBDINPUT(ctypes.Structure):
    # SendInput requires the same memory layout as the Win32 INPUT union.
    # The mouse and hardware branches are modeled in sibling files because
    # Windows validates the full INPUT size before accepting simulated keys.
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]
