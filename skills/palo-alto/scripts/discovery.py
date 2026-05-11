"""Free WAN IP / free port discovery against live PAN-OS config.

Strategy:
  1. Validate any user-supplied WAN IP belongs to the firewall's WAN subnet.
  2. Refuse the firewall's own interface IP.
  3. If no WAN IP supplied: prefer an IP with NO existing NAT rules; otherwise
     port-multiplex on already-used IPs.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Optional

from panos.network import EthernetInterface
from panos.objects import AddressObject
from panos.policies import NatRule

from panos_client import PanosClient, _as_list, _normalize_ip_netmask


log = logging.getLogger("discovery")


@dataclass(frozen=True)
class WanUsage:
    ip: str
    address_object: Optional[str]
    used_ports: frozenset
    nat_rules: tuple


@dataclass(frozen=True)
class WanCandidate:
    ip: str
    address_object_name: str
    address_object_exists: bool
    reason: str  # "fully-free" | "port-multiplex" | "explicit"


class WanSubnetMismatch(RuntimeError):
    """Raised when a customer supplies a WAN IP outside the firewall's WAN subnet."""

    def __init__(self, ip: str, subnet: ipaddress.IPv4Network):
        self.ip = ip
        self.subnet = subnet
        super().__init__(
            f"{ip} firewall'unuzun WAN subnet'i ({subnet}) içinde değil. "
            f"Bu IP DNAT için kullanılamaz."
        )


class PortConflict(RuntimeError):
    """Raised when requested (ip, port, proto) is already consumed."""

    def __init__(self, ip: str, proto: str, port: int, rules: list[str], detail: str = ""):
        self.ip = ip
        self.proto = proto
        self.port = port
        self.rules = rules
        msg = (
            f"{ip} üzerinde {proto.upper()}/{port} zaten kullanımda. "
            f"Çakışan NAT rule: {', '.join(rules)}."
        )
        if detail:
            msg += f" {detail}"
        super().__init__(msg)


def get_wan_subnet(client: PanosClient) -> ipaddress.IPv4Network:
    """Return the CIDR covering the firewall's WAN interface."""
    if client.config.wan_subnet:
        return client.config.wan_subnet

    EthernetInterface.refreshall(client.fw)
    iface = client.fw.find(client.config.wan_interface, EthernetInterface)
    if not iface:
        raise RuntimeError(
            f"WAN interface {client.config.wan_interface!r} not found on firewall"
        )
    ip_values = _as_list(iface.ip)
    if not ip_values:
        raise RuntimeError(
            f"WAN interface {client.config.wan_interface!r} has no IPv4 assigned"
        )
    primary = ip_values[0]
    resolved = _resolve_to_cidr(client, primary)
    if not resolved:
        raise RuntimeError(f"Cannot resolve WAN interface IP {primary!r} to a CIDR")
    return ipaddress.IPv4Network(resolved, strict=False)


def _resolve_to_cidr(client: PanosClient, value: str) -> Optional[str]:
    if "/" in value and value.replace(".", "").split("/")[0].isdigit():
        return value
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    obj = client.get_address_by_name(value)
    if obj and obj.type == "ip-netmask":
        return _normalize_ip_netmask(obj.value)
    return None


def _firewall_own_wan_ip(client: PanosClient, subnet: ipaddress.IPv4Network) -> Optional[str]:
    EthernetInterface.refreshall(client.fw)
    iface = client.fw.find(client.config.wan_interface, EthernetInterface)
    if not iface:
        return None
    for value in _as_list(iface.ip):
        cidr = _resolve_to_cidr(client, value)
        if not cidr:
            continue
        try:
            addr = ipaddress.IPv4Interface(cidr).ip
        except ValueError:
            continue
        if addr in subnet:
            return str(addr)
    return None


