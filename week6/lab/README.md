# Week 6 Lab: Under the hood: namespaces, veth, bridges, VXLAN and containers

My addresses: `NN = 40`, `NNNN = 3540`, so IPv4 `10.40.0.0/16` and IPv6 `fd00:3540::/32`.

Everything below is real output from my machine. Parts 2–6 ran in the course container (`firdaussahran/netlab-mininet:1.0`). Part 1 ran with `sudo mn` on the laptop VM, and Part 7 ran with Docker on the laptop.

| File | What it shows |
|---|---|
| [part1-namespaces.txt](part1-namespaces.txt) | `dump` PIDs match the `lsns` network namespaces; veth ends inside and outside h1 |
| [part2-bridge.txt](part2-bridge.txt) | Hand-built red/blue namespaces + bridge: ping and learned MACs |
| [part3-vxlan-capture.txt](part3-vxlan-capture.txt) | tcpdump of VXLAN (VNI 100 and VNI 106) on h2's underlay |
| [part4-mtu-throughput.txt](part4-mtu-throughput.txt) | Tunnel MTU 1450, DF pings 1422/1423, iperf3 underlay vs overlay |
| [part5-ipv6-underlay.txt](part5-ipv6-underlay.txt) | VNI 106 over an IPv6 underlay: MTU 1430, DF pings 1402/1403 |
| [part6-mtu-mismatch.txt](part6-mtu-mismatch.txt) | Underlay MTU black hole: symptoms, size search, drop counters, fix |
| [part7-docker.txt](part7-docker.txt) | Alpine container: veth `eth0@if10`, own filesystem, own PID namespace |

## 1. The Linux building blocks behind a Mininet host and cable

- **Host = a network namespace held open by a bash process.**
  - `dump` shows `h1 … pid=2868` and `h2 … pid=2870`.
  - `lsns -t net` shows those same PIDs, `bash --norc --noediting -is mininet:h1` and `mininet:h2`, each owning its own namespace (`4026532725` and `4026532824`).
  - The namespace gives the host its own interfaces, addresses, routing table and ARP/neighbour table. Everything else (filesystem, processes, users) is shared with the laptop.
- **Cable = a veth pair**: two virtual interfaces joined back to back.
  - Inside h1 you see only `lo` and `h1-eth0@if5`.
  - In the root namespace you see the other ends, `s1-eth1@if2` and `s1-eth2@if2`. The `@ifN` is the index of the peer interface in the other namespace.
- **Switch = a Linux bridge** (`--switch lxbr`).
  - `s1` is a bridge in the root namespace with the `s1-ethX` ends enslaved to it.
  - It has no namespace of its own, which is why it doesn't appear in `lsns`.

Part 2 confirms this. Two namespaces, two veth pairs and `br0` give a working ping (`10.40.9.1 → 10.40.9.2`, 0% loss). `bridge fdb show` then lists the two learned (non-permanent) MACs: `42:a4:c6:e7:47:d7` on `red-br` and `46:6b:e5:92:48:12` on `blue-br`. That is exactly Week 1's network without Mininet.

## 2. MTU and throughput: underlay vs overlay

| | Underlay `h1-eth0` | Overlay `vxlan100` (IPv4 underlay) | Overlay `vxlan106` (IPv6 underlay) |
|---|---|---|---|
| Interface MTU | 1500 | 1450 | 1430 |
| Largest DF ping `-s` | — | 1422 works, 1423 → `mtu=1450` | 1402 works, 1403 → `mtu=1430` |
| iperf3 receiver | **70.3 Gbit/s** | **44.2 Gbit/s** | not measured |

The overlay reached about **63%** of the underlay rate, roughly 37% slower. Every packet is encapsulated at h1 and decapsulated at h2, and each one carries 50 fewer payload bytes. The absolute numbers are huge because nothing leaves the laptop. The ratio is what matters.

**Where the 50 bytes go (IPv4 underlay).** A 1450-byte inner IP packet becomes a 1500-byte outer IP packet, which is exactly `h1-eth0`'s MTU:

| Header added | Bytes | Why |
|---|---|---|
| Inner Ethernet header | 14 | VXLAN carries whole Layer-2 frames, so the inner frame's dst MAC, src MAC and EtherType go inside |
| VXLAN header | 8 | Flags + 24-bit VNI (100) + reserved |
| Outer UDP header | 8 | src port (hash of the inner flow), dst port 4789, length, checksum |
| Outer IPv4 header | 20 | `10.40.0.1 → 10.40.0.2` |
| **Total** | **50** | 1500 − 50 = 1450 |

The outer Ethernet header (14 bytes) isn't counted, because MTU measures the IP packet and not the frame.

The DF pings confirm the limit. `-s 1422` + 8 (ICMP) + 20 (inner IPv4) = 1450 fits, and 1423 gives 1451, which the kernel refuses locally.

