import ctypes
from ctypes import wintypes


class BITMAPINFOHEADER(ctypes.Structure):
    # CreateDIBSection receives a BITMAPINFOHEADER describing a 32-bit top-down
    # BGRA bitmap. Pillow renders RGBA and the code converts it before copying.
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]
