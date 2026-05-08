import ctypes
from ctypes import wintypes


class SIZE(ctypes.Structure):
    # UpdateLayeredWindow needs the bitmap size as a Win32 SIZE structure.
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]
