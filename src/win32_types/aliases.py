import ctypes
from ctypes import wintypes


# These aliases keep ctypes signatures aligned with Win32 pointer width. Using
# Python int-sized defaults here can truncate callback or SendInput fields on
# 64-bit Windows, producing hard-to-diagnose WinError 87 style failures.
LRESULT = wintypes.LPARAM
UINT_PTR = wintypes.WPARAM
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
