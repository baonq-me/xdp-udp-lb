import utils
from object import *
import struct
import socket

# Use arp -n to get destination mac address
# Tested with Cisco Nexus 92160YC-X :))
servers = [
    ("127.0.0.1", 5555),
    ("127.0.0.1", 6666)
]
device = "eth0"

def make_backend(ip_str, port, mac):
    print(f"Adding backend {ip_str}:{port} via mac " + "[" + ", ".join(f"0x{b:02X}" for b in mac) + "]")
    return Backend(
        ip=struct.unpack("I", socket.inet_aton(ip_str))[0],
        port=socket.htons(port),
        pad=0,
        mac=(ctypes.c_ubyte * 6)(*mac)
    )

backends = []

for i, (ip, port) in enumerate(servers):
    backends.append(make_backend(ip, port, utils.get_route_mac(ip)["mac_array"]))
