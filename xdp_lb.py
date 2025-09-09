import json
import socket
import struct
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from multiprocessing import cpu_count
import os, logging
import schedule
import uvicorn
from bcc import BPF
from fastapi import FastAPI
from prometheus_client import *
from starlette.responses import Response
import numpy as np

import config
import utils
from object import *
from utils import get_link_info

import collections

logging.basicConfig(filename="/dev/stdout",
                    filemode='a',
                    format='[%(asctime)s,%(msecs)d] [%(filename)s:%(lineno)d] [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=os.environ.get('LOGLEVEL', 'INFO').upper()
                    )

DEVICE = config.device
HOSTNAME = socket.gethostname()
b = BPF(src_file="xdp_prog.c", cflags=["-w", "-D__MAX_CPU__=%u" % cpu_count()], debug=0)

packet_counter_per_cpus_last_1s = [0] * cpu_count()
packet_counter_rate_per_cpus_last_1s = [0] * cpu_count()
packet_latency_bucket = collections.deque(maxlen=256*1024)

xdp_collector_registry = CollectorRegistry()

packet_processed_rate = Gauge(name="xdp_packet_processed_rate", documentation="Instant processed packets per second", labelnames=["cpu", "interface", "host"], registry=xdp_collector_registry)
packet_processed = Gauge(name="xdp_packet_processed", documentation="Packets processed", labelnames=["cpu", "interface", "host"], registry=xdp_collector_registry)
packet_latency = Gauge(name="xdp_packet_latency_ns", documentation="Packets processing latency in nanoseconds", labelnames=["type", "interface", "host"], registry=xdp_collector_registry)

interface_stat = Gauge(name="xdp_interfaces_stat", documentation="Interface runtime stats", labelnames=["interface", "type", "host"], registry=xdp_collector_registry)
interface_ethtool_stat = Gauge(name="xdp_interfaces_ethtool_stat", documentation="Interface runtime stats from ethtool, may contains vendor-specific stats", labelnames=["interface", "type", "host"], registry=xdp_collector_registry)
interface_spec = Gauge(name="xdp_interfaces_spec", documentation="Interface specifications", labelnames=["interface", "type", "host"], registry=xdp_collector_registry)
interface_qdisk = Gauge(name="xdp_interface_qdisk", documentation="Interface queuing disciplines", labelnames=["interface", "host", "qdisk"], registry=xdp_collector_registry)

xdp_mode = Gauge(name="xdp_mode", documentation="Information", labelnames=["interface", "host", "mode"], registry=xdp_collector_registry)
xdp_prog_id = Counter(name="xdp_prog_id", documentation="Information", labelnames=["interface", "host"], registry=xdp_collector_registry)

xdp_time_start = Gauge(name="xdp_time_start", documentation="Epoch time in seconds when started", labelnames=["interface", "host"], registry=xdp_collector_registry)

def read_total_packets_processed():
    for k,v in b["counter"].items():
        per_cpu_vals = list(v)
        return per_cpu_vals

    return [0] * cpu_count()


def packet_rate_counter():

    b.ring_buffer_consume()

    latency = np.array(list(packet_latency_bucket))
    packet_latency.labels(interface=DEVICE, host=HOSTNAME, type="mean").set(0 if len(packet_latency_bucket) == 0 else latency.mean())
    packet_latency.labels(interface=DEVICE, host=HOSTNAME, type="min").set(0 if len(packet_latency_bucket) == 0 else latency.min())
    packet_latency.labels(interface=DEVICE, host=HOSTNAME, type="max").set(0 if len(packet_latency_bucket) == 0 else latency.max())
    packet_latency.labels(interface=DEVICE, host=HOSTNAME, type="std").set(0 if len(packet_latency_bucket) == 0 else latency.std())
    for p in [25,50,90,95,99]:
        packet_latency.labels(interface=DEVICE, host=HOSTNAME, type=f"p{p}").set(0.0 if len(packet_latency_bucket) == 0 else float(np.percentile(latency, p)))

    packet_latency_bucket.clear()


    global packet_counter_per_cpus_last_1s
    global packet_counter_rate_per_cpus_last_1s


    packet_counter_per_cpus = read_total_packets_processed()
    packet_counter_rate_per_cpus = [x-y for x,y in zip(packet_counter_per_cpus, packet_counter_per_cpus_last_1s)]

    packet_counter_rate_per_cpus_last_1s = packet_counter_rate_per_cpus
    packet_counter_per_cpus_last_1s = packet_counter_per_cpus

    packet_processed.labels(cpu="total", interface=DEVICE, host=HOSTNAME).set(sum(packet_counter_per_cpus))
    for i,v in enumerate(packet_counter_per_cpus):
        packet_processed.labels(cpu=str(i), interface=DEVICE, host=HOSTNAME).set(v)

    packet_processed_rate.labels(cpu="total", interface=DEVICE, host=HOSTNAME).set(sum(packet_counter_rate_per_cpus))
    for i,v in enumerate(packet_counter_rate_per_cpus):
        packet_processed_rate.labels(cpu=str(i), interface=DEVICE, host=HOSTNAME).set(v)

    for k,v in utils.get_ethtool_stats(DEVICE).items():
        interface_ethtool_stat.labels(interface=DEVICE, host=HOSTNAME, type=k).set(v)

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
        logging.info(f"Trying to load XDP program in mode {config.xdp_mode} ...")
        time_start = time.time()
        b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=config.flags[config.xdp_mode])
        logging.info(f"XDP program loaded in {(time.time() - time_start)*1000:.2f} ms")

    except Exception as e1:
        logging.exception(e1)
        logging.error(f"Fail to load XDP program in mode {config.xdp_mode}, falling back to SKB mode ...")
        time_start = time.time()
        try:
            b.attach_xdp(DEVICE, b.load_func("xdp_prog", BPF.XDP), flags=config.flags["XDP_FLAGS_SKB_MODE"])
            logging.info(f"XDP program loaded in skb mode in {(time.time() - time_start)*1000:.2f} ms")
        except Exception as e2:
            logging.exception(e2)
            logging.info("Can not load XDP program. Exit.")
            sys.exit(1)

    b['rb'].open_ring_buffer(print_event)

    xdp_backends = config.get_backends()
    for i, backend in enumerate(xdp_backends):
        b["backends"][i] = backend

    destination_ip = utils.get_ip_address(DEVICE)
    b["filter_ip"][0] = ctypes.c_uint32(struct.unpack("I", socket.inet_aton(destination_ip))[0])
    logging.info(f"Max CPUs: {cpu_count()}")
    for destination_port in config.destination_ports:
        logging.info(f"Filter destination: {destination_ip}:{destination_port}")
        filter_ports = b["filter_ports"]
        filter_ports[filter_ports.Key(destination_port)] = filter_ports.Leaf(1)


    leaf_backend_counter = b["backend_counter"].Leaf()
    for i in range(len(leaf_backend_counter)):
        leaf_backend_counter[i] = ctypes.c_uint64(len(xdp_backends))
    b["backend_counter"][ctypes.c_uint32(0)] = leaf_backend_counter


    # Device to send traffic
    b["tx_port"][0] = ctypes.c_int(socket.if_nametoindex(DEVICE))

    # Load balancer mac address
    b["lb_mac"][0] = MacAddr(utils.get_mac_tuple(DEVICE))

    logging.info(f"Listening on {DEVICE} ...")

    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()

    logging.info("✅ Server has started up!")

    xdp_time_start.labels(interface=DEVICE, host=HOSTNAME).set(time.time())

    yield

    # 🛑 Shutdown code
    logging.info("🛑 Server is shutting down...")
    logging.info(f"Removing XDP prog from NIC {DEVICE}")
    b.remove_xdp(DEVICE, 0)

