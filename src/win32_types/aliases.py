import ctypes
from ctypes import wintypes


LRESULT = wintypes.LPARAM
UINT_PTR = wintypes.WPARAM
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
