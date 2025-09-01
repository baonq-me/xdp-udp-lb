import ctypes
import socket
import struct
import time

from bcc import BPF


class Backend(ctypes.Structure):
    _fields_ = [
        ("ip", ctypes.c_uint32),
        ("port", ctypes.c_uint16),
        ("pad", ctypes.c_uint16),
        ("mac", ctypes.c_ubyte * 6),
    ]

class MacAddr(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_ubyte * 6)]

b = BPF(src_file="xdp_redirect.c")
in_fn  = b.load_func("xdp_prog", BPF.XDP)

DEVICE = "eth0"   # change this to your NIC
#b.attach_xdp(DEVICE, in_fn, flags=BPF.XDP_FLAGS_SKB_MODE)
#b.attach_xdp(DEVICE, in_fn, flags=BPF.XDP_FLAGS_DRV_MODE)
b.attach_xdp(DEVICE, in_fn, 0)


def print_event(ctx, data, size):
    event = b['rb'].event(data)
    # print(f"Receive packet size {event.pkt_size}")

b['rb'].open_ring_buffer(print_event)


backends = b["backends"]

def make_backend(ip_str, port, mac):
    return Backend(
        ip=struct.unpack("I", socket.inet_aton(ip_str))[0],
        port=socket.htons(port),
        pad=0,
        mac=(ctypes.c_ubyte * 6)(*mac)
    )

servers = [
    ("x.x.x.x", 12345, [0x08, 0xF1, 0xEA, 0xF3, 0xF5, 0x86]),
    ("x.x.x.x", 12346, [0x2A, 0x48, 0x66, 0xA4, 0x5C, 0x0A]),

    ("x.x.x.x",  12345, [0x26, 0xFF, 0xCE, 0x06, 0x8C, 0x52]),
    ("x.x.x.x", 12345, [0xAC, 0x1F, 0x6B, 0x34, 0xBC, 0xD8]),
    ("x.x.x.x", 12346, [0xAC, 0x1F, 0x6B, 0x34, 0x85, 0x88]),

    ("x.x.x.x",    5555, [0x3C, 0x8A, 0xB0, 0xE4, 0xDA, 0x01]),
    ("x.x.x.x",   12345, [0x3C, 0x8A, 0xB0, 0xE4, 0xDA, 0x01]),

    ("x.x.x.x",    5555, [0x26, 0xFF, 0xCE, 0x06, 0x8C, 0x52]),
    ("x.x.x.x",   12345, [0x26, 0xFF, 0xCE, 0x06, 0x8C, 0x52]),
]
for i, (ip, port, mac) in enumerate(servers):
    backend = make_backend(ip, port, mac)
    backends[i] = backend


ip_val = struct.unpack("I", socket.inet_aton("x.x.x.x"))[0]
b["filter_ip"][0] = ctypes.c_uint32(ip_val)
b["filter_port"][0] = ctypes.c_uint16(socket.htons(5555))
b["backend_counter"][0] = ctypes.c_uint16(len(servers))

# b["tx_port"][0] = ctypes.c_int(socket.if_nametoindex(DEVICE))

# ebpf-dev
b["lb_mac"][0] = MacAddr((0x7A, 0x61, 0x7A, 0xCF, 0x02, 0xE4))

# apollo
# b["lb_mac"][0] = MacAddr((0xAC, 0x1F, 0x6B, 0x33, 0xF3, 0xD0))


print(f"Listening on {DEVICE}, printing packet sizes...")
try:
    while True:
        b.ring_buffer_poll()

        cnt = b["counter"][0].value
        print("Packets processed:", cnt)
        time.sleep(0.2)
        #b.ring_buffer_consume()
except KeyboardInterrupt:
    pass
finally:
    b.remove_xdp(DEVICE, 0)
