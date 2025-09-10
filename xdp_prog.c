// xdp_prog.c
#include <uapi/linux/bpf.h>
#include <uapi/linux/if_ether.h>
#include <uapi/linux/ip.h>
#include <uapi/linux/udp.h>
#include <uapi/linux/in.h>
#define MAX_UDP_LENGTH 1500

//#define DEBUG 1

struct backend_t {
    u32 ip;   // network byte order
    u16 port; // network byte order
    u16 pad;  // padding to align to 4 bytes
    u8 mac[6];  // backend MAC
};

// Allocate 4096 page (4096 * 4KB page size = 16MB buffer size)
BPF_RINGBUF_OUTPUT(rb, 4096);

// Store backend configs
BPF_ARRAY(backends, struct backend_t, 16);

// Counter for number of backend
BPF_PERCPU_ARRAY(backend_counter, u64, 1);

// Processed packet counter
BPF_PERCPU_ARRAY(counter, u64, 1);

// filter: only match this dest ip/port
BPF_ARRAY(filter_ip, u32, 1);
BPF_HASH(filter_ports, __u16, __u8);
BPF_ARRAY(source_ip_out, u32, 1);

// device map for xdp_redirect (filled from user space)
BPF_DEVMAP(tx_port, 1);

// Mac address of the load balancer
struct macaddr {
    unsigned char addr[6];
};

BPF_ARRAY(lb_mac, struct macaddr, 1);

struct event {
    int pkt_size;
    int time_delta;
};


static __always_inline __u16 csum_fold_helper(__u32 csum) {
    csum = (csum >> 16) + (csum & 0xffff);
    csum += csum >> 16;
    return ~csum;
}

static __always_inline __u16 iph_csum(struct iphdr *iph)
{
    iph->check = 0;
    unsigned long long csum = bpf_csum_diff(0, 0, (unsigned int *)iph, sizeof(struct iphdr), 0);
    return csum_fold_helper(csum);
}


static __always_inline __u16 iph_csum2(struct iphdr *ip) {

    ip->check = 0;
    u32 csum = 0;
    u16 *ip16 = (u16 *)ip;

    #pragma unroll
    for (int i = 0; i < sizeof(*ip) >> 1; i++) {
        csum += ip16[i];
    }

    while (csum >> 16)
        csum = (csum & 0xffff) + (csum >> 16);

    return ~csum;

}

__attribute__((__always_inline__))
static inline __u16 caludpcsum(struct iphdr *ip, struct udphdr *udp, void *data_end)
{
    __u32 csum_buffer = 0;
    __u16 *buf = (void *)udp;

    // Compute pseudo-header checksum
    csum_buffer += (__u16)ip->saddr;
    csum_buffer += (__u16)(ip->saddr >> 16);
    csum_buffer += (__u16)ip->daddr;
    csum_buffer += (__u16)(ip->daddr >> 16);
    csum_buffer += (__u16)ip->protocol << 8;
    csum_buffer += bpf_ntohs(udp->len);

#ifdef DEBUG
    bpf_trace_printk("UDP payload size: %d bytes", bpf_ntohs(udp->len));
    bpf_trace_printk("csum_buffer: 0x%x", csum_buffer);
#endif

    // Compute checksum on udp header + payload
    for (int i = 0; i < MAX_UDP_LENGTH; i += 2) {
      if ((void *)(buf + 1) > data_end) {
        break;
      }

#ifdef DEBUG
      bpf_trace_printk("0x%x", *buf);
#endif
      csum_buffer += *buf;
      buf++;
    }

    if ((void *)buf + 1 <= data_end) {
      // In case payload is not 2 bytes aligned
      csum_buffer += *(__u8 *)buf;
    }


    __u16 csum = (__u16)csum_buffer + (__u16)(csum_buffer >> 16);
    csum = csum & 0xFFFF;

    return (__u16) csum;
}


int xdp_prog(struct xdp_md *ctx) {

    u64 time_start = bpf_ktime_get_ns();

    int pkt_size = (int)(ctx->data_end - ctx->data);

    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;
    struct ethhdr *eth = data;

    // Check if ethernet header size > packet size (invalid packet)
    if ((void *)(eth + 1) > data_end) {
        return XDP_PASS;
    }

    // Check if ethernet frame is carrying an IP packet
    if (eth->h_proto != __constant_htons(ETH_P_IP)) {
        return XDP_PASS;
    }

    // Check valid IP packet
    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) {
        return XDP_PASS;
    }

    // Stop program if packet is not UDP
    if (ip->protocol != IPPROTO_UDP) {
        return XDP_PASS;
    }

    struct udphdr *udp = (void *)(ip + 1);
    //caludpcsum(ip, udp, data_end);

    // Check valid UDP packet
    if ((void *)(udp + 1) > data_end) {
        return XDP_PASS;
    }

    // Filter IP
    u32 fk = 0;
    u32 *fip = filter_ip.lookup(&fk);
    if (!fip) {
#ifdef DEBUG
        bpf_trace_printk("Not match filter ip");
#endif
        return XDP_PASS;
    }

    // Filter port
    u16 dport = __constant_ntohs(udp->dest);
