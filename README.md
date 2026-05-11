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

| Skill | What it does | Docs |
|-------|--------------|------|
| [`skills/palo-alto`](skills/palo-alto/) | DNAT, security-policy source restriction, and destination-only updates on PAN-OS firewalls, driven by plain-language sentences. Hard-isolated: every object the skill creates is tagged `[skill-managed]`; it can delete/modify only its own rules. The single write-side exception is `--update`, which can repoint **any** rule's inside destination without touching its identity. | [Install + user guide](skills/palo-alto/GUIDE.md) · [Internal SKILL.md](skills/palo-alto/SKILL.md) |

Each skill has its own one-line installer (bash for macOS/Linux, PowerShell for Windows). See the linked guide for details.

## License

MIT — see [LICENSE](LICENSE).
