"""Runtime config — read from environment, never persisted to disk.

Required env vars:
  PANOS_HOST           - firewall mgmt IP or FQDN
  PANOS_API_KEY        - API key (preferred), OR
  PANOS_USERNAME + PANOS_PASSWORD  - username/password (key generated on first request)

Optional env vars:
  PANOS_INSECURE=1     - skip TLS verify (self-signed certs)
  PANOS_CA_BUNDLE      - path to CA bundle PEM (if not insecure and not system-trusted)
  PANOS_WAN_ZONE       - default: WAN
  PANOS_LAN_ZONE       - default: LAN
  PANOS_WAN_INTERFACE  - default: ethernet1/2
  PANOS_LAN_INTERFACE  - default: ethernet1/1
  PANOS_VSYS           - default: vsys1
  PANOS_WAN_SUBNET     - CIDR override (default: discovered from interface IP/mask)

Creds vanish when the process exits. No file is created.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class NamingConvention:
    wan_address_template: str = "WAN_IF{octet}"
    lan_address_template: str = "SERVER_{octet}"
    service_template: str = "SVC_{port}_{proto}"
    nat_rule_template: str = "RULE{octet}"
    security_rule_template: str = "RULE_{octet}"

    def wan_address(self, ip: str) -> str:
        octet = ipaddress.IPv4Address(ip).packed[-1]
        return self.wan_address_template.format(octet=octet)

    def lan_address(self, ip: str) -> str:
        octet = ipaddress.IPv4Address(ip).packed[-1]
        return self.lan_address_template.format(octet=octet)

    def service(self, port: int, proto: str) -> str:
        return self.service_template.format(port=port, proto=proto.upper())

    def nat_rule(self, ip: str, suffix: str = "") -> str:
        octet = ipaddress.IPv4Address(ip).packed[-1]
        name = self.nat_rule_template.format(octet=octet)
        return f"{name}-{suffix}" if suffix else name

    def security_rule(self, ip: str, suffix: str = "") -> str:
        octet = ipaddress.IPv4Address(ip).packed[-1]
        name = self.security_rule_template.format(octet=octet)
        return f"{name}-{suffix}" if suffix else name


@dataclass(frozen=True)
class Config:
    # Connection
    host: str
    api_key: Optional[str]       # if None, username/password must be set
    username: Optional[str]
    password: Optional[str]
    insecure: bool               # True = skip TLS verify
    ca_bundle: Optional[str]     # path to CA PEM if doing TLS verify with private CA

    # Topology
    wan_zone: str
    lan_zone: str
    wan_interface: str
    lan_interface: str
    vsys: str
    wan_subnet: Optional[ipaddress.IPv4Network]

    # Naming
    naming: NamingConvention

    # Security defaults (applied to new security rules)
    security_profiles: dict = field(default_factory=lambda: {
        "virus": "default",
        "spyware": "strict",
        "vulnerability": "strict",
        "wildfire_analysis": "default",
    })
    security_profile_group: str = ""


class ConfigError(Exception):
    pass


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(key)
    return v if (v is not None and v != "") else default


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "")
    return v.lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    host = _env("PANOS_HOST")
    if not host:
        raise ConfigError(
            "PANOS_HOST not set. Customer should provide firewall mgmt IP or FQDN."
        )

    api_key = _env("PANOS_API_KEY")
    username = _env("PANOS_USERNAME")
    password = _env("PANOS_PASSWORD")
    if not api_key and not (username and password):
        raise ConfigError(
            "No credentials. Set PANOS_API_KEY or both PANOS_USERNAME and PANOS_PASSWORD."
        )

    insecure = _env_bool("PANOS_INSECURE")
    ca_bundle = _env("PANOS_CA_BUNDLE")
    if ca_bundle and not os.path.exists(ca_bundle):
        raise ConfigError(f"PANOS_CA_BUNDLE not found: {ca_bundle}")

    wan_subnet = None
    if _env("PANOS_WAN_SUBNET"):
        try:
            wan_subnet = ipaddress.IPv4Network(os.environ["PANOS_WAN_SUBNET"], strict=False)
        except ValueError as e:
            raise ConfigError(f"Invalid PANOS_WAN_SUBNET: {e}")

    return Config(
        host=host,
        api_key=api_key,
        username=username,
        password=password,
        insecure=insecure,
        ca_bundle=ca_bundle,
        wan_zone=_env("PANOS_WAN_ZONE", "WAN"),
        lan_zone=_env("PANOS_LAN_ZONE", "LAN"),
        wan_interface=_env("PANOS_WAN_INTERFACE", "ethernet1/2"),
        lan_interface=_env("PANOS_LAN_INTERFACE", "ethernet1/1"),
        vsys=_env("PANOS_VSYS", "vsys1"),
        wan_subnet=wan_subnet,
        naming=NamingConvention(),
    )


if __name__ == "__main__":
    import json
    try:
        cfg = load_config()
        # Don't print secrets — only the structure
        print(json.dumps({
            "host": cfg.host,
            "auth_mode": "api_key" if cfg.api_key else "username+password",
            "insecure": cfg.insecure,
            "ca_bundle": cfg.ca_bundle,
            "wan_zone": cfg.wan_zone,
            "lan_zone": cfg.lan_zone,
            "wan_interface": cfg.wan_interface,
            "lan_interface": cfg.lan_interface,
            "wan_subnet": str(cfg.wan_subnet) if cfg.wan_subnet else None,
            "example_names": {
                "wan_address(.108)": cfg.naming.wan_address("198.51.100.108"),
                "lan_address(.50)": cfg.naming.lan_address("192.168.1.50"),
                "service(80, tcp)": cfg.naming.service(80, "tcp"),
                "nat_rule(.108)": cfg.naming.nat_rule("198.51.100.108"),
                "security_rule(.108)": cfg.naming.security_rule("198.51.100.108"),
            },
        }, indent=2))
    except ConfigError as e:
        print(f"ERROR: {e}")
        raise SystemExit(1)