#ifdef DEBUG
    bpf_trace_printk("Checking port %d", dport);
#endif

    __u8 *exists = filter_ports.lookup(&dport);
    if (!exists) {
#ifdef DEBUG
        bpf_trace_printk("Not match filter port");
#endif
        return XDP_PASS;
    }

#ifdef DEBUG
    bpf_trace_printk("Filter IP: 0x%x", bpf_ntohl(*fip));
#endif

    //unsigned char *payload = (unsigned char *)(udp + 1);
    //bpf_trace_printk("UDP Data: %s", payload);
    //bpf_trace_printk("UDP end byte: 0x%x", payload[udp->len-1]);


    // packet counter
    u32 k0 = 0;
    u64 *pktcnt = counter.lookup(&k0);
    if (pktcnt) {
#ifdef DEBUG
        bpf_trace_printk("Receive UDP packet with size %d", pkt_size);
#endif
        //(*pktcnt)++;
        __sync_fetch_and_add(pktcnt, 1);
    } else {
        return XDP_PASS;
    }

    u32 i = 0;
    u64 *backends_cnt = backend_counter.lookup(&i);
    if (!backends_cnt) {
        // No backends is configured
#ifdef DEBUG
        bpf_trace_printk("No backends is configured");
#endif
        return XDP_PASS;
    }

#ifdef DEBUG
    bpf_trace_printk("Backend count: %d", *backends_cnt);
#endif

    // choose backend index (round robin)
    u32 index = (*pktcnt) % (*backends_cnt);
    struct backend_t *be = backends.lookup(&index);
    if (!be) {
        // No backends is configured, this is unexpected
        bpf_trace_printk("Backends %d not found", index);
        return XDP_PASS;
    }

#ifdef DEBUG
    bpf_trace_printk("Using backend %d:%d", be->ip, be->port);
    bpf_trace_printk("Backend mac (hi): %x:%x:%x", be->mac[0], be->mac[1], be->mac[2]);
    bpf_trace_printk("Backend mac (lo): %x:%x:%x", be->mac[3], be->mac[4], be->mac[5]);
#endif

    // L3 rewrite

    u32 sk = 0;
    u32 *spo = source_ip_out.lookup(&sk);
    if (!spo || *spo == *fip) {
#ifdef DEBUG
        bpf_trace_printk("Source IP out not found, using filter ip ");
#endif
        ip->saddr = *fip;
    } else {
#ifdef DEBUG
        bpf_trace_printk("Source IP out: %d", *spo);
#endif

        ip->saddr = *spo;
    }

    ip->daddr = be->ip;
    udp->dest = be->port;

#ifdef DEBUG
    bpf_trace_printk("Redirecting packet to new IP 0x%x from IP 0x%x", bpf_ntohl(ip->daddr), bpf_ntohl(ip->saddr));
#endif

    // L2 rewrite (dst + src MAC)
    __builtin_memcpy(eth->h_dest, be->mac, ETH_ALEN);

    u32 k1 = 0;
    struct macaddr *mac = lb_mac.lookup(&k1);
    if (mac) {
        __builtin_memcpy(eth->h_source, mac, ETH_ALEN);

#ifdef DEBUG
        bpf_trace_printk("lb_mac (hi): %x:%x:%x", eth->h_source[0], eth->h_source[1], eth->h_source[2]);
        bpf_trace_printk("lb_mac (lo): %x:%x:%x", eth->h_source[3], eth->h_source[4], eth->h_source[5]);
#endif

    } else {
        // Unexpected

#ifdef DEBUG
        bpf_trace_printk("Can not find lb mac address");
#endif

        return XDP_PASS;
    }

    // Recalculate IP packet checksum
    ip->check = iph_csum(ip);

#ifdef DEBUG
    bpf_trace_printk("IP Checksum #1: 0x%x", ip->check);
#endif

    //ip->check = iph_csum(ip);
    //bpf_trace_printk("IP Checksum #2: 0x%x", ip->check);

    // Calculate UDP packet checksum (optional)
    udp->check = 0;
    //udp->check = caludpcsum(ip, udp, data_end);
    //bpf_trace_printk("UDP Checksum: 0x%x", udp->check);

#ifdef DEBUG
    unsigned char *payload = (unsigned char *)(udp + 1);
    bpf_trace_printk("UDP Data: %s", payload);
#endif


    if (*pktcnt % 100 == 0)
    {
        int time_delta = bpf_ktime_get_ns() - time_start;
#ifdef DEBUG
        bpf_trace_printk("time delta: %d", time_delta);
#endif
        struct event *event = rb.ringbuf_reserve(sizeof(struct event));
        if (!event) {
            bpf_trace_printk("Cannot allocate %d bytes from ring buffer\n", sizeof(struct event));
            return XDP_PASS;
        }
        event->pkt_size = pkt_size;
        event->time_delta = time_delta;

        // Send packet size to userspace ring buffer
        rb.ringbuf_submit(event, 0);

    }

    if (!spo || *spo == *fip) {
#ifdef DEBUG
        bpf_trace_printk("Source IP out not found, returning XDP_TX");
#endif
        return XDP_TX;
    } else {
        return tx_port.redirect_map(0, 0);
    }
}