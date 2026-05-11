"""PAN-OS client wrapper around pan-os-python.

Idempotent CRUD against an in-memory Config (no disk persistence — credentials
arrive via env vars and vanish at process exit).

Every "ensure_*" method:
- creates if missing
- no-ops if identical
- raises ObjectConflictError if same name has different attributes

Commit failure → PAN-OS auto-reverts candidate (no partial state).
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

# pan-os-python 1.12.x imports `distutils.version`, which was removed in
# Python 3.12. Importing `setuptools` first triggers _distutils_hack which
# re-exposes the module. setup.sh pins setuptools<81 for this reason.
import setuptools  # noqa: F401

from panos.firewall import Firewall
from panos.objects import AddressObject, ServiceObject, AddressGroup
from panos.policies import NatRule, Rulebase, SecurityRule

from config import Config


log = logging.getLogger("panos_client")


# Marker prepended to the `description` of every object/rule the skill creates.
# Used by --remove to safely auto-clean ONLY objects this skill owns; objects
# created manually by the operator are never touched.
SKILL_TAG = "[skill-managed]"


def _tag_description(desc: str) -> str:
    """Prepend the skill marker; idempotent."""
    desc = (desc or "").strip()
    if desc.startswith(SKILL_TAG):
        return desc
    return f"{SKILL_TAG} {desc}".strip()


def _is_skill_managed(obj) -> bool:
    d = getattr(obj, "description", None) or ""
    return d.startswith(SKILL_TAG)


class PanosError(Exception):
    """Predictable client-side errors (conflict, validation, etc.)."""


class CommitError(PanosError):
    pass


class ObjectConflictError(PanosError):
    """Same name, different attributes — explicit human decision required."""


class PanosClient:
    """Stateful wrapper around a Firewall + Rulebase tree."""

    # Hard HTTP timeout — refuse to hang the customer's shell on unreachable
    # firewalls. The default in pan-os-python is much higher; we clamp to 60s.
    HTTP_TIMEOUT_SECONDS = 60

    def __init__(self, cfg: Config):
        self.config = cfg
        # Authenticate via api_key OR username/password (SDK will keygen as needed).
        kwargs = {"hostname": cfg.host, "port": 443, "timeout": self.HTTP_TIMEOUT_SECONDS}
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        else:
            kwargs["api_username"] = cfg.username
            kwargs["api_password"] = cfg.password
        self.fw = Firewall(**kwargs)
        # The Firewall constructor exposes a .timeout attribute that
        # propagates to subsequent xapi calls — set explicitly in case the
        # ctor kwarg is ignored in older SDK versions.
        try:
            self.fw.timeout = self.HTTP_TIMEOUT_SECONDS
        except Exception:
            pass

        # TLS verify control. pan-os-python wraps pan.xapi.PanXapi; the underlying
        # http client honours the `cert` attribute.
        if cfg.insecure:
            # Disable verify and silence urllib3 InsecureRequestWarning so the
            # JSON stdout stays clean. Customer explicitly opted in via --insecure.
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
            # pan.xapi: setting cert to False disables verify.
            self.fw.xapi.cert = False  # type: ignore[attr-defined]
        elif cfg.ca_bundle:
            self.fw.xapi.cert = cfg.ca_bundle  # type: ignore[attr-defined]
        # else: default (system trust store, requires public CA)

        self.fw.vsys = cfg.vsys
        self.rulebase = Rulebase()
        self.fw.add(self.rulebase)
        self._refreshed = False
        self._created: list = []

    # ---- Refresh / Read ------------------------------------------------------

    def refresh(self) -> None:
        AddressObject.refreshall(self.fw)
        ServiceObject.refreshall(self.fw)
        AddressGroup.refreshall(self.fw)
        NatRule.refreshall(self.rulebase)
        SecurityRule.refreshall(self.rulebase)
        self._refreshed = True

    def _require_refreshed(self) -> None:
        if not self._refreshed:
            raise PanosError("PanosClient.refresh() must be called first")

    # ---- Address objects -----------------------------------------------------

    def get_address_by_name(self, name: str) -> Optional[AddressObject]:
        self._require_refreshed()
        return self.fw.find(name, AddressObject)

    def find_address_by_value(self, value: str) -> Optional[AddressObject]:
        self._require_refreshed()
        normalized = _normalize_ip_netmask(value)
        for obj in self.fw.findall(AddressObject):
            if obj.type == "ip-netmask" and _normalize_ip_netmask(obj.value) == normalized:
                return obj
        return None

    def ensure_address(
        self, name: str, value: str, description: str = ""
    ) -> AddressObject:
        existing_by_name = self.get_address_by_name(name)
        if existing_by_name:
            if _normalize_ip_netmask(existing_by_name.value) != _normalize_ip_netmask(value):
                raise ObjectConflictError(
                    f"address object {name!r} exists with value "
                    f"{existing_by_name.value!r}, requested {value!r}"
                )
            return existing_by_name

        existing_by_value = self.find_address_by_value(value)
        if existing_by_value:
            log.info(
                "reusing existing address %s (value=%s) instead of creating %s",
                existing_by_value.name, value, name,
            )
            return existing_by_value

        obj = AddressObject(name=name, value=value, type="ip-netmask", description=_tag_description(description))
        self.fw.add(obj)
        obj.create()
        self._created.append(obj)
        log.info("created address %s = %s", name, value)
        return obj

    # ---- Service objects -----------------------------------------------------

    def get_service_by_name(self, name: str) -> Optional[ServiceObject]:
        self._require_refreshed()
        return self.fw.find(name, ServiceObject)

    def find_service_by_port(
        self, protocol: str, port: int
    ) -> Optional[ServiceObject]:
        self._require_refreshed()
        proto = protocol.lower()
        port_str = str(port)
        for obj in self.fw.findall(ServiceObject):
            if obj.protocol == proto and obj.destination_port == port_str:
                if not obj.source_port:
                    return obj
        return None

    def ensure_service(self, name: str, protocol: str, port: int) -> ServiceObject:
        proto = protocol.lower()
        if proto not in ("tcp", "udp"):
            raise PanosError(f"unsupported protocol: {protocol}")

        existing_by_name = self.get_service_by_name(name)
        if existing_by_name:
            if existing_by_name.protocol != proto or existing_by_name.destination_port != str(port):
                raise ObjectConflictError(
                    f"service {name!r} exists as {existing_by_name.protocol}/"
                    f"{existing_by_name.destination_port}, requested {proto}/{port}"
                )
            return existing_by_name

        existing_by_port = self.find_service_by_port(proto, port)
        if existing_by_port:
            log.info("reusing service %s for %s/%d", existing_by_port.name, proto, port)
            return existing_by_port

        obj = ServiceObject(name=name, protocol=proto, destination_port=str(port),
                            description=_tag_description(""))
        self.fw.add(obj)
        obj.create()
        self._created.append(obj)
        log.info("created service %s = %s/%d", name, proto, port)
        return obj

    # ---- Address groups ------------------------------------------------------

    def ensure_address_group(
        self, name: str, members: Iterable[str], description: str = ""
    ) -> AddressGroup:
        members_sorted = sorted(set(members))
        existing = self.fw.find(name, AddressGroup)
        if existing:
            existing_members = sorted(existing.static_value or [])
            if existing_members != members_sorted:
                raise ObjectConflictError(
                    f"address-group {name!r} exists with members "
                    f"{existing_members}, requested {members_sorted}"
                )
            return existing
        grp = AddressGroup(name=name, static_value=members_sorted, description=_tag_description(description))
        self.fw.add(grp)
        grp.create()
        self._created.append(grp)
        log.info("created address-group %s = %s", name, members_sorted)
        return grp

    # ---- NAT rules -----------------------------------------------------------

    def get_nat_rule(self, name: str) -> Optional[NatRule]:
        self._require_refreshed()
        return self.rulebase.find(name, NatRule)

    def list_nat_rules(self) -> list[NatRule]:
        self._require_refreshed()
        return list(self.rulebase.findall(NatRule))

    def find_nat_rules_matching_destination(
        self, wan_address_name: str, service_name: Optional[str] = None
    ) -> list[NatRule]:
        matches = []
        for rule in self.list_nat_rules():
            dests = rule.destination or []
            if isinstance(dests, str):
                dests = [dests]
            if wan_address_name not in dests:
                continue
            if service_name:
                svcs = rule.service or []
                if isinstance(svcs, str):
                    svcs = [svcs]
                if service_name not in svcs and svcs != ["any"]:
                    continue
            matches.append(rule)
        return matches

    def ensure_nat_rule_dnat(
        self,
        name: str,
        wan_address_name: str,
        service_name: str,
        translated_address_name: str,
        translated_port: int,
        from_zones: Iterable[str],
        to_zone: str,
        to_interface: str,
        description: str = "",
    ) -> NatRule:
        existing = self.get_nat_rule(name)
        if existing:
            mismatch = _diff_nat_rule(
                existing,
                wan_address_name=wan_address_name,
                service_name=service_name,
                translated_address_name=translated_address_name,
                translated_port=translated_port,
                from_zones=list(from_zones),
                to_zone=to_zone,
                to_interface=to_interface,
            )
            if mismatch:
                raise ObjectConflictError(
                    f"NAT rule {name!r} exists with different attributes: {mismatch}"
                )
            return existing

        rule = NatRule(
            name=name,
            description=_tag_description(description),
            fromzone=list(from_zones),
            tozone=to_zone,
            to_interface=to_interface,
            source=["any"],
            destination=[wan_address_name],
            service=service_name,
            nat_type="ipv4",
            destination_translated_address=translated_address_name,
            destination_translated_port=translated_port,
        )
        self.rulebase.add(rule)
        rule.create()
        self._created.append(rule)
        log.info(
            "created NAT rule %s: %s/%s -> %s:%d",
            name, wan_address_name, service_name, translated_address_name, translated_port,
        )
        return rule

    def delete_nat_rule(self, name: str) -> bool:
        existing = self.get_nat_rule(name)
        if not existing:
            return False
        existing.delete()
        log.info("deleted NAT rule %s", name)
        return True

    # ---- Security rules ------------------------------------------------------

    def get_security_rule(self, name: str) -> Optional[SecurityRule]:
        self._require_refreshed()
        return self.rulebase.find(name, SecurityRule)

    def list_security_rules(self) -> list[SecurityRule]:
        self._require_refreshed()
        return list(self.rulebase.findall(SecurityRule))

    def find_security_rules_matching_destination(
        self, dest_address_name: str, service_names: Optional[Iterable[str]] = None
    ) -> list[SecurityRule]:
        matches = []
        target_svcs = set(service_names) if service_names else None
        for rule in self.list_security_rules():
            dests = rule.destination or []
            if isinstance(dests, str):
                dests = [dests]
            if dest_address_name not in dests:
                continue
            if target_svcs:
                svcs = rule.service or []
                if isinstance(svcs, str):
                    svcs = [svcs]
                if not (target_svcs & set(svcs)) and svcs not in (["any"], ["application-default"]):
                    continue
            matches.append(rule)
        return matches

    def ensure_security_rule(
        self,
        name: str,
        from_zones: Iterable[str],
        to_zones: Iterable[str],
        source: Iterable[str],
        destination: Iterable[str],
        service: Iterable[str],
        application: Iterable[str] = ("any",),
        action: str = "allow",
        description: str = "",
        log_end: bool = True,
        profile_setting: Optional[dict] = None,
        profile_group: Optional[str] = None,
    ) -> SecurityRule:
        existing = self.get_security_rule(name)
        if existing:
            diff = _diff_security_rule(
                existing,
                from_zones=list(from_zones),
                to_zones=list(to_zones),
                source=list(source),
                destination=list(destination),
                service=list(service),
                application=list(application),
                action=action,
            )
            if diff:
                raise ObjectConflictError(
                    f"security rule {name!r} exists with different attributes: {diff}"
                )
            return existing

        kwargs = dict(
            name=name,
            description=_tag_description(description),
            fromzone=list(from_zones),
            tozone=list(to_zones),
            source=list(source),
            destination=list(destination),
            service=list(service),
            application=list(application),
            action=action,
            log_end=log_end,
        )
        if profile_group:
            kwargs["group"] = profile_group
        else:
            for k, v in (profile_setting or {}).items():
                kwargs[k] = v
        rule = SecurityRule(**kwargs)
        self.rulebase.add(rule)
        rule.create()
        self._created.append(rule)
        log.info(
            "created security rule %s: %s->%s dst=%s svc=%s action=%s",
            name, list(from_zones), list(to_zones), list(destination), list(service), action,
        )
        return rule

    def update_security_rule_source(
        self,
        rule: SecurityRule,
        new_source: Iterable[str],
    ) -> None:
        rule.source = list(new_source)
        rule.apply()
        log.info("updated security rule %s source -> %s", rule.name, list(new_source))

    # ---- Orphan / GC ---------------------------------------------------------

    def count_references(self, name: str) -> int:
        """How many remaining rules / groups reference this object by name.
        Walks NAT rules, security rules, address groups and service groups.
        """
        self._require_refreshed()
        n = 0
        for r in self.rulebase.findall(NatRule):
            if name in _as_list(r.source): n += 1
            if name in _as_list(r.destination): n += 1
            if name in _as_list(r.service): n += 1
            if r.destination_translated_address == name: n += 1
            if getattr(r, "source_translation_ip_address", None) == name: n += 1
            sta = getattr(r, "source_translation_translated_addresses", None)
            if sta and name in _as_list(sta): n += 1
        for r in self.rulebase.findall(SecurityRule):
            if name in _as_list(r.source): n += 1
            if name in _as_list(r.destination): n += 1
            if name in _as_list(r.service): n += 1
        for g in self.fw.findall(AddressGroup):
            if g.static_value and name in g.static_value: n += 1
        try:
            from panos.objects import ServiceGroup
            for g in self.fw.findall(ServiceGroup):
                if g.value and name in g.value: n += 1
        except ImportError:
            pass
        return n

    def cleanup_orphan_managed(self, candidate_names: Iterable[str]) -> dict:
        """For each candidate name, delete the object iff:
          (a) it carries the SKILL_TAG marker in description, AND
          (b) no remaining rule/group references it.
        AddressGroup members are recursed into.

        Returns a dict {deleted: [...], skipped: [(name, reason), ...]}.
        """
        self._require_refreshed()
        deleted: list[str] = []
        skipped: list[tuple] = []
        seen: set = set()

        def _try_delete(name: str) -> None:
            if name in seen or name in ("any", "application-default"):
                return
            seen.add(name)
            # Identify type
            ao = self.fw.find(name, AddressObject)
            so = self.fw.find(name, ServiceObject)
            ag = self.fw.find(name, AddressGroup)
            obj = ao or so or ag
            if obj is None:
                skipped.append((name, "not-found"))
                return
            if not _is_skill_managed(obj):
                skipped.append((name, "not-skill-managed"))
                return
            n = self.count_references(name)
            if n > 0:
                skipped.append((name, f"used-by-{n}-other-refs"))
                return
            # Recurse into address-group members before deleting the group
            if ag is not None and ag.static_value:
                members = list(ag.static_value)
                obj.delete()
                deleted.append(name)
                log.info("deleted orphan address-group %s", name)
                for member in members:
                    _try_delete(member)
                return
            obj.delete()
            deleted.append(name)
            log.info("deleted orphan %s", name)

        for n in candidate_names:
            _try_delete(n)
        return {"deleted": deleted, "skipped": skipped}

    # ---- Commit / revert -----------------------------------------------------

    def commit(self, description: str, timeout: int = 60) -> dict:
        """Synchronous commit. pan-os-python 1.12.x Firewall.commit() does NOT
        accept a 'description' kwarg — description must be embedded in cmd XML.

        Hard timeout (default 60s) — refuse to hang the customer's shell.
        On timeout we issue a non-sync commit so PAN-OS still finishes the job
        in the background, and return a 'pending' result.
        """
        import xml.sax.saxutils as _x
        safe = _x.escape(description) if description else ""
        cmd_xml = f"<commit><description>{safe}</description></commit>" if safe else None

        log.info("commit start (timeout=%ds): %s", timeout, description)

        # Run the SDK commit (which polls PAN-OS internally) in a thread with
        # a hard timeout so the customer never waits more than `timeout` sec.
        import threading
        result_box: dict = {}

        def _commit():
            try:
                result_box["ok"] = self.fw.commit(
                    sync=True, cmd=cmd_xml, exception=True
                )
            except Exception as e:
                result_box["err"] = e

        t = threading.Thread(target=_commit, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            # Did not complete in time. Most PAN-OS commits finish within 60s;
            # if not, the firewall almost certainly is still finishing in the
            # background. Don't raise — return a non-error "running" status so
            # the caller can re-poll via discovery / idempotency on the next call.
            log.warning("commit not confirmed within %ds (likely still running)", timeout)
            return {
                "result": "committing",
                "timeout_seconds": timeout,
                "message": (
                    f"commit accepted but completion not confirmed within {timeout}s. "
                    f"PAN-OS typically finishes within ~60s — the change will land shortly."
                ),
            }

        if "err" in result_box:
            raise CommitError(f"commit failed: {result_box['err']}") from result_box["err"]

        result = result_box.get("ok")
        log.info("commit OK: %s", result)
        return result if isinstance(result, dict) else {"raw": str(result)}

    def revert_candidate(self) -> None:
        log.warning("reverting candidate config (load running.xml)")
        self.fw.op("<load><config><from>running-config.xml</from></config></load>",
                   cmd_xml=False)

    @property
    def created_objects(self) -> list:
        return list(self._created)


# ---- Module-level helpers --------------------------------------------------


def _normalize_ip_netmask(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if "/" not in v:
        return f"{v}/32"
    return v


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)


def _diff_nat_rule(
    existing: NatRule,
    *,
    wan_address_name: str,
    service_name: str,
    translated_address_name: str,
    translated_port: int,
    from_zones: list,
    to_zone: str,
    to_interface: str,
) -> Optional[str]:
    diffs = []
    if sorted(_as_list(existing.fromzone)) != sorted(from_zones):
        diffs.append(f"fromzone {existing.fromzone} != {from_zones}")
    if _as_list(existing.tozone) != [to_zone]:
        diffs.append(f"tozone {existing.tozone} != {to_zone}")
    if existing.to_interface != to_interface:
        diffs.append(f"to_interface {existing.to_interface} != {to_interface}")
    if _as_list(existing.destination) != [wan_address_name]:
        diffs.append(f"destination {existing.destination} != [{wan_address_name}]")
    if _as_list(existing.service) != [service_name]:
        diffs.append(f"service {existing.service} != [{service_name}]")
    if existing.destination_translated_address != translated_address_name:
        diffs.append(
            f"dst-xlate-addr {existing.destination_translated_address} "
            f"!= {translated_address_name}"
        )
    try:
        existing_port = int(existing.destination_translated_port) if existing.destination_translated_port else None
    except (TypeError, ValueError):
        existing_port = None
    if existing_port != translated_port:
        diffs.append(
            f"dst-xlate-port {existing.destination_translated_port} != {translated_port}"
        )
    return "; ".join(diffs) if diffs else None


def _diff_security_rule(
    existing: SecurityRule,
    *,
    from_zones: list,
    to_zones: list,
    source: list,
    destination: list,
    service: list,
    application: list,
    action: str,
) -> Optional[str]:
    diffs = []
    if sorted(_as_list(existing.fromzone)) != sorted(from_zones):
        diffs.append(f"fromzone {existing.fromzone} != {from_zones}")
    if sorted(_as_list(existing.tozone)) != sorted(to_zones):
        diffs.append(f"tozone {existing.tozone} != {to_zones}")
    if sorted(_as_list(existing.source)) != sorted(source):
        diffs.append(f"source {existing.source} != {source}")
    if sorted(_as_list(existing.destination)) != sorted(destination):
        diffs.append(f"destination {existing.destination} != {destination}")
    if sorted(_as_list(existing.service)) != sorted(service):
        diffs.append(f"service {existing.service} != {service}")
    if sorted(_as_list(existing.application)) != sorted(application):
        diffs.append(f"application {existing.application} != {application}")
    if existing.action != action:
        diffs.append(f"action {existing.action} != {action}")
    return "; ".join(diffs) if diffs else None
