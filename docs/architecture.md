# ServerLens — System architecture

> Step-by-step install: [quickstart.md](quickstart.md) | Server configuration: [server/setup.md](server/setup.md) | API: [server/api.md](server/api.md)

## Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        Developer machine                                     │
│                                                                              │
│   ┌─────────────┐    stdio (JSON-RPC)    ┌──────────────────────────┐       │
│   │   Cursor /   │◄────────────────────►│   ServerLens MCP Proxy    │       │
│   │ Claude Desktop│                      │   (mcp-client/)           │       │
│   │  (MCP client)│                      │  only 2 tools:            │       │
│   └─────────────┘                       │  serverlens_list,        │       │
│                                          │  serverlens_call         │       │
│                                          │  ┌──────────────────────┐ │       │
│                                          │  │  SSH Connection       │ │       │
│                                          │  │  Manager (+ keepalive)│ │       │
│                                          │  └──────────┬───────────┘ │       │
│                                          └─────────────┼─────────────┘       │
│                                                        │                     │
└────────────────────────────────────────────────────────┼─────────────────────┘
                                                         │
                                                    SSH (key)
                                                         │
┌────────────────────────────────────────────────────────┼─────────────────────┐
│                        Remote server                    │                     │
│                                                        │                     │
│   ┌────────────────────────────────────────────────────┼───────────────────┐ │
│   │  ServerLens (stdio mode)                           │                   │ │
│   │                                                    ▼                   │ │
│   │  ┌──────────────────┐    ┌────────────────────────────────────────┐   │ │
│   │  │  MCP Server       │    │  Modules                               │   │ │
│   │  │  (JSON-RPC 2.0)  │───►│                                        │   │ │
│   │  │                   │    │  ┌──────────┐ ┌──────────────┐        │   │ │
│   │  │  - initialize    │    │  │ LogReader │ │ ConfigReader │        │   │ │
│   │  │  - tools/list    │    │  │           │ │ (+ Redactor) │        │   │ │
│   │  │  - tools/call    │    │  └──────────┘ └──────────────┘        │   │ │
│   │  │                   │    │                                        │   │ │
│   │  └──────────────────┘    │  ┌──────────┐ ┌──────────────┐        │   │ │
│   │                           │  │ DbQuery  │ │ SystemInfo   │        │   │ │
│   │  ┌──────────────────┐    │  │(psycopg2)│ │ (subprocess) │        │   │ │
│   │  │  Rate Limiter     │    │  └──────────┘ └──────────────┘        │   │ │
│   │  │  Audit Logger     │    └────────────────────────────────────────┘   │ │
│   │  └──────────────────┘                                                  │ │
│   └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│   ┌────────────────────┐    ┌──────────────────┐    ┌──────────────┐        │
│   │  /var/log/          │    │  /etc/nginx/      │    │  PostgreSQL  │        │
│   │  nginx, app, pg     │    │  postgresql, etc  │    │  (read-only) │        │
│   └────────────────────┘    └──────────────────┘    └──────────────┘        │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. MCP Proxy (mcp-client/)

Local MCP server on the developer machine.

**Purpose:** Cursor does not speak SSH. MCP Proxy bridges Cursor and remote servers.

**Protocol:** stdio (JSON-RPC 2.0) — Cursor starts it as a subprocess.

**Two tools for Cursor (dispatch model):** instead of exposing dozens of remote tools with prefixes (`production__logs_tail`, etc.), the proxy exposes only **`serverlens_list`** and **`serverlens_call`**. Real ServerLens tools are invoked via the latter — with server name and tool name.

**Nested navigation:**
- `serverlens_list()` — list configured servers
- `serverlens_list({ server: "my-server" })` — list tools on that server (same as remote `tools/list`)
- `serverlens_call({ server: "my-server", tool: "db_query", arguments: { ... } })` — run a tool on the server

**Responsibilities:**
- Read local configuration with SSH parameters
- Open SSH connections to servers (keepalive: `ServerAliveInterval=15`, `ServerAliveCountMax=3`)
- Start ServerLens in stdio mode on each server
- On SSH drop — automatic reconnect and restore session with remote MCP
- Map `serverlens_call` to JSON-RPC `tools/call` on the right server
- Return responses to Cursor

**Lifecycle:**
1. Cursor starts `serverlens-mcp` → SSH sessions come up → ServerLens (stdio) on each server
2. Cursor calls `serverlens_list()` — sees servers; optionally `serverlens_list({ server })` — tools on that server
3. For actions — `serverlens_call({ server, tool, arguments })`; proxy routes to the SSH channel and forwards `tools/call` to remote ServerLens
4. Response goes back to Cursor; if SSH fails, proxy reconnects and retries on the next call

### 2. ServerLens (server-side)

MCP server running on the remote host.

**Protocol:** stdio (for MCP Proxy) or SSE (for direct access).

**Modules:**

| Module | Role | Data source |
|--------|------|-------------|
| **LogReader** | Read logs | Whitelisted files |
| **ConfigReader** | Read configs (secrets redacted) | Whitelisted files |
| **DbQuery** | Safe database queries | PostgreSQL (read-only user) |
| **SystemInfo** | System state | shell_exec (systemctl, docker, free, df) |

**Security:**
- Whitelist model: only explicitly allowed resources
- No raw SQL — structured queries via whitelisted fields only
- Automatic redaction of secrets (passwords, keys, tokens)
- Read-only PostgreSQL user
- Rate limiting + audit logging

---

## Data flow

### Request: “Show the latest nginx errors”