def _resolve_address_obj_ip(client: PanosClient, name: str) -> Optional[str]:
    obj = client.get_address_by_name(name)
    if not obj or obj.type != "ip-netmask" or not obj.value:
        return None
    try:
        iface = ipaddress.IPv4Interface(obj.value)
    except ValueError:
        return None
    if iface.network.prefixlen == 32:
        return str(iface.ip)
    return None


_TRANSLATED_PORT_RE = re.compile(r"^\d+$")


def _extract_nat_rule_destination_ips(
    client: PanosClient, rule: NatRule, subnet: ipaddress.IPv4Network
) -> list[str]:
    out = []
    for dest_name in _as_list(rule.destination):
        ip = _resolve_address_obj_ip(client, dest_name)
        if ip and ipaddress.IPv4Address(ip) in subnet:
            out.append(ip)
    return out


def _extract_nat_rule_ports(client: PanosClient, rule: NatRule) -> set:
    from panos.objects import ServiceObject, ServiceGroup
    BUILTIN = {
        "service-http": [("tcp", 80)],
        "service-https": [("tcp", 443)],
    }
    result: set = set()
    services = _as_list(rule.service) or ["any"]
    for svc in services:
        if svc == "any":
            result.add(("*", "*"))
            return result
        if svc == "application-default":
            result.add(("*", "*"))
            return result
        if svc in BUILTIN:
            result.update(BUILTIN[svc])
            continue
        svc_obj = client.fw.find(svc, ServiceObject)
        if svc_obj and svc_obj.protocol and svc_obj.destination_port:
            for port in _parse_ports(svc_obj.destination_port):
                result.add((svc_obj.protocol.lower(), port))
            continue
        grp = client.fw.find(svc, ServiceGroup)
        if grp and grp.value:
            for member in grp.value:
                if member in BUILTIN:
                    result.update(BUILTIN[member])
                    continue
                m_obj = client.fw.find(member, ServiceObject)
                if m_obj and m_obj.protocol and m_obj.destination_port:
                    for port in _parse_ports(m_obj.destination_port):
                        result.add((m_obj.protocol.lower(), port))
    return result


def _parse_ports(port_str: str) -> list[int]:
    """Parse PAN-OS service port string: '80' | '80,443' | '7000-7010'."""
    out = []
    for part in str(port_str).split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                out.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        elif part.isdigit():
            out.append(int(part))
    return out


def build_wan_usage_map(
    client: PanosClient, subnet: ipaddress.IPv4Network
) -> dict[str, WanUsage]:
    client._require_refreshed()

    wan_addr_by_ip: dict[str, str] = {}
    for obj in client.fw.findall(AddressObject):
        if obj.type != "ip-netmask" or not obj.value:
            continue
        try:
            iface = ipaddress.IPv4Interface(obj.value)
        except ValueError:
            continue
        if iface.network.prefixlen != 32:
            continue
        if iface.ip in subnet:
            wan_addr_by_ip[str(iface.ip)] = obj.name

    used_by_ip: dict[str, set] = {}
    rules_by_ip: dict[str, list[str]] = {}
    for rule in client.list_nat_rules():
        ips = _extract_nat_rule_destination_ips(client, rule, subnet)
        if not ips:
            continue
        ports = _extract_nat_rule_ports(client, rule)
        for ip in ips:
            used_by_ip.setdefault(ip, set()).update(ports)
            rules_by_ip.setdefault(ip, []).append(rule.name)

    result: dict[str, WanUsage] = {}
    for ip_obj in subnet.hosts():
        ip = str(ip_obj)
        result[ip] = WanUsage(
            ip=ip,
            address_object=wan_addr_by_ip.get(ip),
            used_ports=frozenset(used_by_ip.get(ip, set())),
            nat_rules=tuple(rules_by_ip.get(ip, ())),
        )
    return result


