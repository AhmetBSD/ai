# ai

AI / automation projects by **AhmetBSD**.

This repository is the umbrella for everything I publish in the AI tooling space — Claude Code skills, agents, MCP servers, helper utilities, and notes. Each project lives in its own top-level directory and is independent: install only what you need.

## Repository layout

```
ai/
├── skills/        Claude Code skills (drop-in capabilities for Claude Code)
│   └── palo-alto/   PAN-OS firewall automation via natural language
└── …               (more areas will land here over time)
```

## Currently shipped

### Skills

#### [`skills/palo-alto`](skills/palo-alto/) — PAN-OS firewall automation

The customer talks to Claude Code in plain language; the skill translates the request into PAN-OS API calls.

What it can do:
- **DNAT (port forwarding)** — explicit WAN IP or auto-pick a free one. Detects port/range conflicts against existing rules and refuses with a Turkish explanation before touching anything.
- **Update an existing DNAT's inside destination** — repoint any rule's target IP/port (including operator-created rules). Name, WAN side, zones, source filter, service object, paired security policy all stay untouched.
- **Source IP / region restriction** on a skill-managed security policy.
- **Remove a skill-managed rule** with **auto-cleanup of orphan address/service objects** the skill created for it (recursing into address-groups). Nothing operator-created is ever touched.
- **Read-only discovery** — `free_ip.py` lists which WAN IPs are unused, which are port-multiplex candidates, and which are blocked by `service=any`.

How it stays safe in production:

- **Hard isolation via `[skill-managed]` marker.** Every object and rule the skill creates is tagged in its `description` field. Destructive operations (`--remove`, source restriction) refuse to touch anything without that tag. The single write-side exception is `--update`, which can repoint **any** rule's inside destination without altering its identity.
- **No credentials on disk in normal flow.** Customer runs `auth.py` once: enter host, username, password (input hidden, never echoed, never written). The skill calls PAN-OS keygen, drops the password, writes only the short-lived API key to `~/.palo-alto/session.env` (chmod 600). All subsequent commands auto-load that file — no shell exports required.
- **Short-lived API key.** Lifetime is governed by PAN-OS `api-key-lifetime`. When the key expires, the skill prints a friendly Turkish message pointing the user back to `auth.py`.
- **Least-privilege service account.** [GUIDE.md §8](skills/palo-alto/GUIDE.md#8-operator-hardening--least-privilege-service-account) documents three deployment patterns: local PA admin user with custom role, AD/LDAP-backed admin user, or short-lived API key on top of either.
- **Auto-update.** Each operation calls `update.sh` first (24h cache, silent on network failure) so customers always run the latest committed version of the skill.
- **TLS bypass only on explicit opt-in** for self-signed firewall mgmt certs (typical PA deployment).

Documentation: [user guide](skills/palo-alto/GUIDE.md) · [internal SKILL.md](skills/palo-alto/SKILL.md)

#### One-line install (per customer machine)

**macOS / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.ps1 | iex
```

The installer clones this repo to a local cache, junctions the skill into `~/.claude/skills/palo-alto`, and creates a Python virtual environment with `pan-os-python`. After that, the customer just chats with Claude Code:

> "Forward port 80 of 198.51.100.108 to 192.168.1.50:90."
>
> "Repoint RULE100 to 192.168.1.222:9090."
>
> "Restrict the rule I just created to 203.0.113.46."

## License

MIT — see [LICENSE](LICENSE).