```
1. Cursor: "Show the latest nginx errors"
   │
2. Claude/AI (via dispatch):
   │  serverlens_call({
   │    server: "production",
   │    tool: "logs_search",
   │    arguments: { source: "nginx_error", query: "error", lines: 50 }
   │  })
   │
3. MCP Proxy (local):
   │  ├── Routes to SSH session "production"
   │  ├── Forwards tools/call → logs_search(...) over JSON-RPC via SSH
   │  │
4. ServerLens (on server):
   │  ├── Rate limiter: OK
   │  ├── Checks "nginx_error" in whitelist: OK
   │  ├── Opens /var/log/nginx/error.log (read-only)
   │  ├── Finds lines containing "error"
   │  ├── Limits to 50 lines
   │  ├── Writes audit log
   │  └── Returns result
   │
5. MCP Proxy → Cursor → AI shows the user
```

### Request: “How many users in March?”

```
1. Cursor: "How many users registered in March?"
   │
2. AI:
   │  serverlens_call({
   │    server: "production",
   │    tool: "db_count",
   │    arguments: {
   │      database: "app_prod",
   │      table: "users",
   │      filters: { created_at: { gte: "2026-03-01", lt: "2026-04-01" } }
   │    }
   │  })
   │
3. MCP Proxy → tools/call on server → SSH →
   │
4. ServerLens:
   │  ├── Checks "myapp_prod" in whitelist: OK
   │  ├── Checks "users" in whitelist: OK
   │  ├── Checks "created_at" in allowed_filters: OK
   │  ├── Builds SQL: SELECT COUNT(*) FROM "users" WHERE "created_at" >= $1 AND "created_at" < $2
   │  ├── Executes via PDO (prepared statement, read-only user)
   │  ├── Writes audit log (without filter values)
   │  └── Returns {"count": 1234}
   │
5. MCP Proxy → Cursor → "1234 users registered in March"
```

---

## Transports

### stdio (recommended)

```
Cursor ←stdin/stdout→ MCP Proxy ←SSH stdin/stdout→ ServerLens
```

- Each message is one JSON object per line
- No extra framing layer
- SSH provides encryption and authentication
- No open ports
- No tokens required (SSH key = authentication)

### SSE (alternative)

```
MCP client ←HTTP SSE→ ServerLens (via SSH tunnel)
```

- GET /sse — SSE stream (server → client)
- POST /message?sessionId=xxx — messages (client → server)
- Bearer token for authentication
- Requires SSH tunnel: `ssh -L 9600:127.0.0.1:9600 user@server`

---

## Repository layout

```
serverlens/
├── README.md
│
├── serverlens/                 # Server (ServerLens, Python)
│   ├── __init__.py
│   ├── __main__.py             # Entry point
│   ├── application.py
│   ├── config.py
│   ├── mcp/
│   │   ├── server.py           # MCP protocol
│   │   └── tool.py
│   ├── transport/
│   │   ├── base.py
│   │   ├── sse.py              # SSE (aiohttp)
│   │   └── stdio.py            # stdio
│   ├── auth/
│   │   ├── token_auth.py       # Bearer token (argon2id)
│   │   └── rate_limiter.py
│   ├── module/
│   │   ├── base.py
│   │   ├── log_reader.py       # Logs
│   │   ├── config_reader.py    # Configs
│   │   ├── db_query.py         # PostgreSQL
│   │   └── system_info.py      # System information
│   ├── security/
│   │   ├── path_guard.py       # Path traversal protection
│   │   └── redactor.py         # Secret redaction
│   └── audit/
│       └── audit_logger.py
│
├── serverlens_mcp/             # Python MCP proxy (alternative)
│   ├── __main__.py
│   ├── config.py
│   ├── mcp_proxy.py
│   └── ssh_connection.py
│
├── pyproject.toml              # Server dependencies (Python)
├── requirements.txt
├── config.example.yaml         # Example server config
│
├── src/                        # Server (PHP, legacy)
├── bin/serverlens              # Server CLI (PHP, legacy)
├── composer.json               # Server dependencies (PHP)
│
├── mcp-client/                 # MCP proxy (developer machine, PHP)
│   ├── src/
│   │   ├── Config.php
│   │   ├── SshConnection.php   # SSH connection
│   │   └── McpProxy.php        # MCP proxy
│   ├── bin/serverlens-mcp      # Client CLI
│   ├── composer.json
│   ├── config.example.yaml     # SSH configuration
│   └── docs/
│       └── README.md           # MCP client docs
│
├── docs/                       # Documentation
│   ├── architecture.md         # This document
│   └── server/
│       ├── setup.md            # Server installation
│       └── api.md              # API reference
│
├── scripts/
│   ├── install.sh              # Server install
│   ├── uninstall.sh            # Server uninstall
│   ├── setup_db.sh             # PostgreSQL setup
│   ├── setup_db_users.sql      # PostgreSQL SQL template
│   └── change_db_password.sh   # DB password rotation
│
└── etc/
    └── serverlens.service      # systemd unit
```

---

## Technology stack

### Server (remote)

| Piece | Technology | Rationale |
|-------|------------|-----------|
| Language | **Python 3.10+** | Modern async, type hints, wide availability |
| MCP | **JSON-RPC 2.0** | Standard MCP protocol |
| HTTP (SSE) | **aiohttp** | Async HTTP for long-lived SSE |
| Configuration | **PyYAML** | Standard YAML parser for Python |
| Database | **psycopg2** | PostgreSQL adapter, parameterized queries |
| Hashing | **argon2-cffi** | Argon2id token hashing |
| Process manager | **systemd** | Standard Linux service management |

### MCP proxy (developer machine)

| Piece | Technology | Rationale |
|-------|------------|-----------|
| Language | **PHP 8.1+** | Lightweight stdio proxy |
| Configuration | **Symfony YAML** | YAML config parsing |
| SSH | **openssh-client** (via proc_open) | Standard SSH, no PHP extensions |