app = FastAPI(lifespan=lifespan)


@app.get("/configs")
def get_configs():

    filter_ip = socket.inet_ntoa(struct.pack("I", b["filter_ip"][ctypes.c_int(0)].value))
    filter_ports = []
    backends = []

    for i in range(len(b["backends"])):  # len(table) = array size
        entry = b["backends"][ctypes.c_int(i)]
        if not entry.ip:  # skip empty slots
            continue

        backends.append({
            "ip": socket.inet_ntoa(struct.pack("I", entry.ip)),
            "port": socket.ntohs(entry.port),
            "mac": ":".join(f"{mac_bin:02x}" for mac_bin in entry.mac)
        })

    for k, v in b["filter_ports"].items():
        port = k.value  # the stored destination_port
        enabled = v.value  # the value you stored (1)
        if enabled:
            filter_ports.append(port)

    return {
        "filter_ip": filter_ip,
        "filter_ports": filter_ports,
        "backends": backends,
    }


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

    global packet_counter_per_cpus_last_1s
    global packet_counter_rate_per_cpus_last_1s

    return {
        "packet_processed": packet_counter_per_cpus_last_1s,
        "packet_rate": packet_counter_rate_per_cpus_last_1s
    }

def print_event(ctx, data, size):
    event = b['rb'].event(data)
    #print(f"Receive packet size {event.pkt_size}")
    #print(f"Receive time delta  {event.time_delta} ns")

    packet_latency_bucket.append(event.time_delta)

@app.get("/link")
def link_info():
    return utils.get_link_info(DEVICE)

if __name__ == "__main__":

    link_info = get_link_info(DEVICE)
    if link_info.get("IFLA_XDP", {}).get("IFLA_XDP_ATTACHED", None):
        logging.error(f"A xdp program is being attached to nic {DEVICE}: {json.dumps(link_info.get('IFLA_XDP', {}))}")
        logging.error("Exiting ...")
        sys.exit(1)

    try:
        uvicorn.run(
            "xdp_lb:app",
            host=os.environ.get("HTTP_HOST", "0.0.0.0"),
            port=int(os.environ.get("HTTP_PORT", "8000")),
            workers=1,  # enforce single worker,
            log_config={
                "version": 1,
                "formatters": {
                    "default": {
                        "format": "[%(asctime)s,%(msecs)d] [%(filename)s:%(lineno)d] [%(levelname)s] %(message)s",
                        "datefmt": "%Y-%m-%d %H:%M:%S",
                    },
                },
                "handlers": {
                    "default": {
                        "formatter": "default",
                        "class": "logging.StreamHandler",
                        "stream": sys.stdout,
                    },
                },
                "loggers": {
                    "uvicorn": {"handlers": ["default"], "level": "INFO"},
                    "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
                    "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
                },
            }
        )
    except Exception as e:
        logging.error(e)
    finally:
        if utils.get_loaded_xdp_program(DEVICE):
            logging.warning("XDP program still attached, forcing it to be detached")
            subprocess.run(["ip", "link", "set", "dev", DEVICE, "xdp", "off"])
            subprocess.run(["ip", "link", "set", "dev", DEVICE, "xdpgeneric", "off"])