import ctypes
from ctypes import wintypes


class HARDWAREINPUT(ctypes.Structure):
    # Hardware input is not emitted by this app, but it must exist inside
    # INPUT_UNION so ctypes.sizeof(INPUT) matches the Win32 INPUT structure that
    # SendInput validates before accepting keyboard events.
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]
