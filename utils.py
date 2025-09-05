import socket
import fcntl
import struct
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

def get_mac_tuple(interface: str):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    info = fcntl.ioctl(
        s.fileno(),
        0x8927, # SIOCGIFHWADDR
        struct.pack('256s', interface[:15].encode('utf-8'))
    )
    mac_bytes = info[18:24]
    return tuple(int(binary) for binary in mac_bytes)

def get_route_mac(ip):
    ipr = IPRoute()
    # Step 1: Get route info
    route = ipr.route("get", dst=ip)[0]
    oif = route.get("oif")
    iface = ipr.get_links(oif)[0].get_attr("IFLA_IFNAME")

    gateway = route.get_attr("RTA_GATEWAY")
    if not gateway:  # direct host route, no gateway
        gateway = ip

    # Step 2: Query neighbor (ARP/NDP table)
    neighs = ipr.get_neighbours(dst=gateway, ifindex=oif)
    mac = neighs[0].get_attr("NDA_LLADDR") if neighs else None

    mac_array = None
    if mac:
        mac_array = [int(b, 16) for b in mac.split(":")]  # integers

    return {
        "dst": ip,
        "gateway": gateway,
        "interface": iface,
        "mac": mac,
        "mac_array": mac_array
    }


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


def get_link_info(iface):
    ipr = IPRoute()
    ifindex = socket.if_nametoindex(iface)

    links = ipr.get_links(ifindex)
    ipr.close()

    info = {}

    for link in links:
        for attr in link.get("attrs", []):
            info[attr[0]] = get_link_attrs(attr[1])

    if "IFLA_AF_SPEC" in info and "AF_INET6" in info["IFLA_AF_SPEC"]:
        af_inet6 = {}
        for attr in info["IFLA_AF_SPEC"]["AF_INET6"]["attrs"]:
            af_inet6[attr[0]] = attr[1]
        info["IFLA_AF_SPEC"]["AF_INET6"] = af_inet6

    return info