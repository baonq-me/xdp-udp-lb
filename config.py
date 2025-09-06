import logging
from os import cpu_count

import utils
from object import *
import struct
import socket
from bcc import BPF

# Use arp -n to get destination mac address
servers = [
    ("172.30.30.21", 5555),
    ("172.30.30.22", 5555),
    ("172.30.30.23", 5555),
]
device = "eth0"
destination_ports = [5000+i for i in range(cpu_count())]
xdp_mode = "XDP_FLAGS_DRV_MODE"

# https://docs.ebpf.io/linux/program-type/BPF_PROG_TYPE_XDP
flags = {
    "XDP_FLAGS_SKB_MODE": BPF.XDP_FLAGS_SKB_MODE,
    "XDP_FLAGS_DRV_MODE": BPF.XDP_FLAGS_DRV_MODE,
    "XDP_FLAGS_HW_MODE": BPF.XDP_FLAGS_HW_MODE,         # Only Netronome Agilio CX SmartNICs support this
    "XDP_FLAGS_REPLACE": BPF.XDP_FLAGS_REPLACE,        # Replace exsting XDP program
}

def make_backend(ip_str, port, mac):
    logging.info(f"Backend {ip_str}:{port} via mac " + "[" + ", ".join(f"0x{b:02X}" for b in mac) + "]")
    return Backend(
        ip=struct.unpack("I", socket.inet_aton(ip_str))[0],
        port=socket.htons(port),
        pad=0,
        mac=(ctypes.c_ubyte * 6)(*mac)
    )


def get_backends():
    backends = []
    for i, (ip, port) in enumerate(servers):
        ip_mac = utils.get_route_mac(ip)
        if not ip_mac:
            logging.info(f"Backend IP {ip} not exist in routing table or can not be reached through interface {device}, ignore")
            continue
        backends.append(make_backend(ip, port, ip_mac["mac_array"]))

    return backends
