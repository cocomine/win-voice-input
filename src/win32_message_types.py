import ctypes
from ctypes import wintypes


class POINT(ctypes.Structure):
    # MSG and several caret APIs embed POINT, so the Win32 layout must match the
    # C structure exactly when Python receives message or screen coordinates.
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    # user32.GetMessageW is shared through ctypes.windll.user32 across modules.
    # Keeping one MSG class prevents one module's argtypes from rejecting a
    # structurally identical but different Python MSG class from another module.
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]
