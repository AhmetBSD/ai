"""Preview which WAN IPs / ports are free on the firewall.

Usage (creds via env vars):
    PANOS_HOST=... PANOS_USERNAME=... PANOS_PASSWORD=... PANOS_INSECURE=1 \\
    python3 free_ip.py [--port 80 --protocol tcp]
"""
from __future__ import annotations

import argparse
import json
import sys

from config import load_config, ConfigError
from discovery import build_wan_usage_map, get_wan_subnet, _firewall_own_wan_ip
from panos_client import PanosClient, PanosError


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=None,
                   help="Only show IPs that have this port free")
    p.add_argument("--protocol", default="tcp", choices=["tcp", "udp"])
    args = p.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as e:
        print(json.dumps({"status": "error", "kind": "config", "error": str(e)}, indent=2))
        return 2

    try:
        client = PanosClient(cfg)
        client.refresh()
        subnet = get_wan_subnet(client)
        own_ip = _firewall_own_wan_ip(client, subnet)
        usage = build_wan_usage_map(client, subnet)
    except (PanosError, RuntimeError) as e:
        print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return 1

    proto_key = args.protocol.lower() if args.port is not None else None
    target_tuple = (proto_key, args.port) if args.port is not None else None

    rows = []
    for ip, u in usage.items():
        if ip == own_ip:
            continue
        used_ports = sorted(
            [f"{proto}/{port}" for (proto, port) in u.used_ports if proto != "*"]
        )
        wildcard = ("*", "*") in u.used_ports
        is_target_free = True
        if target_tuple:
            if wildcard or target_tuple in u.used_ports:
                is_target_free = False
        rows.append({
            "ip": ip,
            "address_object": u.address_object,
            "nat_rules": list(u.nat_rules),
            "used_ports": used_ports,
            "service_any_consumes_all": wildcard,
            "fully_free": not u.nat_rules,
            "target_port_free": is_target_free if target_tuple else None,
        })
    rows.sort(key=lambda r: tuple(int(o) for o in r["ip"].split(".")))

    summary = {
        "host": cfg.host,
        "wan_subnet": str(subnet),
        "firewall_own_wan_ip": own_ip,
        "total_hosts": len(rows),
        "fully_free_count": sum(1 for r in rows if r["fully_free"]),
        "target": {
            "protocol": args.protocol,
            "port": args.port,
            "free_count": sum(1 for r in rows if r["target_port_free"]) if target_tuple else None,
        } if target_tuple else None,
        "hosts": rows,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
