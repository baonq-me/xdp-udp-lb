# UDP Load Balancer using XDP

**This work is just for fun and still in progress ...*

![alt text](xdp-lb-grafana.png "Title")


## Key features

- Load balance UDP packets to multiple backend (configurable destination IP and Port)
- Prometheus exporter metrics
- Carefully tested at 4 milion, 280-byte packets per second (4Mpps, 9Gbps) using enterprise-grade network devices (`Cisco Nexus 92160YC-X` for packet switching).

- Tested by `nc`, `hping3` and a `kernel level packet generator`

Note 1: To save CPU workload, this load balancer do not recalculate UDP payload checksum when modify destion port.

Note 2: Debuging techniques, kernel tuning parameters, NIC parameters and hardware specs are not included in this repo. If you can not reach 4Mpps, it's not about the code :D

## Getting started

- Step 1: Prepare a Ubuntu 24.04 or Rocky 9 machine
- Step 2: Install bcc at [https://github.com/iovisor/bcc](https://github.com/iovisor/bcc)
- Step 3: `pip install -r requirements.txt`
- Step 4: Update `config.py`
- Step 5: `python xdb_lb.py`

## Sample metrics

```
curl -s 127.0.0.1:8000/metrics
# HELP xdp_packet_processed_rate Instant processed packets per second
# TYPE xdp_packet_processed_rate gauge
xdp_packet_processed_rate{host="ebpf-dev",interface="eth0"} 182351.0
# HELP xdp_packet_processed Packets processed
# TYPE xdp_packet_processed gauge
xdp_packet_processed{host="ebpf-dev",interface="eth0"} 722103.0
# HELP interfaces_stat Interface runtime stats
# TYPE interfaces_stat gauge
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_packets"} 9.36350127e+08
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_packets"} 6.49417534e+08
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_bytes"} 5.0914459884e+010
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_bytes"} 4.3758133082e+010
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_dropped"} 1.19207681e+08
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_dropped"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="multicast"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="collisions"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_length_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_over_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_crc_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_frame_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_fifo_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_missed_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_aborted_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_carrier_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_fifo_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_heartbeat_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_window_errors"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="rx_compressed"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="tx_compressed"} 0.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="num_tx_queue"} 2.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="num_rx_queue"} 2.0
interfaces_stat{host="ebpf-dev",interface="eth0",type="mtu"} 1500.0
# HELP interfaces_spec Interface specifications
# TYPE interfaces_spec gauge
interfaces_spec{host="ebpf-dev",interface="eth0",type="dummy"} 65668.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="forwarding"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="mc_forwarding"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="proxy_arp"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="accept_redirects"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="secure_redirects"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="send_redirects"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="shared_media"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="rp_filter"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="accept_source_route"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="bootp_relay"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="log_martians"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="tag"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="arpfilter"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="medium_id"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="noxfrm"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="nopolicy"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="force_igmp_version"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="arp_announce"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="arp_ignore"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="promote_secondaries"} 1.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="arp_accept"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="arp_notify"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="accept_local"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="src_vmark"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="proxy_arp_pvlan"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="route_localnet"} 0.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="igmpv2_unsolicited_report_interval"} 10000.0
interfaces_spec{host="ebpf-dev",interface="eth0",type="igmpv3_unsolicited_report_interval"} 1000.0
# HELP xdp_mode Information
# TYPE xdp_mode gauge
xdp_mode{host="ebpf-dev",interface="eth0",mode="xdp"} 1.0
# HELP xdp_prog_id_total Information
# TYPE xdp_prog_id_total counter
xdp_prog_id_total{host="ebpf-dev",interface="eth0"} 1162.0
# HELP xdp_prog_id_created Information
# TYPE xdp_prog_id_created gauge
xdp_prog_id_created{host="ebpf-dev",interface="eth0"} 1.7570728622585256e+09
# HELP interface_qdisk Interface queuing disciplines
# TYPE interface_qdisk gauge
interface_qdisk{host="ebpf-dev",interface="eth0",qdisk="mq"} 1.0
```