import ctypes

from win32_types.bitmap_info_header import BITMAPINFOHEADER
from win32_types.rgb_quad import RGBQUAD


class BITMAPINFO(ctypes.Structure):
    # CreateDIBSection receives BITMAPINFO as one contiguous structure: a
    # BITMAPINFOHEADER followed by a color table slot. Even though 32-bit BI_RGB
    # does not use palette colors, the field is part of the Win32 layout.
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]
