# ServerLens MCP proxy — Documentation

> **Prerequisite:** ServerLens must already be installed on the remote server.
> If not, follow [docs/quickstart.md](../../docs/quickstart.md) first (steps 1–4).

A local MCP server that runs on the developer machine and connects to remote ServerLens instances over SSH.

## How it works

```
┌─────────────┐    stdio    ┌──────────────────┐    SSH     ┌──────────────┐
│   Cursor /   │◄──────────►│  ServerLens MCP   │◄─────────►│  ServerLens   │
│   Claude     │            │  (on your machine)│            │  (on server)  │
└─────────────┘             └──────────────────┘            └──────────────┘
```

1. **Cursor** starts `serverlens-mcp` as an stdio MCP server.
2. The **MCP client** opens an SSH session to the remote server (keepalive built in: `ServerAliveInterval=15`).
3. **ServerLens** is started over SSH in stdio mode.
4. Cursor sees **two MCP tools** — not dozens of prefixed names, but a single **dispatcher**:
   - **`serverlens_list`** — list of connected servers and remote tools (logs, DB, configs, system, etc.).
   - **`serverlens_call`** — invoke a specific tool on a chosen server: pass `server`, `tool`, and parameters as on the remote ServerLens.
5. Responses return over SSH.

**v2 (dispatch) model:** previously each remote tool was exported with a prefix (`production__logs_tail`, `staging__db_query`, and many others). Now MCP always exposes exactly **2 tools**; server and tool name are chosen inside `serverlens_call` arguments. This keeps the Cursor tool list short and avoids inflating MCP tool count when using several servers.

Cursor does not know about SSH — it talks to the local MCP server only.

If the connection drops, the MCP client may **automatically reconnect** to remote servers (SSH sessions and re-initialization).

---

## Installation

### 1. Clone the repository

```bash
git clone git@github.com:yourorg/serverlens.git
cd serverlens/mcp-client
composer install
```

### 2. Create configuration

```bash
mkdir -p ~/.serverlens
cp config.example.yaml ~/.serverlens/config.yaml
```

Edit `~/.serverlens/config.yaml`:

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"        # server IP or hostname
      user: "deploy"          # SSH user
      port: 22
      key: "~/.ssh/id_ed25519"
    remote:
      php: "php"
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"
```

The key under `servers` (e.g. `production`) is the **server id** for the `server` argument to `serverlens_call`. Multiple servers are multiple keys under `servers:`; MCP tool name prefixes are no longer used.

> **Tip:** you can define all servers in one `config.yaml` and use `--servers` to select a subset per project (see section 4).

### 3. Verify SSH access

Confirm the SSH key works:

```bash
ssh -i ~/.ssh/id_ed25519 deploy@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config"
```

### 4. Connect to Cursor

Add to `~/.cursor/mcp.json` (global — all servers):

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/full/path/to/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/Users/your_username/.serverlens/config.yaml"
      ]
    }
  }
}
```

**Per-project configuration** — if you only need specific servers in a project, create `.cursor/mcp.json` in the project root and use `--servers`:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/full/path/to/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/Users/your_username/.serverlens/config.yaml",
        "--servers",
        "production"
      ]
    }
  }
}
```

Multiple servers — pass comma-separated names:

```json
"--servers", "production,staging"
```

Server names must match keys under `servers:` in `config.yaml`. If `--servers` is omitted, all servers from config are connected.

Restart Cursor. ServerLens appears in the list of available MCP servers.

---

## Configuration

The config lists one or more servers — each with an `ssh` and `remote` block.

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"
      user: "deploy"
      key: "~/.ssh/id_ed25519"
    remote:
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"

  staging:
    ssh:
      host: "5.6.7.8"
      user: "deploy"
      key: "~/.ssh/id_ed25519"
    remote:
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"
```

### SSH parameters

```yaml
ssh:
  host: "1.2.3.4"              # required
  user: "deploy"                # required
  port: 22                      # default 22
  key: "~/.ssh/id_ed25519"      # path to SSH key (~ expanded)
  options:                       # extra SSH options (optional)
    ConnectTimeout: "10"         # connect timeout
    ServerAliveInterval: "30"    # override keepalive (client default is 15s)
    ServerAliveCountMax: "3"     # max missed keepalives
```

SSH keepalive is **built into** the MCP client (`ServerAliveInterval=15`). Use `options` when you need different values or extra `ssh` options.

### Remote server parameters

```yaml
remote:
  php: "php"                                         # PHP path (default "php")
  serverlens_path: "/opt/serverlens/bin/serverlens"  # ServerLens binary
  config_path: "/etc/serverlens/config.yaml"         # ServerLens config path
```

---

## Per-project server selection (`--servers`)

A single `config.yaml` can list all your servers. The `--servers` flag selects which ones to connect in a particular project.

