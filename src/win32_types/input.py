import ctypes
from ctypes import wintypes

from win32_types.input_union import INPUT_UNION


class INPUT(ctypes.Structure):
    # Windows validates this full INPUT layout, not just the KEYBDINPUT branch.
    # On 64-bit Windows ctypes.sizeof(INPUT) should be 40 bytes.
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]
