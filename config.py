import utils
from object import *
import struct
import socket
from bcc import BPF

# Use arp -n to get destination mac address. Will fail if destination ip is not in routing table
servers = [
    ("172.30.30.21", 5555),
    ("172.30.30.22", 5555),
    ("172.30.30.23", 5555),
]

device = "eth0"

# https://docs.ebpf.io/linux/program-type/BPF_PROG_TYPE_XDP
flags = {
    "XDP_FLAGS_SKB_MODE": BPF.XDP_FLAGS_SKB_MODE,
    "XDP_FLAGS_DRV_MODE": BPF.XDP_FLAGS_DRV_MODE,
    "XDP_FLAGS_HW_MODE": BPF.XDP_FLAGS_HW_MODE,         # Only Netronome Agilio CX SmartNICs support this
    "XDP_FLAGS_REPLACE": BPF.XDP_FLAGS_REPLACE,        # Replace exsting XDP program
}

def make_backend(ip_str, port, mac):
    print(f"Backend {ip_str}:{port} via mac " + "[" + ", ".join(f"0x{b:02X}" for b in mac) + "]")
    return Backend(
        ip=struct.unpack("I", socket.inet_aton(ip_str))[0],
        port=socket.htons(port),
        pad=0,
        mac=(ctypes.c_ubyte * 6)(*mac)
    )


def get_backends():
    backends = []
    for i, (ip, port) in enumerate(servers):
        backends.append(make_backend(ip, port, utils.get_route_mac(ip)["mac_array"]))

    return backends
