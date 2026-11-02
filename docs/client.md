# ServerLens MCP client — Documentation

> **Prerequisite:** ServerLens must already be installed on the remote server.
> If not, follow [quickstart.md](quickstart.md) first (steps 1–3).

The MCP client (`serverlens_mcp/`, Python) is a local MCP server that runs on the developer machine and connects to remote ServerLens instances over SSH.

> The old PHP client (`mcp-client/`, `composer install`, `php .../serverlens-mcp`) has been removed. If your `mcp.json` still points to it, switch to the setup below.

## How it works

```
┌─────────────┐    stdio    ┌──────────────────┐    SSH     ┌──────────────┐
│   Cursor /   │◄──────────►│  serverlens_mcp   │◄─────────►│  ServerLens   │
│ Claude Code  │            │  (on your machine)│            │  (on server)  │
└─────────────┘             └──────────────────┘            └──────────────┘
```

1. The LLM tool starts `python -m serverlens_mcp` as an stdio MCP server.
2. On the first call to a server, the client opens an SSH session to it (keepalive built in: `ServerAliveInterval=15`) and runs `serverlens serve --stdio` there.
3. The LLM tool sees **two MCP tools**:
   - **`serverlens_list`** — servers from the config; with `{ "server": "name" }` — tools on that server.
   - **`serverlens_call`** — run a tool on a server: `{ "server": "...", "tool": "...", "arguments": { ... } }`.
4. If the SSH connection drops, the client reconnects on the next call.

---

## Requirements

- Python **3.10+**
- PyYAML — the only third-party dependency of the client
- `ssh` (OpenSSH client) and an SSH key that can log in to the server

The client does **not** need the server dependencies (aiohttp, psycopg2, etc.) — don't run `pip install -r requirements.txt` on your machine.

---

## Installation

### 1. Clone the repository and create a venv

```bash
git clone <repo-url> ~/serverlens
cd ~/serverlens
python3 -m venv venv
venv/bin/pip install pyyaml
```

A venv is recommended: Homebrew Python and recent Linux distributions refuse a global `pip install` (`externally-managed-environment`), and the macOS system `python3` has no PyYAML.

Check that the client starts:

```bash
PYTHONPATH=~/serverlens ~/serverlens/venv/bin/python -m serverlens_mcp --help
```

### 2. Create the configuration

```bash
mkdir -p ~/.serverlens
cp ~/serverlens/serverlens_mcp/config.example.yaml ~/.serverlens/config.yaml
```

Edit `~/.serverlens/config.yaml`:

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"        # server IP, hostname or ~/.ssh/config alias
      user: "deploy"          # SSH user
      port: 22
      key: "~/.ssh/id_ed25519"
```

The key under `servers` (e.g. `production`) is the **server name** used in `serverlens_call` and `--servers`.

### 3. Verify SSH access

```bash
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 deploy@1.2.3.4 "serverlens validate-config"
```

### 4. Connect to your LLM tool

Replace `YOU` with your user name. Paths must be **absolute** — `~` is not expanded in `args` and `env`.

**Cursor** — `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in the project root:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "/Users/YOU/serverlens/venv/bin/python",
      "args": [
        "-m", "serverlens_mcp",
        "--config", "/Users/YOU/.serverlens/config.yaml"
      ],
      "env": { "PYTHONPATH": "/Users/YOU/serverlens" }
    }
  }
}
```

**Claude Code** — from the project directory (or add `--scope user` to make it global):

```bash
claude mcp add serverlens -e PYTHONPATH=/Users/YOU/serverlens -- /Users/YOU/serverlens/venv/bin/python -m serverlens_mcp --config /Users/YOU/.serverlens/config.yaml
```

The same JSON block also works in Claude Desktop (`claude_desktop_config.json`) and in a project `.mcp.json`.

**OpenAI Codex CLI** — `~/.codex/config.toml`:

```toml
[mcp_servers.serverlens]
command = "/Users/YOU/serverlens/venv/bin/python"
args = ["-m", "serverlens_mcp", "--config", "/Users/YOU/.serverlens/config.yaml"]
env = { PYTHONPATH = "/Users/YOU/serverlens" }
```

Restart the tool after changing its MCP configuration.

---

## Configuration

### SSH parameters

```yaml
ssh:
  host: "1.2.3.4"              # required
  user: "deploy"                # required
  port: 22                      # default 22
  key: "~/.ssh/id_ed25519"      # optional, ~ expanded; omit to use ~/.ssh/config or ssh-agent
  options:                       # extra ssh -o options (optional)
    ServerAliveInterval: "30"    # override keepalive (client default is 15s)