### How it works

| In `args` | What happens |
|-----------|------------|
| `--servers` not set | All servers from `config.yaml` are connected |
| `--servers`, `"production"` | Only `production` is connected |
| `--servers`, `"production,staging"` | Both `production` and `staging` are connected |

### Example: global config with 5 servers

`~/.serverlens/config.yaml`:
```yaml
servers:
  service-book:
    ssh: { host: "1.2.3.4", user: "deploy", key: "~/.ssh/id_ed25519" }
    remote: { serverlens_path: "/opt/serverlens/bin/serverlens", config_path: "/etc/serverlens/config.yaml" }
  rias:
    ssh: { host: "5.6.7.8", user: "deploy", key: "~/.ssh/id_ed25519" }
    remote: { serverlens_path: "/opt/serverlens/bin/serverlens", config_path: "/etc/serverlens/config.yaml" }
  rias-test:
    ssh: { host: "9.10.11.12", user: "deploy", key: "~/.ssh/id_ed25519" }
    remote: { serverlens_path: "/opt/serverlens/bin/serverlens", config_path: "/etc/serverlens/config.yaml" }
  # ... more servers
```

### Project A — needs only `service-book`

`.cursor/mcp.json` in project root:
```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/path/to/mcp-client/bin/serverlens-mcp",
        "--config", "/Users/you/.serverlens/config.yaml",
        "--servers", "service-book"
      ]
    }
  }
}
```

### Project B — needs `rias` and `rias-test`

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/path/to/mcp-client/bin/serverlens-mcp",
        "--config", "/Users/you/.serverlens/config.yaml",
        "--servers", "rias,rias-test"
      ]
    }
  }
}
```

### Validation

If a server name in `--servers` does not exist in `config.yaml`, the MCP client exits with an error:

```
Fatal: Unknown server(s) in --servers: typo-name. Available in config: service-book, rias, rias-test
```

---

## Available tools

On the MCP side, Cursor shows **only two tools**:

| Tool | Purpose |
|------------|------------|
| `serverlens_list` | Returns configured servers and the **full catalog of remote tools** from each ServerLens (logs, configs, DB, system, etc.) — what used to be duplicated with prefixes is aggregated here. |
| `serverlens_call` | Runs one remote tool: arguments include `server` (name from `config.yaml`), `tool` (e.g. `logs_tail`, `db_query`), and parameters as in [ServerLens API](../../docs/server/api.md). |

Actual operations (`logs_tail`, `logs_search`, `db_query`, `config_read`, `system_docker`, etc.) still run **on the remote ServerLens**; only the MCP invocation path changes — via the `serverlens_call` dispatcher instead of a separate MCP name per server+tool pair.

---

## Example prompts in Cursor

After MCP is connected, you can ask in natural language:

- *“Show the latest nginx errors”* → the assistant calls the right tool via `serverlens_call` (e.g. `logs_search`).
- *“How many users signed up in March?”* → `db_count` on the chosen server.
- *“Show PostgreSQL configuration”* → `config_read`.
- *“What is the status of Docker containers?”* → `system_docker`.
- *“Find upstream timed out in the logs”* → `logs_search`.

From the user’s perspective the behavior is the same; internally MCP naming uses v2 (two tools and dispatch).

---

## Troubleshooting

### MCP does not connect

```bash
# Check that the script runs:
php mcp-client/bin/serverlens-mcp --config ~/.serverlens/config.yaml

# stderr may show (example):
# [MCP] Config: /Users/.../.serverlens/config.yaml
# [MCP] Connecting to server 'production'...
# [MCP:production] SSH command: ssh -o BatchMode=yes ...
# [MCP:production] Initialized: ServerLens v1.0.0
# [MCP] Discovered 17 tools on 'production'
# [MCP] Ready: 1 server(s), 17 remote tool(s), 2 MCP tools
```

With multiple servers, the first number in `Ready` is server count, the second is total remote tools; **MCP tools** is always **2** (`serverlens_list` and `serverlens_call`).

### SSH does not connect

```bash
# Test SSH manually:
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 deploy@1.2.3.4 echo "ok"

# Test ServerLens on the server:
ssh deploy@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config"
```

### Tools do not appear in Cursor

1. Check `~/.cursor/mcp.json` — paths must be **absolute**
2. Restart Cursor after changing configuration
3. Check MCP logs in Cursor’s terminal (Output → MCP)

---

## Related documents

| Document | Description |
|----------|----------|
| [Quickstart (from scratch)](../../docs/quickstart.md) | End-to-end installation |
| [Server setup](../../docs/server/setup.md) | Detailed ServerLens configuration |
| [API Reference](../../docs/server/api.md) | All tools reference |
| [Architecture](../../docs/architecture.md) | How the system fits together |
