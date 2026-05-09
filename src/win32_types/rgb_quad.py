import ctypes


class RGBQUAD(ctypes.Structure):
    # Win32 names this structure RGBQUAD, but the byte order in memory is BGR
    # plus a reserved byte. BITMAPINFO includes one entry to match the C layout.
    _fields_ = [
        ("rgbBlue", ctypes.c_byte),
        ("rgbGreen", ctypes.c_byte),
        ("rgbRed", ctypes.c_byte),
        ("rgbReserved", ctypes.c_byte),
    ]
