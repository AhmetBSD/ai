# Palo Alto Skill — User Guide

This skill lets you manage **DNAT (port forwarding)** and **security policy source restrictions** on your Palo Alto firewall by chatting with Claude Code in plain language. Claude connects to your firewall directly, detects conflicts, and applies + commits the change in one shot.

> **Important:** Your password is **never** written to disk. It lives in process memory only and disappears when the operation ends. In each new Claude session you provide firewall credentials once.

---

## 1. Install (One Command, One Time)

In a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.sh | bash
```

The installer:
- Clones the skill repository to `~/.local/share/ai-skills/`
- Symlinks the skill into Claude's discovery path: `~/.claude/skills/palo-alto/`
- Creates a Python virtual environment and installs `pan-os-python`

You're ready when you see `DONE` at the end.

Prerequisites (expected to already be present):
- macOS or Linux
- `git`
- Python 3.10 or newer (recommended: 3.13)
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
- Verify it's not the firewall's own interface IP
- **Reject** if TCP/80 is already used by another NAT rule on that IP — and tell you which rule
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

### 3.5 Remove an existing rule

> "Remove RULE108"

The NAT rule and its matching security rule are removed together.

### 3.6 Preview free IPs/ports

> "Show me which WAN IPs are free"

Lists which IPs are entirely unused vs. which have port-multiplex capacity. Read-only, no changes.

---

## 4. Conflict Checks

The skill refuses the operation (and tells you why) in these cases:

| Situation | What the skill says |
|-----------|---------------------|
| IP is outside the WAN subnet (e.g. `8.8.8.8`) | "This IP is not in your firewall's WAN subnet (`198.51.100.96/28`). Pick one in that range." |
| IP is the firewall's own interface IP (e.g. `.98`) | "This is the firewall's own WAN interface IP — DNAT cannot land on it." |
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

## 8. More Information

- All script options and architecture: `~/.claude/skills/palo-alto/SKILL.md`
- Troubleshooting reference: `~/.claude/skills/palo-alto/references/troubleshooting.md`
- XML API cheat-sheet: `~/.claude/skills/palo-alto/references/xml-api-cheatsheet.md`

Report issues: https://github.com/AhmetBSD/ai/issues
