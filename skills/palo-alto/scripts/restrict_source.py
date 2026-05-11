"""Source IP restriction skill — narrow a security rule's allowed sources.

Customer says: "RULE_108'e gelen trafik sadece 203.0.113.46 için açık olsun"
Claude invokes:

    PANOS_HOST=... PANOS_USERNAME=... PANOS_PASSWORD=... PANOS_INSECURE=1 \\
    python3 restrict_source.py --rule RULE_108 --source-ips 203.0.113.46

Or by destination match (when customer doesn't know the rule name):

    PANOS_HOST=... ... python3 restrict_source.py \\
        --match-dest 198.51.100.108 --match-port 80 --source-ips 203.0.113.46
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import sys
from typing import Optional

from config import load_config, ConfigError
from panos_client import (
    PanosClient, PanosError, CommitError, ObjectConflictError, NotOwnedError,
    AuthExpiredError,
)
from runtime import auto_update


log = logging.getLogger("restrict_source")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    if not verbose:
        logging.getLogger("pandevice").setLevel(logging.WARNING)
        logging.getLogger("panos").setLevel(logging.WARNING)


def _parse_source_ips(spec: str) -> list[str]:
    out = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            net = ipaddress.IPv4Network(raw, strict=False)
        except ValueError as e:
            raise SystemExit(f"invalid source IP/CIDR: {raw!r} — {e}")
        out.append(str(net) if net.prefixlen < 32 else str(net.network_address))
    if not out:
        raise SystemExit("--source-ips: no valid entries")
    return out


def _find_target_rule(client: PanosClient, args: argparse.Namespace):
    if args.rule:
        rule = client.get_security_rule(args.rule)
        if not rule:
            raise SystemExit(f"security rule {args.rule!r} not found")
        return rule

    try:
        ipaddress.IPv4Address(args.match_dest)
    except (ValueError, TypeError):
        raise SystemExit(f"--match-dest not a valid IPv4: {args.match_dest!r}")
    wan_addr_name = client.config.naming.wan_address(args.match_dest)
    wan_addr = client.get_address_by_name(wan_addr_name)
    if not wan_addr:
        wan_addr = client.find_address_by_value(f"{args.match_dest}/32")
        if not wan_addr:
            raise SystemExit(
                f"no address object found for WAN IP {args.match_dest} "
                f"(expected {wan_addr_name})"
            )
    candidates = client.find_security_rules_matching_destination(wan_addr.name)
    if not candidates:
        raise SystemExit(
            f"no security rule matches destination {wan_addr.name} ({args.match_dest})"
        )
    if args.match_port is None:
        if len(candidates) == 1:
            return candidates[0]
        raise SystemExit(
            f"multiple security rules match destination {wan_addr.name}: "
            f"{[r.name for r in candidates]}. Specify --match-port."
        )
    proto = args.match_protocol.lower()
    service_name_exact = client.config.naming.service(args.match_port, proto)
    filtered = [
        r for r in candidates
        if service_name_exact in (r.service or [])
        or any(_service_covers(client, svc, proto, args.match_port) for svc in (r.service or []))
    ]
    if not filtered:
        raise SystemExit(
            f"no security rule on {wan_addr.name} covers {proto}/{args.match_port}"
        )
    if len(filtered) > 1:
        raise SystemExit(
            f"multiple rules cover {wan_addr.name} {proto}/{args.match_port}: "
            f"{[r.name for r in filtered]}. Specify --rule explicitly."
        )
    return filtered[0]


def _service_covers(client: PanosClient, service_ref: str, proto: str, port: int) -> bool:
    from panos.objects import ServiceObject, ServiceGroup
    if service_ref in ("any", "application-default"):
        return True
    builtin = {
        "service-http": ("tcp", 80),
        "service-https": ("tcp", 443),
    }
    if service_ref in builtin:
        return builtin[service_ref] == (proto, port)
    obj = client.fw.find(service_ref, ServiceObject)
    if obj and obj.protocol and obj.destination_port:
        if obj.protocol.lower() != proto:
            return False
        return _port_str_contains(obj.destination_port, port)
    grp = client.fw.find(service_ref, ServiceGroup)
    if grp and grp.value:
        return any(_service_covers(client, m, proto, port) for m in grp.value)
    return False


def _port_str_contains(port_str: str, port: int) -> bool:
    for part in str(port_str).split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                if int(lo) <= port <= int(hi):
                    return True
            except ValueError:
                continue
        elif part.isdigit() and int(part) == port:
            return True
    return False


def do_restrict(args: argparse.Namespace) -> dict:
    cfg = load_config()
    client = PanosClient(cfg)
    client.refresh()
    rule = _find_target_rule(client, args)

    requested_sources: list[str] = []
    if args.source_region:
        regions = [r.strip() for r in args.source_region.split(",") if r.strip()]
        requested_sources.extend(regions)

    if args.source_ips:
        parsed = _parse_source_ips(args.source_ips)
        for cidr in parsed:
            ip_only = cidr.split("/")[0]
            existing = client.find_address_by_value(cidr)
            if existing:
                requested_sources.append(existing.name)
                continue
            new_name = _src_addr_name(ip_only)
            client.ensure_address(
                name=new_name,
                value=cidr,
                description=f"Source IP {cidr}",
            )
            requested_sources.append(new_name)

    if not requested_sources:
        raise SystemExit("no --source-ips or --source-region given")

    if len(requested_sources) > 1 and not args.no_group and args.source_ips:
        group_name = f"SRC-{rule.name}"
        region_names = [r.strip() for r in (args.source_region or "").split(",") if r.strip()]
        ip_members = [s for s in requested_sources if s not in region_names]
        if len(ip_members) > 1:
            client.ensure_address_group(group_name, ip_members,
                                        description=f"Allowed sources for {rule.name}")
            non_ip = [s for s in requested_sources if s not in ip_members]
            requested_sources = non_ip + [group_name]

    if args.append:
        existing = list(rule.source or [])
        existing = [s for s in existing if s != "any"]
        final_source = sorted(set(existing + requested_sources))
    else:
        final_source = sorted(set(requested_sources))

    plan = {
        "host": cfg.host,
        "rule": rule.name,
        "existing_source": list(rule.source or []),
        "new_source": final_source,
        "mode": "append" if args.append else "replace",
    }

    if list(rule.source or []) == final_source:
        plan["status"] = "no-change"
        return plan

    if args.dry_run:
        plan["dry_run"] = True
        plan["status"] = "would-apply"
        return plan

    try:
        client.update_security_rule_source(rule, final_source)
        commit_result = client.commit(
            description=f"restrict source on {rule.name} → {','.join(final_source)}"
        )
    except CommitError:
        raise
    except (ObjectConflictError, PanosError):
        log.error("update failed, reverting candidate")
        try:
            client.revert_candidate()
        except Exception as e:
            log.error("revert failed: %s", e)
        raise

    plan["status"] = "applied"
    plan["commit_job"] = commit_result.get("jobid") if isinstance(commit_result, dict) else None
    return plan


def _src_addr_name(ip: str) -> str:
    return "SRC_" + ip.replace(".", "-")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restrict source IPs/regions on a PAN-OS security rule."
    )
    sel = parser.add_argument_group("rule selection (use --rule OR --match-dest)")
    sel.add_argument("--rule", default=None, help="security rule name (e.g. RULE_108)")
    sel.add_argument("--match-dest", default=None,
                     help="WAN destination IPv4 of the rule")
    sel.add_argument("--match-port", type=int, default=None,
                     help="Port to disambiguate when multiple rules share destination")
    sel.add_argument("--match-protocol", default="tcp", choices=["tcp", "udp"])

    src = parser.add_argument_group("source spec")
    src.add_argument("--source-ips", default=None,
                     help="comma-separated IPv4 or CIDR (e.g. 1.2.3.4,5.6.7.0/24)")
    src.add_argument("--source-region", default=None,
                     help="comma-separated region codes (e.g. TR,US)")
    src.add_argument("--append", action="store_true",
                     help="add to existing source instead of replacing")
    src.add_argument("--no-group", action="store_true",
                     help="do not auto-create an address-group when >1 IP")

    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.rule and not args.match_dest:
        parser.error("must specify --rule or --match-dest")
    if args.rule and args.match_dest:
        parser.error("--rule and --match-dest are mutually exclusive")

    _setup_logging(args.verbose)
    auto_update()

    try:
        result = do_restrict(args)
    except ConfigError as e:
        _emit({"status": "error", "kind": "config", "error": str(e)})
        return 2
    except AuthExpiredError as e:
        _emit({"status": "error", "kind": "auth_expired", "error": str(e)})
        return 1
    except NotOwnedError as e:
        _emit({"status": "error", "kind": "not_owned",
               "error": str(e), "object_kind": e.kind, "object_name": e.name, "action": e.action})
        return 1
    except ObjectConflictError as e:
        _emit({"status": "error", "kind": "object_conflict", "error": str(e)})
        return 1
    except CommitError as e:
        _emit({"status": "error", "kind": "commit", "error": str(e)})
        return 1
    except (PanosError, RuntimeError) as e:
        _emit({"status": "error", "kind": "panos", "error": str(e)})
        return 1
    _emit(result)
    return 0


def _emit(obj: dict) -> None:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    sys.exit(main())