**Why IPv6 costs 70.** The IPv6 header is a fixed 40 bytes, against IPv4's usual 20. Everything else is the same: 14 + 8 + 8 + 40 = **70**, so the tunnel MTU is 1500 − 70 = **1430**. The pings match: 1402 + 28 = 1430 works and 1403 doesn't.

## 3. Why small pings kept working while TCP almost stopped

After `h2 ip link set h2-eth0 mtu 1400`, nothing told h1 or the tunnel about the change. `vxlan100` still believed it could send 1450-byte packets.

- **The limit is set by size, not by traffic type.**
  - The largest DF ping that still worked was `-s 1326`. That's 1326 + 28 = 1354 inside the tunnel, and 1354 + 50 = **1404** on the underlay: h2's 1400 plus the 4 bytes Linux allows on receive for a VLAN tag.
  - Anything larger is dropped. Both counters rose by exactly 3 for 3 large pings: `s1-eth2 tx_dropped` 14 → 17 and `h2-eth0 rx_dropped` 14 → 17. The switch sends the frame towards h2, and h2's interface discards it as it arrives.
- **Small pings** (84 bytes inside, 134 on the wire) are far below 1404, so they arrive normally.
- **TCP sends full-size segments.**
  - Its MSS comes from h1's 1450 tunnel MTU, so data segments are 1450 bytes inside the tunnel and 1500 on the underlay. Every one of them is silently dropped.
  - Only the small packets get through: SYN, ACKs and the first bits of data. That's why iperf3 moved 1 MB in the first second and then `0.00 bits/sec`, with retransmissions and a congestion window collapsed to `1.37 KBytes` (receiver average 629 Kbit/s, against 44 Gbit/s before).
- **No error ever comes back.**
  - Path MTU Discovery relies on a router sending ICMP "fragmentation needed" (or IPv6 "packet too big").
  - Here the packet is dropped by a receiving NIC at Layer 2, not by a router, so no ICMP is generated. TCP keeps retransmitting segments that can never fit.
  - The result is a black hole that only swallows big packets.

Setting h2's MTU back to 1500 fixed it. The 1400-byte ping works again, and iperf3 was back to 46.3 Gbit/s.

(Linux also stops the reverse mistake: `ip link set vxlan100 mtu 1500` is refused, because a tunnel MTU can't exceed what its underlay device carries. A switch or router elsewhere on the path has no such check, which is why this failure happens in real networks.)

## 4. What a Docker container has that a Mininet host doesn't

Both get their network the same way. The Alpine container's `eth0@if10` is one end of a veth pair whose other end sits on Docker's `docker0` bridge, just like Part 2. On top of that, a container has:

- **Its own root filesystem from an image.** `/etc/os-release` says *Alpine Linux v3.20*, not the laptop's OS. A Mininet host sees the laptop's filesystem.
- **Its own PID namespace.** `ps` lists only `sleep 600` as **PID 1** and `ps` itself. In a Mininet host, `ps` shows every process on the laptop.
- **Its own mount, UTS (hostname) and IPC namespaces, and cgroup limits**, all managed by Docker as one unit that can be pulled, started, stopped and removed (`docker rm -f c1`).

So a container is a set of namespaces plus a filesystem image. That's why a router's software can be shipped as a container and run anywhere.

## 5. VXLAN vs my Week 5 P4 tunnel

| | VXLAN (this lab) | Week 5 P4 tunnel |
|---|---|---|
| Bytes added | 50 over IPv4 (Ethernet 14 + VXLAN 8 + UDP 8 + IPv4 20), 70 over IPv6 | 4: one custom `myTunnel` header (`proto_id` 16 bits + `dst_id` 16 bits) inserted between Ethernet and IPv4 |
| Devices that must understand it | Only the two end points (VTEPs h1 and h2). `s1`, and any router in between, sees an ordinary UDP/IP packet. s1 forwarded it without knowing it was a tunnel | Every switch on the path. Each P4 switch has to parse the custom EtherType and forward on `dst_id`, and a normal switch or router would not know what to do with it |
| What stops a packet looping forever | The **outer IP TTL / hop limit**, which every router on the underlay decrements and drops at 0 (at Layer 2, a loop-free topology or STP). The inner TTL is not touched inside the tunnel | **Nothing in the header.** `myTunnel` has no TTL, and the tunnel path doesn't decrement the inner IPv4 TTL. Only correct `dst_id` table entries prevent a loop, so a bad entry would circulate the packet forever |

The trade-off: VXLAN is costly per packet but deployable over any IP network. The P4 tunnel is tiny but only works where every hop is programmable and correctly programmed.

## AI use

I used AI assistance to write up this lab. It gave me a step-by-step guide and drafted this README from the output I captured. I checked the numbers and explanations against my own output.
