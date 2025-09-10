import logging
import os
from os import cpu_count

import utils
from object import *
import struct
import socket
from bcc import BPF
import dotenv

dotenv.load_dotenv("env")

def parse_config_backends(s):
    result = []
    for entry in s.split(","):
        if ":" in entry:
            host, port = entry.rsplit(":", 1)
        else:
            # Assume last 4–5 digits are the port if no colon
            # (works for your "172.30.23.25555" example)
            host, port = entry[:-5], entry[-5:]
        result.append((host.strip(), int(port)))
    return result

# Use arp -n to get destination mac address
servers = parse_config_backends(os.environ.get("BACKENDS", default="127.0.0.1:5000"))
device_in = os.environ.get("INTERFACE_IN", default="eth0")
device_out = os.environ.get("INTERFACE_OUT", default="eth0")

vip = os.environ.get("INTERFACE_IN_VIP", "")

listen_host = os.environ.get("LISTEN_HOST", default="0.0.0.0")
listen_port = int(os.environ.get("LISTEN_PORT", default="8000"))

destination_ports = [int(j) for j in os.environ.get("DESTINATION_PORTS", default=','.join([str(5000+i) for i in range(cpu_count())])).split(",")]
xdp_mode = os.environ.get("XDP_MODE", default="XDP_FLAGS_SKB_MODE")

# https://docs.ebpf.io/linux/program-type/BPF_PROG_TYPE_XDP
flags = {
    "XDP_FLAGS_DRV_MODE": BPF.XDP_FLAGS_DRV_MODE,
    "XDP_FLAGS_SKB_MODE": BPF.XDP_FLAGS_SKB_MODE,
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
            default_gw_mac = utils.get_mac_str_by_ip(utils.get_default_gateway_ip())
            logging.warning(f"Backend IP {ip} not exist in routing table, using default gateway mac address {default_gw_mac}")
            backends.append(make_backend(ip, port, utils.mac_string_to_int(default_gw_mac)))

        else:
            backends.append(make_backend(ip, port, ip_mac["mac_array"]))

    return backends
