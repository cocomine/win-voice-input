import ctypes
from ctypes import wintypes


class RECT(ctypes.Structure):
    # SystemParametersInfoW fills this RECT with the desktop work area. The
    # status window uses that rectangle to stay above the taskbar on any monitor
    # layout that Windows reports as the current work area.
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]
