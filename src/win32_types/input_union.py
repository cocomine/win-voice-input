import ctypes

from win32_types.hardware_input import HARDWAREINPUT
from win32_types.keybd_input import KEYBDINPUT
from win32_types.mouse_input import MOUSEINPUT


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]
