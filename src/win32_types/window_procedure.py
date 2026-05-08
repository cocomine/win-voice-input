import ctypes
from ctypes import wintypes

from win32_types.aliases import LRESULT


WindowProcedure = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
