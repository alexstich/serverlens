# ServerLens

**Version:** 2.0.0

A secure read-only MCP tool for diagnosing remote servers. From Cursor you can read logs and configuration, run safe PostgreSQL queries, and monitor system state — all over SSH.

## How it works

```
┌─────────────┐    stdio    ┌──────────────────┐    SSH     ┌──────────────┐
│   Cursor     │◄──────────►│  MCP proxy       │◄─────────►│  ServerLens   │
│              │            │  (your machine)  │            │  (server)     │
└─────────────┘             └──────────────────┘            └──────────────┘
```

| Component | Where | What it does |
|-----------|-----|------------|
| **ServerLens** (`src/`) | Remote server | Read-only access to logs, configs, DB, system information |
| **MCP proxy** (`mcp-client/`) | Developer machine | Local MCP server for Cursor: dispatch model (`serverlens_list` / `serverlens_call`), auto-reconnect on disconnect, built-in SSH keepalive |

Cursor does not talk SSH directly — it speaks to the local MCP proxy, which connects to servers on its own.

## Installation

> **Full step-by-step guide: [docs/quickstart.md](docs/quickstart.md)**

Short sequence:

1. **On the server:** clone the repo, run `scripts/install.sh`, configure `/etc/serverlens/config.yaml`
2. **On your machine:** `cd mcp-client && composer install`, configure `~/.serverlens/config.yaml` with SSH settings
3. **In Cursor:** add the MCP proxy to `~/.cursor/mcp.json`, restart

## Tools (MCP proxy v2)

In Cursor you see **two** proxy tools; remote operations go through them:

| Tool | Purpose |
|------------|------------|
| `serverlens_list` | No parameters — list servers from config; with `{ "server": "name" }` — list tools on that server |
| `serverlens_call` | Invoke: `{ "server": "my-server", "tool": "db_query", "arguments": { ... } }` |

On the server, all ServerLens read-only tools remain available (logs, configs, DB, system) — see [API Reference](docs/server/api.md). **LogReader** supports `type: "directory"` sources with glob patterns.

## Security

- **SSH keys** — authentication via standard SSH
- **Whitelist** — access only to explicitly allowed files, tables, fields
- **No raw SQL** — only structured, parameterized queries
- **Secret redaction** — passwords, keys, and tokens are masked automatically
- **Read-only** — ServerLens cannot modify data

## Documentation

| Document | Description |
|----------|----------|
| **[Quick start](docs/quickstart.md)** | Step-by-step setup from zero to a working system |
| [Architecture](docs/architecture.md) | How the system is built, components, data flow |
| [Server setup](docs/server/setup.md) | Detailed ServerLens configuration |
| [API Reference](docs/server/api.md) | Reference for all tools |
| [MCP proxy](mcp-client/docs/README.md) | Installing and configuring the MCP client |

## Stack

PHP 8.1+, ReactPHP, Symfony YAML, PDO PostgreSQL
