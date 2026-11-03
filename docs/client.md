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

Everything below happens **on your machine** (the developer laptop), not on the server.

> Want an AI agent to do it? Give Claude Code (or Cursor / Codex) [llm-install.md](llm-install.md) — it is the same procedure written as instructions for the agent.

### The three paths you will need

An MCP config for ServerLens always contains the same three absolute paths. Here is what each one is and where it comes from:

| # | What | Where it is | Example (macOS) | Example (Linux) |
|---|------|-------------|-----------------|-----------------|
| 1 | **Python of the venv** — goes into `command` | `<repo>/venv/bin/python`, created in step 2 | `/Users/anna/serverlens/venv/bin/python` | `/home/anna/serverlens/venv/bin/python` |
| 2 | **Repository root** — goes into `PYTHONPATH` | the folder you cloned in step 1, the one that contains `serverlens_mcp/` | `/Users/anna/serverlens` | `/home/anna/serverlens` |
| 3 | **Client config** — goes after `--config` | `~/.serverlens/config.yaml`, created in step 4 | `/Users/anna/.serverlens/config.yaml` | `/home/anna/.serverlens/config.yaml` |

Path 1 lives **inside** path 2: the venv is just a `venv/` folder in the repository. So once you know where you cloned the repo, you know both.

`~` means your home directory (`echo $HOME` prints it: `/Users/<name>` on macOS, `/home/<name>` on Linux). MCP configs do **not** expand `~` or `$HOME`, so the final config must contain the full path. Step 5 prints all three paths for you — you don't have to type them.

> The examples assume the repository is cloned to `~/serverlens`. Any other folder works too — just run the commands from it.

### 1. Clone the repository

```bash
git clone <repo-url> ~/serverlens
cd ~/serverlens
pwd          # ← this is path 2, the repository root
ls           # you should see serverlens_mcp/ here
```

The client is not installed system-wide: it runs straight from this folder. Don't delete or move the folder after setup — if you move it, redo steps 2 and 5.

### 2. Create a venv with PyYAML

The client needs Python **3.10+** and one library, PyYAML. Check which Python you have:

```bash
python3 --version     # must be 3.10 or newer
which python3         # where it lives, e.g. /opt/homebrew/bin/python3 or /usr/bin/python3
```

If `python3` is missing or older than 3.10:
- **macOS:** `brew install python` (Homebrew) — or install from python.org. The `python3` from Xcode Command Line Tools also works if it is 3.10+.
- **Ubuntu / Debian:** `sudo apt install python3 python3-venv`

Now create the venv **inside the repository folder**:

```bash
cd ~/serverlens
python3 -m venv venv
venv/bin/pip install pyyaml
```

What happens:
- `python3 -m venv venv` creates the folder `~/serverlens/venv/` — a private Python environment just for this client. It does not touch your system Python.
- `venv/bin/python` (path 1) is the interpreter of that environment. It is the one that sees PyYAML, so the MCP config must point to it — not to `python3`, not to `/usr/bin/python3`.
- `venv/bin/pip install pyyaml` installs PyYAML into `venv/` only.

Why not a plain `pip install pyyaml`: Homebrew Python and recent Linux distributions refuse a global install (`externally-managed-environment`), and the macOS system `python3` has no PyYAML.

> Don't run `pip install -r requirements.txt` here — those are the **server** dependencies (aiohttp, psycopg2…), the client doesn't need them.

Check that the client starts:

```bash
cd ~/serverlens
PYTHONPATH="$PWD" venv/bin/python -m serverlens_mcp --help
```

You should see a help text that starts with `ServerLens MCP Proxy`. `PYTHONPATH` tells Python where to find the `serverlens_mcp` package (the repository root); the MCP config sets the same variable in its `env` block.

> If you later upgrade or remove the Python that created the venv (e.g. `brew upgrade python`), the venv may stop working. Fix: `rm -rf ~/serverlens/venv` and repeat this step.

### 3. Check SSH access to the server

The client logs in with the regular `ssh` command, without prompts. Make sure this works **from your machine**:

```bash
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 deploy@1.2.3.4 "serverlens validate-config"
```