def find_dnat_wan_candidate(
    client: PanosClient,
    requested_port: int,
    protocol: str,
    explicit_wan_ip: Optional[str] = None,
) -> WanCandidate:
    """Choose a WAN IP for a new DNAT.

    Raises:
      WanSubnetMismatch: explicit_wan_ip is outside the WAN subnet.
      PortConflict: the (ip, proto, port) tuple is already consumed.
      RuntimeError: no candidate available in the subnet.

    Note: the firewall's own WAN interface IP is allowed as an explicit DNAT
    target (single-IP deployments are common). It is still de-prioritised by
    auto-pick because it almost always has services bound (GP, SSL-VPN, etc.).
    """
    subnet = get_wan_subnet(client)
    own_ip = _firewall_own_wan_ip(client, subnet)
    usage = build_wan_usage_map(client, subnet)
    proto = protocol.lower()
    target_key = (proto, requested_port)

    def _blocking_rules(u: WanUsage) -> list[str]:
        """Names of NAT rules that block target_key on this IP."""
        if ("*", "*") in u.used_ports:
            return list(u.nat_rules)
        if target_key in u.used_ports:
            # Find which rules cover target_key. We don't store per-rule port
            # detail, so report all rules on the IP — caller can refine.
            return list(u.nat_rules)
        return []

    if explicit_wan_ip:
        ip = explicit_wan_ip
        try:
            ip_addr = ipaddress.IPv4Address(ip)
        except ValueError:
            raise RuntimeError(f"{ip!r} geçerli bir IPv4 adresi değil")
        if ip_addr not in subnet:
            raise WanSubnetMismatch(ip, subnet)
        # Firewall's own WAN IP is allowed (common single-IP deployments).
        # Any actual conflict on this IP is caught by the port-conflict check
        # below — e.g. GlobalProtect already using 443.
        u = usage.get(ip)
        if u:
            blockers = _blocking_rules(u)
            if blockers:
                detail = ""
                if ("*", "*") in u.used_ports:
                    detail = (
                        "Bu kural service=any veya application-default kullandığı için "
                        "tüm portları tutuyor."
                    )
                raise PortConflict(ip, proto, requested_port, blockers, detail=detail)
        name = u.address_object if u else None
        # If this IP already hosts other NAT rules, future rules MUST use a
        # unique name — fall through to port-multiplex naming (RULE{octet}-{P}).
        # Otherwise the caller's predicted RULE{octet} can collide with an
        # unrelated existing rule on the same IP (common on multi-service IPs).
        reason = "port-multiplex" if (u and u.nat_rules) else "explicit"
        return WanCandidate(
            ip=ip,
            address_object_name=name or client.config.naming.wan_address(ip),
            address_object_exists=bool(name),
            reason=reason,
        )

    # Auto-pick: phase 1 — fully-free
    candidates_free = [
        u for u in usage.values()
        if u.ip != own_ip and not u.nat_rules
    ]
    candidates_free.sort(key=lambda u: (not u.address_object, ipaddress.IPv4Address(u.ip)))
    if candidates_free:
        u = candidates_free[0]
        return WanCandidate(
            ip=u.ip,
            address_object_name=u.address_object or client.config.naming.wan_address(u.ip),
            address_object_exists=bool(u.address_object),
            reason="fully-free",
        )

    # Phase 2: port-multiplex
    candidates_mux = [
        u for u in usage.values()
        if u.ip != own_ip and not _blocking_rules(u)
    ]
    candidates_mux.sort(key=lambda u: (len(u.nat_rules), ipaddress.IPv4Address(u.ip)))
    if candidates_mux:
        u = candidates_mux[0]
        return WanCandidate(
            ip=u.ip,
            address_object_name=u.address_object or client.config.naming.wan_address(u.ip),
            address_object_exists=bool(u.address_object),
            reason="port-multiplex",
        )

    raise RuntimeError(
        f"{subnet} subnet'inde {proto.upper()}/{requested_port} için boş IP yok. "
        f"Tüm adresler bu portu kullanıyor veya service=any kuralları mevcut."
    )
