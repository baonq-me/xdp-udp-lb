import ctypes


class Backend(ctypes.Structure):
    _fields_ = [
        ("ip", ctypes.c_uint32),
        ("port", ctypes.c_uint16),
        ("pad", ctypes.c_uint16),
        ("mac", ctypes.c_ubyte * 6),
    ]

class MacAddr(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_ubyte * 6)]
