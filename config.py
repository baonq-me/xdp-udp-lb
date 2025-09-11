import os
import sys
from os import cpu_count
import dotenv
from bcc import BPF
from utils import *

dotenv.load_dotenv("env")

# Use arp -n to get destination mac address
servers = parse_config_backends(os.environ.get("BACKENDS", default="127.0.0.1:5000"))
device_in = os.environ.get("INTERFACE_IN", default="eth0")
device_out = os.environ.get("INTERFACE_OUT", default="eth0")
vip = os.environ.get("INTERFACE_IN_VIP", "")
filter_ip = get_ip_address(device_in) if vip == "" else vip

listen_host = os.environ.get("LISTEN_HOST", default="0.0.0.0")
listen_port = int(os.environ.get("LISTEN_PORT", default="8000"))

destination_ports = [int(j) for j in os.environ.get("DESTINATION_PORTS", default=','.join([str(5000+i) for i in range(cpu_count())])).split(",")]
xdp_mode = os.environ.get("XDP_MODE", default="XDP_FLAGS_SKB_MODE")

# https://docs.ebpf.io/linux/program-type/BPF_PROG_TYPE_XDP
flags = {
    # High performance
    "XDP_FLAGS_DRV_MODE": BPF.XDP_FLAGS_DRV_MODE,

    # Most compatible
    "XDP_FLAGS_SKB_MODE": BPF.XDP_FLAGS_SKB_MODE,

    # Only Netronome Agilio CX SmartNICs support this
    "XDP_FLAGS_HW_MODE": BPF.XDP_FLAGS_HW_MODE,

    # Replace existing XDP program
    "XDP_FLAGS_REPLACE": BPF.XDP_FLAGS_REPLACE,
}

log_config = {
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

