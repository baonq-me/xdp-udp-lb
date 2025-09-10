import logging
import socket
import fcntl
import struct
import subprocess

import netifaces
from pyroute2 import IPRoute
import json
import ast

def get_ip_address(ifname: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(
        fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack('256s', ifname[:15].encode('utf-8'))
        )[20:24]
    )

def mac_string_to_int(mac_str: str):
    return tuple(int(b, 16) for b in mac_str.split(":"))

def get_mac_tuple(interface: str):
    with open(f"/sys/class/net/{interface}/address", "r") as f:
        mac_str = f.readline()
        return  mac_string_to_int(mac_str)

def read_arp_table():
    arp_table = []
    with open("/proc/net/arp") as f:
        next(f)  # skip header
        for line in f:
            fields = line.split()
            entry = {
                "ip": fields[0],
                "hw_type": fields[1],
                "flags": fields[2],
                "mac": fields[3],
                "mask": fields[4],
                "device": fields[5],
            }
            arp_table.append(entry)
    return arp_table

def get_route_mac(ip):

    for arp_entry in read_arp_table():
        if arp_entry["ip"] == ip:
            mac_array = mac_string_to_int(arp_entry["mac"])
            arp_entry["mac_array"] = mac_array
            return arp_entry

    return None


def get_link_attrs(attrs):
    try:
        return json.loads(str(attrs).replace('\'', '\"'))
    except:
        try:
            return dict(ast.literal_eval(str(attrs))["attrs"])
        except:
            try:
                return str(attrs)
            except:
                return "can not parse data"


def get_link_info_by_interface(iface):
    info = {}
    with IPRoute() as ipr:
        ifindex = socket.if_nametoindex(iface)

        links = ipr.get_links(ifindex)
        ipr.close()

        for link in links:
            for attr in link.get("attrs", []):
                info[attr[0]] = get_link_attrs(attr[1])

        if "IFLA_AF_SPEC" in info and "AF_INET6" in info["IFLA_AF_SPEC"]:
            af_inet6 = {}
            for attr in info["IFLA_AF_SPEC"]["AF_INET6"]["attrs"]:
                af_inet6[attr[0]] = attr[1]
            info["IFLA_AF_SPEC"]["AF_INET6"] = af_inet6

    return info

def get_loaded_xdp_program(interface):
    link_info = get_link_info_by_interface(interface)

    if link_info.get("IFLA_XDP", {}).get("IFLA_XDP_ATTACHED", None):
        return link_info["IFLA_XDP"]

    return None

def get_ethtool_stats(interface: str) -> dict:
    """
    Get NIC statistics from ethtool -S <interface>
    Returns a dict {stat_name: int_value}
    """
    stats = {}
    try:
        # Run ethtool -S
        result = subprocess.run(
            ["ethtool", "-S", interface],
            capture_output=True,
            text=True,
            check=True
        )
        for i,line in enumerate(result.stdout.splitlines()):
            # Ignore header line
            if i == 0:
                continue

            # Lines look like: "     rx_packets: 12345"
            if ":" in line:
                key, value = line.split(":", 1)
                stats[key.strip()] = int(value.strip())
    except subprocess.CalledProcessError as e:
        logging.error(e)
        return {}

    return stats

def get_default_gateway_ip():
    gws = netifaces.gateways()
    return gws['default'][netifaces.AF_INET][0]

def get_mac_str_by_ip(ip):
    with open("/proc/net/arp") as f:
        for line in f.readlines()[1:]:  # skip header
            fields = line.split()
            if fields[0] == ip:
                return fields[3]  # MAC address column
    return None