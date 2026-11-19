# Week 6: Under the hood, and a VXLAN data centre overlay

| Folder | What's in it |
|---|---|
| [lab/](lab/) | Lab: Mininet hosts as namespaces, a network built by hand from namespaces, veth pairs and a bridge, a VXLAN tunnel over IPv4 and IPv6, an MTU black hole, Docker. Answers in [lab/README.md](lab/README.md), evidence in `part*.txt`. |
| [assignment/](assignment/) | Assignment: my own multi-tenant data centre (4 hypervisors, 3 tenants, red and blue overlapping on 10.40.50.0/24) built as a VXLAN overlay. |

## Assignment files

- `overlay.yaml`: my design, with fix B (underlay MTU 9000). `blackhole.yaml` is the all-1500 design. `mtu_a.yaml` (tenants 1450) and `mtu_b.yaml` (underlay 9000) are the two fixes.
- `build_overlay.py`: my `tenant_commands()` (bridge, VXLAN device with no remote, VM ports, one flood entry per other hypervisor) and `check_mtu()` (refuses a tenant whose MTU + 50 exceeds the underlay).
- `evidence6/`: check, flood and learn, isolation, black hole, and the two fixes measured.
- `make_page6.py` + `page6.yaml`: generate `docs/week6/index.html`.

Run in the course container, from `assignment/`:

```
python3 build_overlay.py blackhole.yaml     # refused by check_mtu(): nothing was built
python3 build_overlay.py overlay.yaml
python3 make_page6.py overlay.yaml
```
