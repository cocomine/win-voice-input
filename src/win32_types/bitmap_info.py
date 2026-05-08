import ctypes

from win32_types.bitmap_info_header import BITMAPINFOHEADER
from win32_types.rgb_quad import RGBQUAD


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]
