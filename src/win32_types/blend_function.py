import ctypes


class BLENDFUNCTION(ctypes.Structure):
    # AC_SRC_ALPHA tells UpdateLayeredWindow to use the bitmap's per-pixel alpha.
    # This is what keeps the rounded panel and microphone icon smooth.
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]
