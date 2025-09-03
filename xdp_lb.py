import ctypes
import socket
import struct
import sys
import threading
import time
import socket
import fcntl
import struct
from contextlib import asynccontextmanager

import schedule
from pyroute2 import IPRoute, AsyncIPRoute
import socket
import config
from object import *
from bcc import BPF
from fastapi import FastAPI
import uvicorn
import utils
import asyncio
import ast
import json
import os

DEVICE = config.device  # change this to your NIC

b = BPF(src_file="xdp_prog.c")

packet_counter_last_1s = 0
packet_rate_1s = 0
#packet_counter_last_1m = 0

def packet_rate_counter():
    global packet_counter_last_1s
    global packet_rate_1s

    packet_rate_1s = b["counter"][0].value - packet_counter_last_1s
    packet_counter_last_1s = b["counter"][0].value

def run_scheduler():
    """Run scheduler loop in background"""
    schedule.every(1).seconds.do(packet_rate_counter)
    while True:
        schedule.run_pending()
        time.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🚀 Startup code

    try:
        b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=BPF.XDP_FLAGS_DRV_MODE)
        print("XDP program loaded in driver mode")
    except Exception as e1:
        print(e1)
        try:
            b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=BPF.XDP_FLAGS_SKB_MODE)
            print("XDP program loaded in skb mode")
        except Exception as e2:
            print(e2)
            try:
                b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=0)
                print("XDP program loaded in default mode")
            except Exception as e3:
                print(e3)
                print("Can not load XDP program. Exit.")
                sys.exit(1)

    b['rb'].open_ring_buffer(print_event)

    for i, backend in enumerate(config.backends):
        b["backends"][i] = backend

    b["filter_ip"][0] = ctypes.c_uint32(struct.unpack("I", socket.inet_aton(utils.get_ip_address(DEVICE)))[0])
    b["filter_port"][0] = ctypes.c_uint16(socket.htons(5555))
    b["backend_counter"][0] = ctypes.c_uint16(len(config.backends))

    # Device to send traffic
    b["tx_port"][0] = ctypes.c_int(socket.if_nametoindex(DEVICE))

    # Load balancer mac address
    b["lb_mac"][0] = MacAddr(utils.get_mac_tuple(DEVICE))

    print(f"Listening on {DEVICE} ...")

    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()

    print("✅ Server has started up!")

    yield

    # 🛑 Shutdown code
    print("🛑 Server is shutting down...")

app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    #b.ring_buffer_poll(timeout=0.1)

    global packet_rate_1s

    return {
        "packet_processed": b["counter"][0].value,
        "packet_rate_1s": packet_rate_1s
    }


def print_event(ctx, data, size):
    event = b['rb'].event(data)
    # print(f"Receive packet size {event.pkt_size}")

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


def get_link_info(iface="eth0"):
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

@app.get("/link")
def link_info():
    return get_link_info("eth0")

if __name__ == "__main__":
    try:
        uvicorn.run(
            "xdp_lb:app",
            host="0.0.0.0",
            port=8000,
            workers=1,  # enforce single worker
        )
    except KeyboardInterrupt:
        pass
    finally:
        print(f"Removing XDP prog from NIC {DEVICE}")
        b.remove_xdp(DEVICE, 0)
