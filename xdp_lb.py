import collections
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from multiprocessing import cpu_count

import numpy as np
import schedule
import uvicorn
from bcc import BPF
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import *
from scapy.arch import get_if_hwaddr
from scapy.layers.l2 import ARP, Ether
from scapy.sendrecv import sendp
from starlette.responses import Response

import config
import utils
from object import *
from utils import *

logging.basicConfig(filename="/dev/stdout",
                    filemode='a',
                    format='[%(asctime)s,%(msecs)d] [%(filename)s:%(lineno)d] [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=os.environ.get('LOGLEVEL', 'INFO').upper()
                    )

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
    packet_latency.labels(interface=config.device_in, host=HOSTNAME, type="mean").set(0 if len(packet_latency_bucket) == 0 else latency.mean())
    packet_latency.labels(interface=config.device_in, host=HOSTNAME, type="min").set(0 if len(packet_latency_bucket) == 0 else latency.min())
    packet_latency.labels(interface=config.device_in, host=HOSTNAME, type="max").set(0 if len(packet_latency_bucket) == 0 else latency.max())
    packet_latency.labels(interface=config.device_in, host=HOSTNAME, type="std").set(0 if len(packet_latency_bucket) == 0 else latency.std())
    for p in [25,50,90,95,99]:
        packet_latency.labels(interface=config.device_in, host=HOSTNAME, type=f"p{p}").set(0.0 if len(packet_latency_bucket) == 0 else float(np.percentile(latency, p)))

    packet_latency_bucket.clear()


    global packet_counter_per_cpus_last_1s
    global packet_counter_rate_per_cpus_last_1s


    packet_counter_per_cpus = read_total_packets_processed()
    packet_counter_rate_per_cpus = [x-y for x,y in zip(packet_counter_per_cpus, packet_counter_per_cpus_last_1s)]

    packet_counter_rate_per_cpus_last_1s = packet_counter_rate_per_cpus
    packet_counter_per_cpus_last_1s = packet_counter_per_cpus

    packet_processed.labels(cpu="total", interface=config.device_in, host=HOSTNAME).set(sum(packet_counter_per_cpus))
    for i,v in enumerate(packet_counter_per_cpus):
        packet_processed.labels(cpu=str(i), interface=config.device_in, host=HOSTNAME).set(v)

    packet_processed_rate.labels(cpu="total", interface=config.device_in, host=HOSTNAME).set(sum(packet_counter_rate_per_cpus))
    for i,v in enumerate(packet_counter_rate_per_cpus):
        packet_processed_rate.labels(cpu=str(i), interface=config.device_in, host=HOSTNAME).set(v)

    for k,v in utils.get_ethtool_stats(config.device_in).items():
        interface_ethtool_stat.labels(interface=config.device_in, host=HOSTNAME, type=k).set(v)

def broadcast_arp():
    ip = config.vip
    mac = get_if_hwaddr(config.device_in)

    logging.info(f"Broadcasting ARP for {ip} ({mac}) on {config.device_in} ...")

    ether = Ether(dst="ff:ff:ff:ff:ff:ff")
    arp = ARP(op=2, psrc=ip, hwsrc=mac, pdst=ip, hwdst="00:00:00:00:00:00")
    packet = ether / arp

    sendp(packet, iface=config.device_in, verbose=False)


