"""DNAT skill — destination NAT (port forwarding) entry point.

Customer says: "198.51.100.108'in 80 portunu 192.168.1.50:90'a yönlendir"
Claude parses the sentence and invokes:

    PANOS_HOST=...  PANOS_USERNAME=... PANOS_PASSWORD=...  PANOS_INSECURE=1 \\
    python3 dnat.py --wan-ip 198.51.100.108 --public-port 80 \\
                    --target-ip 192.168.1.50 --target-port 90

Credentials live only in env vars for the duration of this process.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import sys
from typing import Optional

from config import load_config, ConfigError
from discovery import (
    find_dnat_wan_candidate,
    WanSubnetMismatch, PortConflict,
)
from panos_client import (
    PanosClient, PanosError, CommitError, ObjectConflictError, NotOwnedError,
)


log = logging.getLogger("dnat")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,  # keep stdout clean for JSON output
    )
    if not verbose:
        logging.getLogger("pandevice").setLevel(logging.WARNING)
        logging.getLogger("panos").setLevel(logging.WARNING)


def _validate_args(args: argparse.Namespace) -> None:
    if args.remove:
        return
    if not (1 <= args.public_port <= 65535):
        raise SystemExit(f"--public-port out of range: {args.public_port}")
    if not (1 <= args.target_port <= 65535):
        raise SystemExit(f"--target-port out of range: {args.target_port}")
    if args.protocol not in ("tcp", "udp"):
        raise SystemExit(f"--protocol must be tcp or udp")
    try:
        ipaddress.IPv4Address(args.target_ip)
    except (ValueError, TypeError):
        raise SystemExit(f"--target-ip not a valid IPv4: {args.target_ip!r}")
    if args.wan_ip:
        try:
            ipaddress.IPv4Address(args.wan_ip)
        except (ValueError, TypeError):
            raise SystemExit(f"--wan-ip not a valid IPv4: {args.wan_ip!r}")


def do_dnat(args: argparse.Namespace) -> dict:
    cfg = load_config()
    client = PanosClient(cfg)
    client.refresh()

    candidate = find_dnat_wan_candidate(
        client,
        requested_port=args.public_port,
        protocol=args.protocol,
        explicit_wan_ip=args.wan_ip,
    )
    log.info(
        "WAN candidate: %s (%s) — address-obj=%s exists=%s",
        candidate.ip, candidate.reason, candidate.address_object_name,
        candidate.address_object_exists,
    )

    wan_addr_name = candidate.address_object_name
    lan_addr_name = cfg.naming.lan_address(args.target_ip)
    service_name = cfg.naming.service(args.public_port, args.protocol)

    suffix = ""
    if candidate.reason == "port-multiplex":
        suffix = f"{args.protocol.upper()}{args.public_port}"
    nat_rule_name = cfg.naming.nat_rule(candidate.ip, suffix=suffix)
    sec_rule_name = cfg.naming.security_rule(candidate.ip, suffix=suffix)

    # Idempotency early-exit. After we know the exact rule name (suffix-aware),
    # check if a rule already exists with identical parameters and short-circuit.
    existing_nat = client.get_nat_rule(nat_rule_name)
    if existing_nat is not None:
        from panos_client import _diff_nat_rule
        diff = _diff_nat_rule(
            existing_nat,
            wan_address_name=wan_addr_name,
            service_name=service_name,
            translated_address_name=lan_addr_name,
            translated_port=args.target_port,
            from_zones=[cfg.wan_zone],
            to_zone=cfg.wan_zone,
            to_interface=cfg.wan_interface,
        )
        if diff is None:
            return {
                "host": cfg.host,
                "wan_ip": candidate.ip,
                "wan_address_object": wan_addr_name,
                "target_ip": args.target_ip,
                "target_address_object": lan_addr_name,
                "service_object": service_name,
                "protocol": args.protocol,
                "public_port": args.public_port,
                "target_port": args.target_port,
                "nat_rule": nat_rule_name,
                "security_rule": sec_rule_name,
                "selection_reason": "already-applied",
                "status": "no-change",
            }

    plan = {
        "host": cfg.host,
        "wan_ip": candidate.ip,
        "wan_address_object": wan_addr_name,
        "target_ip": args.target_ip,
        "target_address_object": lan_addr_name,
        "service_object": service_name,
        "protocol": args.protocol,
        "public_port": args.public_port,
        "target_port": args.target_port,
        "nat_rule": nat_rule_name,
        "security_rule": sec_rule_name,
        "from_zones": [cfg.wan_zone],
        "to_zone_nat": cfg.wan_zone,
        "to_zone_security": cfg.lan_zone,
        "to_interface": cfg.wan_interface,
        "selection_reason": candidate.reason,
    }

    if args.dry_run:
        plan["dry_run"] = True
        plan["status"] = "would-apply"
        return plan

    try:
        client.ensure_address(
            name=wan_addr_name,
            value=f"{candidate.ip}/32",
            description=f"WAN host {candidate.ip}",
        )
        client.ensure_address(
            name=lan_addr_name,
            value=f"{args.target_ip}/32",
            description=args.description or f"Internal host {args.target_ip}",
        )
        client.ensure_service(
            name=service_name,
            protocol=args.protocol,
            port=args.public_port,
        )
        client.ensure_nat_rule_dnat(
            name=nat_rule_name,
            wan_address_name=wan_addr_name,
            service_name=service_name,
            translated_address_name=lan_addr_name,
            translated_port=args.target_port,
            from_zones=[cfg.wan_zone],
            to_zone=cfg.wan_zone,
            to_interface=cfg.wan_interface,
            description=args.description or "",
        )
        client.ensure_security_rule(
            name=sec_rule_name,
            from_zones=[cfg.wan_zone],
            to_zones=[cfg.lan_zone],
            source=["any"],
            destination=[wan_addr_name],
            service=[service_name],
            application=["any"],
            action="allow",
            description=args.description or "",
            log_end=True,
            profile_setting=cfg.security_profiles,
            profile_group=cfg.security_profile_group or None,
        )
    except (ObjectConflictError, PanosError):
        log.error("object create failed, reverting candidate")
        try:
            client.revert_candidate()
        except Exception as e:
            log.error("revert failed: %s", e)
        raise

    try:
        commit_result = client.commit(
            description=args.description or
            f"dnat {candidate.ip}:{args.public_port} -> {args.target_ip}:{args.target_port}"
        )
    except CommitError:
        raise

    if isinstance(commit_result, dict) and commit_result.get("result") == "committing":
        plan["status"] = "committing"
        plan["commit_note"] = commit_result.get("message")
    else:
        plan["status"] = "applied"
        plan["commit_job"] = commit_result.get("jobid") if isinstance(commit_result, dict) else None
    return plan


def do_remove(args: argparse.Namespace) -> dict:
    cfg = load_config()
    client = PanosClient(cfg)
    client.refresh()
    target = args.remove
    nat = client.get_nat_rule(target)
    if not nat:
        return {"host": cfg.host, "rule": target, "status": "not-found"}

    # Hard isolation: skill only operates on its own rules. If the NAT rule
    # has no [skill-managed] marker, refuse — the operator owns it.
    from panos_client import _is_skill_managed
    if not _is_skill_managed(nat):
        raise NotOwnedError("NatRule", target, "silme")

    sec_prefix = cfg.naming.security_rule_template.split("{")[0]
    nat_prefix = cfg.naming.nat_rule_template.split("{")[0]
    sec_name = target.replace(nat_prefix, sec_prefix, 1) if nat_prefix != sec_prefix else None
    sec = client.get_security_rule(sec_name) if sec_name and sec_name != target else None
    # If a paired security rule exists but is operator-owned, leave it alone —
    # the NAT side is ours to remove, the security side belongs to the operator.
    if sec is not None and not _is_skill_managed(sec):
        sec = None

    # Collect candidate-orphan object names from the rules we are about to
    # delete. Skill marker + zero-reference check happens AFTER deletion.
    from panos_client import _as_list as _al
    candidate_names: set = set()
    for v in (_al(nat.source) + _al(nat.destination) + _al(nat.service)
              + [nat.destination_translated_address,
                 getattr(nat, "source_translation_ip_address", None)]
              + _al(getattr(nat, "source_translation_translated_addresses", None))):
        if v: candidate_names.add(v)
    if sec:
        candidate_names.update(_al(sec.source))
        candidate_names.update(_al(sec.destination))
        candidate_names.update(_al(sec.service))
    candidate_names.discard("any"); candidate_names.discard("application-default")

    sec_deleted = False
    if sec:
        sec.delete(); sec_deleted = True
    nat.delete()

    # Now that NAT + SEC are removed from the in-memory tree, the orphan
    # scan counts references in the remaining rules ONLY.
    cleanup = client.cleanup_orphan_managed(candidate_names)

    if args.dry_run:
        return {
            "status": "would-remove",
            "nat": target,
            "security": sec_name if sec_deleted else None,
            "orphan_candidates": sorted(candidate_names),
            "would_delete": cleanup["deleted"],
            "would_skip":   cleanup["skipped"],
        }

    commit_result = client.commit(description=f"remove dnat {target} + GC")
    out = {
        "host": cfg.host,
        "nat_rule": target,
        "security_rule": sec_name if sec_deleted else None,
        "deleted_orphans": cleanup["deleted"],
        "kept_objects":    cleanup["skipped"],
    }
    if isinstance(commit_result, dict) and commit_result.get("result") == "committing":
        out["status"] = "removing"
        out["commit_note"] = commit_result.get("message")
    else:
        out["status"] = "removed"
        out["commit_job"] = commit_result.get("jobid") if isinstance(commit_result, dict) else None
    return out


def do_update(args: argparse.Namespace) -> dict:
    """Repoint an existing NAT rule's inside destination to a new IP/port.

    Allowed on BOTH skill-managed and operator-owned rules. The rule's
    identity (name, WAN side, zones, source filters, service, security
    policy) is preserved — only destination_translated_address and
    destination_translated_port change.
    """
    cfg = load_config()
    client = PanosClient(cfg)
    client.refresh()
    target = args.update
    nat = client.get_nat_rule(target)
    if not nat:
        return {"host": cfg.host, "rule": target, "status": "not-found"}

    from panos_client import _is_skill_managed
    skill_owned = _is_skill_managed(nat)

    # Capture the old destination so we can attempt orphan cleanup afterwards
    # (only if our marker is on it — operator objects stay).
    old_dest_addr = nat.destination_translated_address
    try:
        old_dest_port = int(nat.destination_translated_port) if nat.destination_translated_port else None
    except (ValueError, TypeError):
        old_dest_port = None

    new_addr_name = cfg.naming.lan_address(args.target_ip)
    plan = {
        "host": cfg.host,
        "rule": target,
        "rule_owned_by": "skill" if skill_owned else "operator",
        "old_destination": f"{old_dest_addr}:{old_dest_port}" if old_dest_addr else None,
        "new_destination": f"{new_addr_name}:{args.target_port}",
        "new_target_address": new_addr_name,
        "new_target_ip": args.target_ip,
        "new_target_port": args.target_port,
    }

    if args.dry_run:
        plan["dry_run"] = True
        plan["status"] = "would-apply"
        return plan

    # Ensure the new address object exists (created with [skill-managed] marker).
    try:
        client.ensure_address(
            name=new_addr_name,
            value=f"{args.target_ip}/32",
            description=args.description or f"Internal host {args.target_ip}",
        )
        client.update_nat_rule_destination(
            rule=nat,
            translated_address_name=new_addr_name,
            translated_port=args.target_port,
        )
    except (ObjectConflictError, NotOwnedError, PanosError):
        log.error("update failed, reverting candidate")
        try:
            client.revert_candidate()
        except Exception as e:
            log.error("revert failed: %s", e)
        raise

    # Try to clean up the old destination address if it was skill-managed and
    # no longer referenced anywhere. Operator-created objects are skipped.
    cleanup = {"deleted": [], "skipped": []}
    if old_dest_addr and old_dest_addr != new_addr_name:
        # Refresh in-memory state so count_references sees the updated rule.
        client.refresh()
        cleanup = client.cleanup_orphan_managed([old_dest_addr])

    commit_result = client.commit(
        description=f"update dnat {target} -> {new_addr_name}:{args.target_port}"
    )
    plan["deleted_orphans"] = cleanup["deleted"]
    plan["kept_objects"]    = cleanup["skipped"]
    if isinstance(commit_result, dict) and commit_result.get("result") == "committing":
        plan["status"] = "updating"
        plan["commit_note"] = commit_result.get("message")
    else:
        plan["status"] = "updated"
        plan["commit_job"] = commit_result.get("jobid") if isinstance(commit_result, dict) else None
    return plan


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or remove a DNAT (port forwarding) on PAN-OS."
    )
    parser.add_argument("--public-port", type=int,
                        help="External port number (1-65535)")
    parser.add_argument("--target-ip",
                        help="Inside server IPv4 to receive traffic")
    parser.add_argument("--target-port", type=int,
                        help="Inside server port (1-65535)")
    parser.add_argument("--wan-ip", default=None,
                        help="Specific WAN IPv4 to use (default: auto-pick free)")
    parser.add_argument("--protocol", default="tcp", choices=["tcp", "udp"])
    parser.add_argument("--description", default="",
                        help="Free-text description for objects/rules")
    parser.add_argument("--remove", default=None,
                        help="Remove this NAT rule (and matching security rule) instead. "
                             "Only allowed on skill-managed rules.")
    parser.add_argument("--update", default=None,
                        help="Repoint an existing NAT rule's inside destination "
                             "(target IP/port). Allowed on operator-owned rules too "
                             "— only the destination changes; everything else stays.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan, do not change firewall")
    parser.add_argument("--keygen", action="store_true",
                        help="Generate a fresh PAN-OS API key from username+password "
                             "and print it. Use when api-key-lifetime is short and you "
                             "want a single short-lived key per Claude session.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if sum(1 for x in [args.remove, args.update, args.keygen] if x) > 1:
        parser.error("--remove, --update, --keygen are mutually exclusive")

    _setup_logging(args.verbose)

    if args.keygen:
        try:
            cfg = load_config()
        except ConfigError as e:
            _emit({"status": "error", "kind": "config", "error": str(e)})
            return 2
        if not (cfg.username and cfg.password):
            _emit({"status": "error", "kind": "config",
                   "error": "--keygen needs PANOS_USERNAME and PANOS_PASSWORD env vars"})
            return 2
        client = PanosClient(cfg)
        try:
            # Force a real auth round-trip: pan-os-python lazy-fetches the key on
            # the first XML API request. A cheap op call (`<show><clock>`) does it.
            client.fw.op("<show><clock/></show>", cmd_xml=False)
            api_key = client.fw.api_key
        except Exception as e:
            _emit({"status": "error", "kind": "auth", "error": str(e)})
            return 1
        _emit({"status": "ok", "host": cfg.host, "api_key": api_key,
               "hint": "export PANOS_API_KEY='<api_key>' to use it for subsequent calls"})
        return 0

    if args.remove:
        try:
            result = do_remove(args)
        except ConfigError as e:
            _emit({"status": "error", "error": str(e), "kind": "config"})
            return 2
        except NotOwnedError as e:
            _emit({"status": "error", "kind": "not_owned",
                   "error": str(e), "object_kind": e.kind, "object_name": e.name, "action": e.action})
            return 1
        except (PanosError, RuntimeError) as e:
            _emit({"status": "error", "error": str(e), "kind": "panos"})
            return 1
        _emit(result)
        return 0 if result.get("status") in ("removed", "removing", "would-remove") else 1

    if args.update:
        if not (args.target_ip and args.target_port):
            parser.error("--update requires --target-ip and --target-port")
        try:
            ipaddress.IPv4Address(args.target_ip)
        except (ValueError, TypeError):
            raise SystemExit(f"--target-ip not a valid IPv4: {args.target_ip!r}")
        if not (1 <= args.target_port <= 65535):
            raise SystemExit(f"--target-port out of range: {args.target_port}")
        try:
            result = do_update(args)
        except ConfigError as e:
            _emit({"status": "error", "error": str(e), "kind": "config"})
            return 2
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
        return 0 if result.get("status") in ("updated", "updating", "would-apply") else 1

    if not (args.public_port and args.target_ip and args.target_port):
        parser.error("--public-port, --target-ip, --target-port required (or use --remove / --update)")
    _validate_args(args)

    try:
        result = do_dnat(args)
    except ConfigError as e:
        _emit({"status": "error", "error": str(e), "kind": "config"})
        return 2
    except WanSubnetMismatch as e:
        _emit({
            "status": "error", "kind": "wan_subnet_mismatch",
            "error": str(e),
            "given_ip": e.ip, "wan_subnet": str(e.subnet),
        })
        return 1
    except PortConflict as e:
        _emit({
            "status": "error", "kind": "port_conflict",
            "error": str(e),
            "wan_ip": e.ip, "protocol": e.proto, "port": e.port,
            "conflicting_rules": e.rules,
        })
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
