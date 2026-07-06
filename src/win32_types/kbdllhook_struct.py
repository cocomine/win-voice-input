import ctypes
from ctypes import wintypes

from win32_types.aliases import ULONG_PTR


class KBDLLHOOKSTRUCT(ctypes.Structure):
    # WH_KEYBOARD_LL passes this structure to the hook callback for every
    # keyboard event. Modeling the native layout keeps vkCode and flags aligned
    # with Windows so the Enter-only capture can distinguish the key without
    # touching normal key handling while dictation is idle.
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]
