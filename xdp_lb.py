import json
import socket
import struct
import sys
import threading
import time
from contextlib import asynccontextmanager
from multiprocessing import cpu_count

import schedule
import uvicorn
from bcc import BPF
from fastapi import FastAPI
from prometheus_client import *
from starlette.responses import Response

import config
import utils
from object import *
from utils import get_link_info

BACKENDS = []

DEVICE = config.device  # change this to your NIC
HOSTNAME = socket.gethostname()
b = BPF(src_file="xdp_prog.c", cflags=["-w", "-D__MAX_CPU__=%u" % cpu_count()], debug=0)

packet_counter_last_1s = 0
packet_rate_1s = 0

xdp_collector_registry = CollectorRegistry()
packet_processed_rate = Gauge(name="xdp_packet_processed_rate", documentation="Instant processed packets per second", labelnames=["interface", "host"], registry=xdp_collector_registry)
packet_processed = Gauge(name="xdp_packet_processed", documentation="Packets processed", labelnames=["interface", "host"], registry=xdp_collector_registry)
interface_stat = Gauge(name="interfaces_stat", documentation="Interface runtime stats", labelnames=["interface", "type", "host"], registry=xdp_collector_registry)
interface_spec = Gauge(name="interfaces_spec", documentation="Interface specifications", labelnames=["interface", "type", "host"], registry=xdp_collector_registry)

xdp_mode = Gauge(name="xdp_mode", documentation="Information", labelnames=["interface", "host", "mode"], registry=xdp_collector_registry)
xdp_prog_id = Counter(name="xdp_prog_id", documentation="Information", labelnames=["interface", "host"], registry=xdp_collector_registry)
interface_qdisk = Gauge(name="interface_qdisk", documentation="Interface queuing disciplines", labelnames=["interface", "host", "qdisk"], registry=xdp_collector_registry)



def packet_rate_counter():
    global packet_counter_last_1s
    global packet_rate_1s

    packet_rate_1s = b["counter"][0].value - packet_counter_last_1s
    packet_counter_last_1s = b["counter"][0].value

    packet_processed.labels(interface=DEVICE, host=HOSTNAME).set(b["counter"][0].value)
    packet_processed_rate.labels(interface=DEVICE, host=HOSTNAME).set(packet_rate_1s)

def run_scheduler():
    """Run scheduler loop in background"""
    schedule.every(1).seconds.do(packet_rate_counter)
    while True:
        schedule.run_pending()
        time.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🚀 Startup code

    xdp_mode = "XDP_FLAGS_DRV_MODE"
    #xdp_mode = "XDP_FLAGS_SKB_MODE"

    try:
        print(f"Trying to load XDP program in mode {xdp_mode} ...")
        time_start = time.time()
        b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=config.flags[xdp_mode])
        print(f"XDP program loaded in {(time.time() - time_start)*1000:.2f} ms")

    except Exception as e1:
        print(e1)
        print(f"Fail to load XDP program in mode {xdp_mode}, falling back to SKB mode ...")
        time_start = time.time()
        try:
            b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=config.flags["XDP_FLAGS_SKB_MODE"])
            print(f"XDP program loaded in skb mode in {(time.time() - time_start)*1000:.2f} ms")
        except Exception as e2:
            print(e2)
            print("Can not load XDP program. Exit.")
            sys.exit(1)

    # b['rb'].open_ring_buffer(print_event)

    for i, backend in enumerate(BACKENDS):
        b["backends"][i] = backend

    filter_ip = utils.get_ip_address(DEVICE)
    b["filter_ip"][0] = ctypes.c_uint32(struct.unpack("I", socket.inet_aton(filter_ip))[0])
    print(f"Max CPUs: {cpu_count()}")
    for i in range(cpu_count()):
        filter_port = 5000+i
        print(f"Filter destination: {filter_ip}:{filter_port}")
        filter_ports = b["filter_ports"]
        filter_ports[filter_ports.Key(filter_port)] = filter_ports.Leaf(1)

    b["backend_counter"][0] = ctypes.c_uint16(len(BACKENDS))

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

@app.get("/metrics")
def get_metrics():

    link_info = get_link_info(DEVICE)

    for metric_name in link_info["IFLA_STATS64"].keys():
        interface_stat.labels(interface=DEVICE, host=HOSTNAME, type=metric_name).set(link_info["IFLA_STATS64"][metric_name])

    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="num_tx_queue").set(link_info["IFLA_NUM_TX_QUEUES"])
    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="num_rx_queue").set(link_info["IFLA_NUM_RX_QUEUES"])

    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=DEVICE, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])

    for metric_name in link_info["IFLA_AF_SPEC"]["AF_INET"].keys():
        interface_spec.labels(interface=DEVICE, host=HOSTNAME, type=metric_name).set(link_info["IFLA_AF_SPEC"]["AF_INET"][metric_name])

    # XDP mode
    '''
    "IFLA_XDP": {
        "IFLA_XDP_SKB_PROG_ID": 1023,
        "IFLA_XDP_ATTACHED": "xdpgeneric",
        "IFLA_XDP_PROG_ID": 1023
    },
    
    "IFLA_XDP": {
        "IFLA_XDP_DRV_PROG_ID": 1047,
        "IFLA_XDP_ATTACHED": "xdp",
        "IFLA_XDP_PROG_ID": 1047
    },
    
    "IFLA_XDP": {
        "IFLA_XDP_ATTACHED": null
    },

    '''

    xdp_prog_id.clear()
    xdp_mode.clear()

    if "IFLA_XDP" in link_info.keys() and "IFLA_XDP_ATTACHED" in link_info["IFLA_XDP"].keys() and link_info["IFLA_XDP"]["IFLA_XDP_ATTACHED"]:
        xdp_prog_id.labels(interface=DEVICE, host=HOSTNAME).inc(link_info["IFLA_XDP"].get("IFLA_XDP_PROG_ID"))
        xdp_mode.labels(interface=DEVICE, host=HOSTNAME, mode=link_info["IFLA_XDP"]["IFLA_XDP_ATTACHED"]).set(1)
    else:
        xdp_mode.labels(interface=DEVICE, host=HOSTNAME, mode="").set(0)

    interface_qdisk.clear()
    interface_qdisk.labels(interface=DEVICE, host=HOSTNAME, qdisk=link_info["IFLA_QDISC"]).set(1)

    return Response(
        generate_latest(xdp_collector_registry),
        media_type=CONTENT_TYPE_LATEST
    )


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


@app.get("/link")
def link_info():
    return utils.get_link_info(DEVICE)

if __name__ == "__main__":

    link_info = get_link_info(DEVICE)
    if link_info.get("IFLA_XDP", {}).get("IFLA_XDP_ATTACHED", None):
        print(f"A xdp program is being attached to nic {DEVICE}: {json.dumps(link_info.get('IFLA_XDP', {}))}")
        print("Exiting ...")
        sys.exit(1)

    BACKENDS = config.get_backends()

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

        #  ip link set dev eth0 xdpgeneric off
        # Run this if xdp program can not be detached