# Palo Alto Skill — User Guide

This skill lets you manage **DNAT (port forwarding)** and **security policy source restrictions** on your Palo Alto firewall by chatting with Claude Code in plain language. Claude connects to your firewall directly, detects conflicts, and applies + commits the change in one shot.

> **Important:** Your password is **never** written to disk. It lives in process memory only and disappears when the operation ends. In each new Claude session you provide firewall credentials once.

---

## 1. Install (One Command, One Time)

### macOS / Linux (bash)

```bash
curl -fsSL https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.ps1 | iex
```

The installer:
- Clones the skill repository (macOS/Linux: `~/.local/share/ai-skills/`; Windows: `%LOCALAPPDATA%\ai-skills\`)
- Symlinks (junction on Windows) the skill into Claude's discovery path (`~/.claude/skills/palo-alto/`)
- Creates a Python virtual environment and installs `pan-os-python`

You're ready when you see `DONE` at the end.

Prerequisites (expected to already be present):
- macOS, Linux, or Windows 10+
- `git` (Windows: Git for Windows from https://git-scm.com/download/win)
- Python 3.10 or newer, recommended 3.13 (Windows: https://www.python.org/downloads/, "Add to PATH" enabled)
- Claude Code installed (claude.ai/code)

---

## 2. First Use — Providing Firewall Credentials

Just write to Claude (example):

> "My firewall is 10.0.0.1, admin, password MyPass123. Forward port 80 of 198.51.100.108 to 192.168.1.50:90."

Claude extracts:
- **Firewall mgmt IP, username, and password** from your sentence
- **DNAT details** (WAN IP, port, target server, target port)
- Calls the skill; if no conflict, applies and commits.

Within the same conversation you don't need to repeat the credentials. Just say "forward port X of IP Y to server Z:port" and Claude reuses the firewall info.

---

## 3. What You Can Ask — Example Sentences

### 3.1 Forward a specific WAN IP's port

> "Forward port 80 of 198.51.100.108 to 192.168.1.50:90"

The skill will:
- Verify `198.51.100.108` is inside your firewall's WAN subnet
- **Reject** if TCP/80 is already used by another NAT rule on that IP — and tell you which rule (the firewall's own WAN IP is accepted as a target; only port collisions are blocked)
- Otherwise: create address objects `WAN_IF108`, `SERVER_50`, service object `SVC_80_TCP` (if missing)
- Create NAT rule `RULE108` and matching security rule `RULE_108`
- Commit

### 3.2 "Find a free IP and forward"

> "Pick a free IP and forward port 80 to 192.168.1.50:90"

The skill prefers an IP that has no existing NAT rules at all. If none exists, it picks an IP whose requested port is still free (port-multiplexing).

### 3.3 UDP forwarding

> "Forward UDP port 5060 of 198.51.100.108 to 192.168.1.20:5060"

Runs with `--protocol udp`, creates service object `SVC_5060_UDP`.

### 3.4 Source IP restriction (security policy)

> "Restrict RULE_108 so only 203.0.113.46 and 203.0.113.37 can reach it"

The skill:
- Finds the security rule
- For a single source IP: creates an address object. For multiple: creates an address-group.
- Updates the rule's `source` field (e.g. `any` → restricted list)
- Commits

> "Restrict port 80 of 198.51.100.108 to Turkey only"

Geographic restriction: passes `--source-region TR`.

### 3.5 Remove an existing rule (with auto-cleanup)

> "Remove RULE108"

The NAT rule, its matching security rule, **and any address/service objects the skill itself created for that rule** are removed together — provided they are not referenced by any other rule. Objects the operator created by hand are never touched (the skill identifies its own objects via a `[skill-managed]` marker in the description field).

**Hard isolation:** `--remove` is **only allowed on rules the skill itself created**. Operator-created rules are refused with `kind: not_owned`. The same protection applies to source-IP restriction (`restrict_source.py`).

The JSON response lists `deleted_orphans` (what was cleaned up) and `kept_objects` (what was kept and why — e.g. `not-skill-managed`, `used-by-3-other-refs`). AddressGroup members are recursed into, so `SRC-RULE_X` groups + their `SRC_a-b-c-d` members are removed together.

### 3.6 Update the inside destination of an existing rule

> "Repoint RULE100 to 10.0.10.222:9090"

This is the **one write-side exception** to hard isolation: the skill can change `destination_translated_address` + `destination_translated_port` on **any** NAT rule, including operator-created ones. The rule's name, WAN side, zones, source filter, service object, and paired security policy stay exactly as the operator set them.

```
PANOS_HOST=... PANOS_USERNAME=... PANOS_PASSWORD=... PANOS_INSECURE=1 \
  ~/.palo-alto/venv/bin/python ~/.claude/skills/palo-alto/scripts/dnat.py \
  --update RULE100 --target-ip 10.0.10.222 --target-port 9090
```

The response's `rule_owned_by` field reads `skill` or `operator` so you know which path was taken. The previous destination address is auto-cleaned **only if it was skill-managed and now has no references** — operator-created objects are left alone.

### 3.6 Preview free IPs/ports

> "Show me which WAN IPs are free"

Lists which IPs are entirely unused vs. which have port-multiplex capacity. Read-only, no changes.

---

## 4. Conflict Checks

The skill refuses the operation (and tells you why) in these cases:

| Situation | What the skill says |
|-----------|---------------------|
| IP is outside the WAN subnet (e.g. `8.8.8.8`) | "This IP is not in your firewall's WAN subnet (`198.51.100.96/28`). Pick one in that range." |
| Requested port already in use on that IP | "TCP/80 is already used on 198.51.100.99. Conflicting rule: RULE99. Pick a different IP or port." |
| Service-group or port-range conflict | "Port 7081 is already taken by the PORT_7081-7082 range." |
| A `service=any` or `application-default` rule exists | "A service=any rule on this IP consumes every port." |
| Same rule name exists with different parameters | "RULE108 already exists pointing to a different target. Delete the old rule or use a different name." |

---

## 5. Auto-Update

Before every operation the skill silently pulls the latest version from GitHub (at most once every 24 hours). **No manual step needed.**

- On network failure: the skill keeps working with the cached version
- On a new version: pulled in the background, active on your next operation

Force an immediate update:

```bash
bash ~/.claude/skills/palo-alto/scripts/update.sh --force
```

---

## 6. Security Summary

| Topic | Behaviour |
|-------|-----------|
| Password persistence | **None.** Lives only in env vars of the running process; gone at exit. |
| API key persistence | **None.** If you provide username+password, `pan-os-python` generates a fresh API key per session. |
| TLS verification | Self-signed certs are accepted (typical for firewall mgmt). You can pin a CA via `PANOS_CA_BUNDLE`. |
| Log files | Skill writes no log files. JSON output goes only to stdout for Claude to consume. |
| Outbound traffic | HTTPS only, to the mgmt IP you provided. Nothing else. |
| Object ownership | Every object the skill creates is tagged with `[skill-managed]` in its description. Only tagged + zero-reference objects can be auto-cleaned. Operator-created objects are never touched. |

---

## 7. Common Issues

**`PANOS_HOST not set`**
→ You haven't given firewall details in this conversation yet. Say it once: "Firewall 10.0.0.1, admin/MyPass."

**`SSL: CERTIFICATE_VERIFY_FAILED`**
→ Self-signed cert. Claude will automatically pass `PANOS_INSECURE=1`. If not, tell Claude "I use a self-signed cert."

**`distutils not found` (Python 3.14)**
→ Skill setup handles this automatically (`setuptools<81` provides the shim). Re-run `install.sh`.

**`commit failed: ... validation`**
→ Firewall-side PAN-OS validation error. Read the message and fix (e.g. wrong zone name). The skill auto-reverts the candidate config, so nothing remains broken.

**"Not in WAN subnet" but the IP is correct**
→ Check the actual subnet on the WAN interface. To override, tell Claude "my WAN subnet is 1.2.3.0/24" (the skill uses `PANOS_WAN_SUBNET`).

---

## 8. Operator Hardening — Least-Privilege Service Account

In production you should not authenticate the skill with a full `admin` account. PAN-OS gives you three patterns; pick whichever fits your environment. The **skill code is identical in all three** — the only thing that changes is what kind of credentials end up in the env vars.

### Pattern A — Local PA admin user with custom role (simplest, no AD)

PAN-OS Web UI:

1. **Device → Admin Roles → Add**
   - Name: `skill-dnat-operator`
   - Web UI tab: turn **everything off**
   - XML API tab: enable `Configuration`, `Operational Requests`, `Commit`. Leave Export/Import/Log/Report/UserID off.
   - CLI: `None`
   - (Optional, PAN-OS 10.1+) XPath restriction: limit config write to
     `/config/devices/entry/vsys/entry/{address,service,address-group,rulebase/nat,rulebase/security}`
2. **Device → Administrators → Add**
   - Name: `skill-svc`
   - Authentication: Password (local), set a strong password
   - Administrator Type: Role Based
   - Profile: `skill-dnat-operator`
3. Commit

Then on the customer's machine:
```bash
export PANOS_USERNAME=skill-svc
export PANOS_PASSWORD='...'
export PANOS_INSECURE=1
```

### Pattern B — AD-backed admin user (LDAP auth profile)

Use this if you already have an LDAP authentication profile pointing at Active Directory (`Domain Users` is the convention). The PA admin user shares its name with an AD user; the actual password lives in AD only.

1. Create the role profile exactly as in **Pattern A step 1**.
2. **Device → Administrators → Add**
   - Name: e.g. `john.doe` (must equal the AD `sAMAccountName`)
   - Authentication Profile: pick your LDAP/AD profile (e.g. `Domain Users`)
   - Administrator Type: Role Based, Profile: `skill-dnat-operator`
3. Make sure the AD account is in the group your LDAP profile's `allow-list` accepts.
4. Commit.

Customer-side env vars are the same as Pattern A — the username matches AD, the password is the AD password. PA does the LDAP bind transparently.

### Pattern C — Short-lived API key (any pattern + key rotation)

Layer on top of Pattern A or B. PA CLI:

```
configure
set deviceconfig setting management api-key-lifetime 60
commit
```

`api-key-lifetime` is in **minutes** (`60` = 1 hour; `0` disables expiry). Each generated key is valid for that window only.

Workflow:

```bash
# Once per session — generate a fresh key with username + password.
export PANOS_HOST=fw.example.com
export PANOS_USERNAME=skill-svc
export PANOS_PASSWORD='...'
export PANOS_INSECURE=1
~/.palo-alto/venv/bin/python ~/.claude/skills/palo-alto/scripts/dnat.py --keygen
# → prints JSON with "api_key": "LUFRPT..."

# Then drop the password, use the short-lived key for the rest of the session:
unset PANOS_PASSWORD
export PANOS_API_KEY='LUFRPT...'

# Subsequent skill calls in the same shell use only the api key.
```

The skill always prefers `PANOS_API_KEY` over username+password when both are set. When the key expires the next call returns `kind: auth` — re-run `--keygen` and replace it.

### (Optional) Lock the admin user down by source IP

**Device → Setup → Management → Permitted IP Addresses**: allow only the customer's workstation IP. Combined with one of the patterns above this means the service account is unusable from anywhere else.

---

## 9. More Information

- All script options and architecture: `~/.claude/skills/palo-alto/SKILL.md`
- Troubleshooting reference: `~/.claude/skills/palo-alto/references/troubleshooting.md`
- XML API cheat-sheet: `~/.claude/skills/palo-alto/references/xml-api-cheatsheet.md`

Report issues: https://github.com/AhmetBSD/ai/issues
