import ctypes

from win32_types.hardware_input import HARDWAREINPUT
from win32_types.keybd_input import KEYBDINPUT
from win32_types.mouse_input import MOUSEINPUT


class INPUT_UNION(ctypes.Union):
    # SendInput receives a union, not only the keyboard branch we currently use.
    # Modeling all branches keeps the memory layout compatible with Windows and
    # avoids parameter errors when simulating Ctrl+V or Backspace.
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]