```

The client calls the system `ssh`, so `~/.ssh/config` is honoured (aliases, `IdentityFile`, `ProxyJump`). The port is always passed as `-p`, so a `Port` from `~/.ssh/config` is ignored — set `port` in this file.

### Remote command

By default the client runs `serverlens serve --stdio` on the server (the installer creates `/usr/local/bin/serverlens`). Override it only when needed:

```yaml
servers:
  custom:
    ssh: { host: "9.10.11.12", user: "deploy" }
    command: "/opt/serverlens/venv/bin/python -m serverlens serve --stdio --config /etc/serverlens/config.yaml"
```

The old `remote:` section (`serverlens_path`, `config_path`) still works but prints a deprecation warning — use `command` instead.

---

## Per-project server selection (`--servers`)

Define all servers once in `~/.serverlens/config.yaml` and pick a subset per project:

| In `args` | What happens |
|-----------|------------|
| `--servers` not set | All servers from the config are available |
| `"--servers", "production"` | Only `production` |
| `"--servers", "production,staging"` | `production` and `staging` |

Example `.cursor/mcp.json` in a project root:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "/Users/YOU/serverlens/venv/bin/python",
      "args": [
        "-m", "serverlens_mcp",
        "--config", "/Users/YOU/.serverlens/config.yaml",
        "--servers", "production,staging"
      ],
      "env": { "PYTHONPATH": "/Users/YOU/serverlens" }
    }
  }
}
```

An unknown name stops the client with an error:

```
Fatal: Unknown server(s) in --servers: typo-name. Available in config: production, staging
```

---

## Updating

```bash
cd ~/serverlens
git pull
```

Restart the LLM tool so it starts the new client code.

---

## Troubleshooting

### `No module named serverlens_mcp`

`PYTHONPATH` is missing or doesn't point to the repository root (the directory that contains `serverlens_mcp/`). Check the `env` block in your MCP config.

### `No module named yaml`

PyYAML isn't installed for the Python in `command`. Run `~/serverlens/venv/bin/pip install pyyaml` and make sure `command` points to `venv/bin/python`.

### `Config not found` / `Error: config not found`

The `--config` path is wrong or relative / starts with `~`. Use an absolute path.

### The client starts but a server doesn't respond

Run the client by hand and look at stderr:

```bash
PYTHONPATH=~/serverlens ~/serverlens/venv/bin/python -m serverlens_mcp --config ~/.serverlens/config.yaml
# [MCP] Config: /Users/.../.serverlens/config.yaml
# [MCP] Ready (lazy connect): 1 server(s) configured (production), 2 MCP tools
```

Servers are connected lazily, on the first `serverlens_call` / `serverlens_list({ server })`. Then the log shows:

```
[MCP:production] SSH command: ssh -o BatchMode=yes ...
[MCP:production] Initialized: ServerLens v2.0.0
[MCP] Discovered 17 tools on 'production'
```

If SSH fails, try the same thing manually:

```bash
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 deploy@1.2.3.4 "serverlens validate-config"
```

`BatchMode=yes` means no password prompts: the key must work without interaction (use ssh-agent for a key with a passphrase).

---

## Related documents

| Document | Description |
|----------|----------|
| [Quickstart (from scratch)](quickstart.md) | End-to-end installation |
| [Server setup](server/setup.md) | Detailed ServerLens configuration |
| [API Reference](server/api.md) | All tools reference |
| [Architecture](architecture.md) | How the system fits together |
