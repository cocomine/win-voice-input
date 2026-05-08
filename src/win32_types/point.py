import ctypes
from ctypes import wintypes


class POINT(ctypes.Structure):
    # MSG and several caret APIs embed POINT, so the Win32 layout must match the
    # C structure exactly when Python receives message or screen coordinates.
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
