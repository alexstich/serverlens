# ServerLens

**Version:** 2.0.0

A secure read-only MCP server for diagnosing remote servers. Connect it to any LLM-powered tool that supports MCP — and the model gets safe access to logs, configs, database, and system metrics over SSH.

### Works with any MCP-compatible tool

| Tool | How to connect |
|------|---------------|
| **Cursor** | Add to `.cursor/mcp.json` — works out of the box |
| **Claude Code** | `claude mcp add serverlens ...` or `.mcp.json` in the project |
| **OpenAI Codex CLI** | Add to `~/.codex/config.toml` under `[mcp_servers.serverlens]` |
| **Claude Desktop** | Add to `claude_desktop_config.json` |
| **Any MCP client** | ServerLens uses standard MCP stdio transport |

By connecting ServerLens as an MCP server, the LLM model gets the data it needs for analysis: application logs, server configs, database records, system health — without the ability to change anything.

## Security model

ServerLens is designed so that the LLM model **cannot harm your server in any way**, regardless of which tool you use. This is achieved at every level of the architecture:

### SSH — standard developer access

The connection between your machine and the server uses ordinary SSH with key authentication — the same mechanism developers use daily. No new ports, no custom protocols, no additional attack surface. If your SSH access is already secure, ServerLens adds nothing to worry about.

### Strict read-only on the server

ServerLens on the server operates exclusively in **read-only** mode:

- **Logs** — tail, search, count. No writing, no deletion, no rotation.
- **Configs** — read only, with automatic redaction of secrets (passwords, tokens, API keys are replaced with `[REDACTED]`).
- **Database** — only `SELECT` queries through a structured API. No raw SQL — the model cannot execute `UPDATE`, `DELETE`, `DROP`, or any DDL. Queries are parameterized and limited to whitelisted tables and fields.
- **System** — read-only metrics: CPU, RAM, disk, service status, process list. No ability to start, stop, or restart anything.
- **Files** — list, read and search files inside whitelisted directory trees, read `.xlsx` sheets. Paths can't leave the tree, private keys are never served, text passes the same secret redaction as configs.
- **Systemd journal** — `journalctl` tail and search for whitelisted units only. Search queries are matched by the agent itself and never passed to the shell.

### MCP protocol — the model is sandboxed

The LLM model does not have direct access to SSH, the filesystem, or the database. It can only call two MCP tools (`serverlens_list` and `serverlens_call`), and each call is routed through ServerLens which enforces the read-only whitelist. Even if the model attempts something unexpected, the server-side agent simply has no write operations available.

### Whitelist-only access

Nothing is accessible by default. Every log file, config, database table, and field must be explicitly listed in the server config (`/etc/serverlens/config.yaml`). If it's not in the whitelist, it doesn't exist for the model.

Within a table, `allowed_fields` is the boundary that matters: a column listed there may be selected, filtered on, grouped by, and ordered by. `allowed_filters` / `allowed_order_by` only *extend* that set — use them for a column you want filterable but not visible. `denied_fields` overrides everything. Read-only queries are also capped by a 30-second statement timeout, so a filter on an unindexed column of a huge table gets cut off instead of blocking the server.

### Summary

| Layer | Protection |
|-------|-----------|
| Network | SSH with key auth — standard, proven, no extra ports |
| Transport | MCP over stdio — no HTTP endpoints, no open ports on the server |
| Server agent | Read-only by design — no write/modify/delete operations exist in the code |
| Database | Structured queries only, whitelisted tables/fields, no raw SQL |
| Configs | Automatic secret redaction (`[REDACTED]`) |
| Access scope | Explicit whitelist — only what you allow in `config.yaml` |

## How it works

```
┌─────────────┐    stdio    ┌──────────────────┐    SSH     ┌──────────────┐
│  LLM tool    │◄──────────►│  MCP proxy       │◄─────────►│  ServerLens   │
│  (Cursor,    │            │  (your machine)  │            │  (server)     │
│  Claude Code)│            │                  │            │               │
└─────────────┘             └──────────────────┘            └──────────────┘
```

| Component | Where | What it does |
|-----------|-----|------------|
| **ServerLens** (`serverlens/`) | Remote server | Read-only access to logs, configs, DB, system information |
| **MCP client** (`serverlens_mcp/`) | Developer machine | Local MCP server: dispatch model (`serverlens_list` / `serverlens_call`), auto-reconnect on disconnect, built-in SSH keepalive |

The LLM tool does not talk SSH directly — it speaks to the local MCP client, which connects to servers on its own.

## Installation

> **Full step-by-step guide: [docs/quickstart.md](docs/quickstart.md)**

Short sequence:

1. **On the server:** clone the repo, run `scripts/install.sh`, configure `/etc/serverlens/config.yaml`
2. **On your machine:** clone the repo, create a venv with `pyyaml`, copy `serverlens_mcp/config.example.yaml` to `~/.serverlens/config.yaml` and fill in SSH settings
3. **In your LLM tool:** add `python -m serverlens_mcp` to the MCP config (e.g. `.cursor/mcp.json` for Cursor), restart

MCP config (paths must be absolute):

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "/Users/YOU/serverlens/venv/bin/python",
      "args": ["-m", "serverlens_mcp", "--config", "/Users/YOU/.serverlens/config.yaml"],
      "env": { "PYTHONPATH": "/Users/YOU/serverlens" }
    }
  }
}
```

**Per-project server access:** use `--servers` to connect only specific servers in a project:

```json
"args": ["-m", "serverlens_mcp", "--config", "/Users/YOU/.serverlens/config.yaml", "--servers", "production,staging"]
```

Define all servers once in `config.yaml`, then select which ones each project needs via `--servers` (comma-separated). See [MCP client docs](docs/client.md#per-project-server-selection---servers).

## Tools (MCP client v2)

The LLM model sees **two** client tools; remote operations go through them:

| Tool | Purpose |
|------------|------------|
| `serverlens_list` | No parameters — list servers from config; with `{ "server": "name" }` — list tools on that server |
| `serverlens_call` | Invoke: `{ "server": "my-server", "tool": "db_query", "arguments": { ... } }` |

On the server, all ServerLens read-only tools remain available (logs, configs, DB, system) — see [API Reference](docs/server/api.md). **LogReader** supports `type: "directory"` sources with glob patterns.

## Documentation

| Document | Description |
|----------|----------|
| **[Quick start](docs/quickstart.md)** | Step-by-step setup from zero to a working system |
| [Architecture](docs/architecture.md) | How the system is built, components, data flow |
| [Server setup](docs/server/setup.md) | Detailed ServerLens configuration |
| [API Reference](docs/server/api.md) | Reference for all tools |
| [MCP client](docs/client.md) | Installing and configuring the MCP client |

## Stack

**Server:** Python 3.10+, aiohttp, PyYAML, psycopg2, argon2-cffi

**MCP client:** Python 3.10+, PyYAML
