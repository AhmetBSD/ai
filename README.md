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
| [`skills/palo-alto`](skills/palo-alto/) | DNAT (port forwarding) and security-policy source restriction on PAN-OS firewalls, driven by plain-language sentences. | [Install + user guide](skills/palo-alto/GUIDE.md) · [Internal SKILL.md](skills/palo-alto/SKILL.md) |

Each skill has its own `install.sh` for one-line customer setup; see the linked guide.

## License

MIT — see [LICENSE](LICENSE).