Replace the key, user and host with yours. `BatchMode=yes` forbids password prompts — if the command asks for a password or fails, the MCP client will fail the same way (see [Troubleshooting](#troubleshooting)).

### 4. Create the client config

This file lists the servers the client may connect to. Its default location is `~/.serverlens/config.yaml` (path 3):

```bash
mkdir -p ~/.serverlens
cp ~/serverlens/serverlens_mcp/config.example.yaml ~/.serverlens/config.yaml
open -e ~/.serverlens/config.yaml     # macOS; on Linux: nano ~/.serverlens/config.yaml
```

Fill in the server — the same host, user and key that worked in step 3:

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"        # server IP, hostname or ~/.ssh/config alias
      user: "deploy"          # SSH user
      port: 22
      key: "~/.ssh/id_ed25519"   # here ~ is fine: the client expands it
```

The key under `servers` (e.g. `production`) is the **server name** used in `serverlens_call` and `--servers`. All options: [Configuration](#configuration).

Check that the client reads the config:

```bash
cd ~/serverlens
PYTHONPATH="$PWD" venv/bin/python -m serverlens_mcp --config ~/.serverlens/config.yaml < /dev/null
# [MCP] Config: /Users/anna/.serverlens/config.yaml
# [MCP] Ready (lazy connect): 1 server(s) configured (production), 2 MCP tools
```

(`< /dev/null` makes the client exit right away instead of waiting for an MCP tool to talk to it.)

### 5. Get your paths

Run this from the repository folder — it prints the three paths and checks that each exists:

```bash
cd ~/serverlens
for p in "$PWD/venv/bin/python" "$PWD" "$HOME/.serverlens/config.yaml"; do
  [ -e "$p" ] && echo "OK      $p" || echo "MISSING $p"
done
```

Example output:

```
OK      /Users/anna/serverlens/venv/bin/python      ← path 1: "command"
OK      /Users/anna/serverlens                      ← path 2: "PYTHONPATH"
OK      /Users/anna/.serverlens/config.yaml         ← path 3: after "--config"
```

If any line says `MISSING`, go back to the step that creates it (2, 1 or 4).

### 6. Connect to your LLM tool

Pick your tool. Each command below is run **from the repository folder** (`cd ~/serverlens`) — it substitutes your real paths automatically.

#### Claude Code

```bash
cd ~/serverlens
claude mcp add serverlens --scope user \
  -e PYTHONPATH="$PWD" \
  -- "$PWD/venv/bin/python" -m serverlens_mcp --config "$HOME/.serverlens/config.yaml"
```

`--scope user` makes it available in all projects; drop it to add the server only to the current project. Check with `claude mcp list` — `serverlens` should be `✓ Connected`.

#### Cursor, Claude Desktop, project `.mcp.json`

These tools read a JSON file. Print a ready block with your paths:

```bash
cd ~/serverlens
cat <<JSON
{
  "mcpServers": {
    "serverlens": {
      "command": "$PWD/venv/bin/python",
      "args": [
        "-m", "serverlens_mcp",
        "--config", "$HOME/.serverlens/config.yaml"
      ],
      "env": { "PYTHONPATH": "$PWD" }
    }
  }
}
JSON
```

Copy the output into the right file:

| Tool | File |
|------|------|
| Cursor, all projects | `~/.cursor/mcp.json` |
| Cursor, one project | `.cursor/mcp.json` in the project root |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Linux) | `~/.config/Claude/claude_desktop_config.json` |
| Claude Code, shared with the team | `.mcp.json` in the project root |

If the file already has other servers, add only the `"serverlens": { ... }` part inside the existing `"mcpServers"` object.

The result should look like this (with your user name instead of `anna`):

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "/Users/anna/serverlens/venv/bin/python",
      "args": [
        "-m", "serverlens_mcp",
        "--config", "/Users/anna/.serverlens/config.yaml"
      ],
      "env": { "PYTHONPATH": "/Users/anna/serverlens" }
    }
  }
}
```

#### OpenAI Codex CLI

Print a block for `~/.codex/config.toml`:

```bash
cd ~/serverlens
cat <<TOML
[mcp_servers.serverlens]
command = "$PWD/venv/bin/python"
args = ["-m", "serverlens_mcp", "--config", "$HOME/.serverlens/config.yaml"]
env = { PYTHONPATH = "$PWD" }
TOML
```

Append the output to `~/.codex/config.toml`.

#### After connecting

Restart the tool (Cursor, Claude Desktop, Codex) so it picks up the new config. Then ask something like *"call serverlens_list"* — you should get the list of servers from your `config.yaml`. If not, see [Troubleshooting](#troubleshooting).

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

`PYTHONPATH` is missing or doesn't point to the repository root (the directory that contains `serverlens_mcp/`). Check the `env` block in your MCP config. The check in [step 5](#5-get-your-paths) prints the correct paths.

### `No module named yaml`

PyYAML isn't installed for the Python in `command`. Run `~/serverlens/venv/bin/pip install pyyaml` and make sure `command` points to `venv/bin/python`. The check in [step 5](#5-get-your-paths) prints the correct paths.

### `Config not found` / `Error: config not found`

The `--config` path is wrong or relative / starts with `~`. Use an absolute path. The check in [step 5](#5-get-your-paths) prints the correct paths.

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