def run_scheduler():
    """Run scheduler loop in background"""
    schedule.every(1).seconds.do(packet_rate_counter)
    if config.vip != "":
        schedule.every(3).seconds.do(broadcast_arp)

    while True:
        schedule.run_pending()
        time.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🚀 Startup code

    try:
        logging.info(f"Trying to load XDP program in mode {config.xdp_mode} ...")
        time_start = time.time()
        b.attach_xdp(config.device_in, b.load_func("xdp_prog", BPF.XDP), flags=config.flags[config.xdp_mode])
        logging.info(f"XDP program loaded in {(time.time() - time_start)*1000:.2f} ms")

    except Exception as e1:
        logging.exception(e1)
        logging.error(f"Fail to load XDP program in mode {config.xdp_mode}, falling back to SKB mode ...")
        time_start = time.time()
        try:
            b.attach_xdp(config.device_in, b.load_func("xdp_prog", BPF.XDP), flags=config.flags["XDP_FLAGS_SKB_MODE"])
            logging.info(f"XDP program loaded in skb mode in {(time.time() - time_start)*1000:.2f} ms")
        except Exception as e2:
            logging.exception(e2)
            logging.info("Can not load XDP program. Exit.")
            sys.exit(1)

    b['rb'].open_ring_buffer(print_event)

    xdp_backends = config.get_backends()
    for i, backend in enumerate(xdp_backends):
        b["backends"][i] = backend

    filter_ip = utils.get_ip_address(config.device_in) if config.vip == "" else config.vip
    logging.info(f"Filter IP: {filter_ip}")
    b["filter_ip"][0] = ctypes.c_uint32(struct.unpack("I", socket.inet_aton(filter_ip))[0])
    logging.info(f"Max CPUs: {cpu_count()}")
    for destination_port in config.destination_ports:
        logging.info(f"Filter destination: {filter_ip}:{destination_port}")
        filter_ports = b["filter_ports"]
        filter_ports[filter_ports.Key(destination_port)] = filter_ports.Leaf(1)


    leaf_backend_counter = b["backend_counter"].Leaf()
    for i in range(len(leaf_backend_counter)):
        leaf_backend_counter[i] = ctypes.c_uint64(len(xdp_backends))
    b["backend_counter"][ctypes.c_uint32(0)] = leaf_backend_counter


    # Device to send traffic
    if config.device_in != config.device_out:
        logging.info(f"Setting out interface to {config.device_out}")
        b["tx_port"][0] = ctypes.c_int(socket.if_nametoindex(config.device_out))

        source_ip_out = utils.get_ip_address(config.device_out)
        logging.info(f"Setting out ip address to {source_ip_out}")
        b["source_ip_out"][0] = ctypes.c_uint32(struct.unpack("I", socket.inet_aton(source_ip_out))[0])
    else:
        b["source_ip_out"][0] = ctypes.c_uint32(struct.unpack("I", socket.inet_aton(filter_ip))[0])


    # Load balancer mac address
    b["lb_mac"][0] = MacAddr(utils.get_mac_tuple(config.device_in if config.device_in == config.device_out else config.device_out))

    logging.info(f"Listening on {config.listen_host}:{config.listen_port} ...")

    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()

    logging.info("✅ Server has started up!")

    xdp_time_start.labels(interface=config.device_in, host=HOSTNAME).set(time.time())

    yield

    # 🛑 Shutdown code
    logging.info("🛑 Server is shutting down...")
    logging.info(f"Removing XDP prog from NIC {config.device_in}")
    b.remove_xdp(config.device_in, 0)

app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


@app.get("/api/v1/configs")
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
        "device_in": config.device_in,
        "device_in_ip": get_ip_address(config.device_in) if config.vip == "" else config.vip,
        "device_out": config.device_out,
        "device_out_ip": get_ip_address(config.device_out),
        "default_gateway_ip": get_default_gateway_ip(),
        "default_gateway_mac": get_mac_str_by_ip(get_default_gateway_ip()),
        "filter_ip": filter_ip,
        "filter_ports": filter_ports,
        "backends": backends,
    }


@app.get("/metrics")
def get_metrics():

    link_info = get_link_info_by_interface(config.device_in)

    for metric_name in link_info["IFLA_STATS64"].keys():
        interface_stat.labels(interface=config.device_in, host=HOSTNAME, type=metric_name).set(link_info["IFLA_STATS64"][metric_name])

    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="num_tx_queue").set(link_info["IFLA_NUM_TX_QUEUES"])
    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="num_rx_queue").set(link_info["IFLA_NUM_RX_QUEUES"])

    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])
    interface_stat.labels(interface=config.device_in, host=HOSTNAME, type="mtu").set(link_info["IFLA_MTU"])

    for metric_name in link_info["IFLA_AF_SPEC"]["AF_INET"].keys():
        interface_spec.labels(interface=config.device_in, host=HOSTNAME, type=metric_name).set(link_info["IFLA_AF_SPEC"]["AF_INET"][metric_name])

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
        xdp_prog_id.labels(interface=config.device_in, host=HOSTNAME).inc(link_info["IFLA_XDP"].get("IFLA_XDP_PROG_ID"))
        xdp_mode.labels(interface=config.device_in, host=HOSTNAME, mode=link_info["IFLA_XDP"]["IFLA_XDP_ATTACHED"]).set(1)
    else:
        xdp_mode.labels(interface=config.device_in, host=HOSTNAME, mode="").set(0)

    interface_qdisk.clear()
    interface_qdisk.labels(interface=config.device_in, host=HOSTNAME, qdisk=link_info["IFLA_QDISC"]).set(1)

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

@app.get("/api/v1/links")
def get_link_info():
    return {
        "device_in": utils.get_link_info_by_interface(config.device_in),
        "device_out": utils.get_link_info_by_interface(config.device_out)
    }

if __name__ == "__main__":

    link_info = get_link_info_by_interface(config.device_in)
    if link_info.get("IFLA_XDP", {}).get("IFLA_XDP_ATTACHED", None):
        logging.error(f"A xdp program is being attached to nic {config.device_in}: {json.dumps(link_info.get('IFLA_XDP', {}))}")
        logging.error("Exiting ...")
        sys.exit(1)

    try:
        uvicorn.run(
            "xdp_lb:app",
            host=config.listen_host,
            port=config.listen_port,
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
        if utils.get_loaded_xdp_program(config.device_in):
            logging.warning("XDP program still attached, forcing it to be detached")
            subprocess.run(["ip", "link", "set", "dev", config.device_in, "xdp", "off"])
            subprocess.run(["ip", "link", "set", "dev", config.device_in, "xdpgeneric", "off"])