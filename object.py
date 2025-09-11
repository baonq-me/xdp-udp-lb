import ctypes
from typing import Optional

from pydantic import BaseModel, Field


class Backend(ctypes.Structure):
    _fields_ = [
        ("ip", ctypes.c_uint32),
        ("port", ctypes.c_uint16),
        ("pad", ctypes.c_uint16),
        ("mac", ctypes.c_ubyte * 6),
    ]

class BackendRequest(BaseModel):
    ip: str = Field(..., description="Backend IP", example="172.30.0.5")
    port: int = Field(..., description="Backend port", example=8000)
    mac: Optional[str] = Field(
        default=None,
        description="Backend MAC address (optional). If leave empty, MAC address in arp table is first choice, then fallback to defaut gateway MAC address",
        example="00:11:22:33:44:55",
        pattern=r"^([0-9a-f]{2}:){5}([0-9a-f]{2})$"
    )


class MacAddr(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_ubyte * 6)]

def get_empty_backend():
    mac = (0,0,0,0,0,0)
    return Backend(
        ip=0,
        port=0,
        pad=0,
        mac=(ctypes.c_ubyte * 6)(*mac)
    )

