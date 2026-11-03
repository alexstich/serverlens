# ServerLens MCP client — installation guide for an AI agent

This document is written for an AI coding agent (Claude Code, Cursor agent, Codex CLI, …) that installs the ServerLens **MCP client** on the user's machine and connects it to the user's LLM tool. A human can follow it too, but [client.md](client.md) is easier to read.

It covers the client only. ServerLens must already be installed on the remote server — if it isn't, see [quickstart.md](quickstart.md), steps 1–3.

## For the human: how to use this

Open your agent on your own machine (not on the server) and send it:

> Install the ServerLens MCP client by following `docs/llm-install.md` from `<repo-url>`. My server: host `1.2.3.4`, SSH user `deploy`, key `~/.ssh/id_ed25519`.

If you already have a clone, run the agent in it and say "follow docs/llm-install.md". The agent asks for whatever is missing.

---

## For the agent

### What you are setting up

```
LLM tool (on the user's machine)
   │ stdio
   ▼
serverlens_mcp  — Python client, runs from a git clone on the user's machine
   │ ssh (key auth, no prompts)
   ▼
serverlens serve --stdio  — on the remote server, installed in /opt/serverlens
```

- **Client** (`serverlens_mcp/`): on the user's machine. Needs Python 3.10+, PyYAML, OpenSSH client. **This is what you install.**
- **Server**: already installed on the remote host (`/usr/local/bin/serverlens`). You only check that it is reachable; you don't install or change anything on the server.

The MCP config of the LLM tool needs three **absolute** paths:

| Name | Value | Used as |
|------|-------|---------|
| `REPO` | absolute path of the client clone, the folder that contains `serverlens_mcp/` | `PYTHONPATH` |
| `PY` | `$REPO/venv/bin/python` | `command` |
| `CFG` | `$HOME/.serverlens/config.yaml` | value after `--config` |

MCP configs do not expand `~` or `$HOME` — always write the full path.

### Rules

1. **Ask, don't guess** server details: host, SSH user, port, key path, and the server name to use in the config. If the user gave only a host, ask for the rest; offer `~/.ssh/config` aliases you find (`grep -i '^host ' ~/.ssh/config`) as options, but let the user confirm.
2. **Don't change anything on the remote server.** Over SSH you only run the read-only check from step 3. Never run `sudo` there and never ask for a password.
3. **Don't read or print private keys.** Checking that a key file exists (`test -f`) is fine.
4. **Don't overwrite existing configs.** Before editing `~/.cursor/mcp.json`, `claude_desktop_config.json`, `~/.codex/config.toml` or `~/.serverlens/config.yaml`, back it up (`cp file file.bak.$(date +%s)`) and merge — keep other servers.
5. **Verify every step** with the check given below before moving on. If a check fails, use [Fixes](#fixes); don't skip ahead.
6. Don't run `pip install -r requirements.txt` or `pip install .` on the user's machine — those are server dependencies. The client needs only PyYAML.

### Step 0. Collect inputs

You need:

- server name for the config (short, e.g. `production`) — one per server;
- `host`, `user`, `port` (default 22), `key` (optional if `~/.ssh/config` or ssh-agent handles it);
- which LLM tool to connect. If you are Claude Code, default to Claude Code; otherwise ask.

Check prerequisites on the user's machine:

```bash
python3 --version   # need 3.10+
which python3
which ssh git
```

If Python is missing or older than 3.10, stop and tell the user how to install it (macOS: `brew install python`; Ubuntu/Debian: `sudo apt install python3 python3-venv`). Don't install it yourself without the user's consent.

### Step 1. Get the client code

If the current directory already contains `serverlens_mcp/__main__.py`, use it as `REPO`. Otherwise clone (ask for the URL if the user didn't give one):

```bash
git clone <repo-url> ~/serverlens
cd ~/serverlens
```

Set and record `REPO`:

```bash
cd ~/serverlens && REPO="$PWD" && echo "$REPO" && test -f "$REPO/serverlens_mcp/__main__.py" && echo OK
```

Shell variables don't survive between your tool calls in many agents — re-derive `REPO` with `cd <path> && REPO="$PWD"` in every command, or substitute the literal path.

### Step 2. Create the venv

```bash
cd "$REPO"
python3 -m venv venv
venv/bin/pip install -q pyyaml
PYTHONPATH="$PWD" venv/bin/python -m serverlens_mcp --help | head -1
```

Expected: `ServerLens MCP Proxy — local MCP server for Cursor`. The venv lives in `$REPO/venv`; `PY` is `$REPO/venv/bin/python`.

If `venv` already exists, reuse it: just run the `pip install` and the check. If the check fails with a broken interpreter (`bad interpreter`, `No such file`), `rm -rf "$REPO/venv"` and redo the step.

### Step 3. Check SSH access to the server

For each server:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -p PORT -i KEY USER@HOST \
  'command -v serverlens && serverlens validate-config'
```

Omit `-i KEY` if the user has no explicit key. Read the result:

| Output | Meaning | Next |
|--------|---------|------|
| `/usr/local/bin/serverlens` + `Configuration is valid: /etc/serverlens/config.yaml` | Server is ready | Step 4 |
| `Permission denied (publickey…)` / `Host key verification failed` | SSH itself fails | [Fixes → SSH](#ssh-fails). The client uses the same SSH, so nothing will work until this passes |
| empty output, exit code 1 (`command -v` found nothing) | ServerLens is not installed on the server | Stop. Tell the user to install it first ([quickstart.md](quickstart.md), steps 1–3) and come back |
| `Configuration error: … Permission denied` | Installed, but the SSH user can't read `/etc/serverlens/config.yaml` | [Fixes → groups](#ssh-user-cant-read-the-config-or-logs) |

### Step 4. Write the client config

```bash
mkdir -p ~/.serverlens
test -f ~/.serverlens/config.yaml && cp ~/.serverlens/config.yaml ~/.serverlens/config.yaml.bak.$(date +%s)
```

If the file exists, add the new server under `servers:` and keep the others. Otherwise create it:

```yaml
servers:
  production:                 # server name chosen in step 0
    ssh:
      host: "1.2.3.4"
      user: "deploy"
      port: 22
      key: "~/.ssh/id_ed25519"  # ~ is fine here; omit the line to use ~/.ssh/config / ssh-agent
```

The template with all options is `$REPO/serverlens_mcp/config.example.yaml`; reference: [client.md → Configuration](client.md#configuration). Note: `port` is always passed as `ssh -p`, so a `Port` in `~/.ssh/config` is ignored — set `port` here.

Check:

```bash
cd "$REPO"
PYTHONPATH="$PWD" venv/bin/python -m serverlens_mcp --config "$HOME/.serverlens/config.yaml" < /dev/null
```

Expected on stderr: `[MCP] Ready (lazy connect): N server(s) configured (production, …), 2 MCP tools`.

### Step 5. Check the full chain without the LLM tool

This sends real MCP requests through the client to the server — do it before touching the tool's config:

```bash
cd "$REPO"
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"serverlens_list","arguments":{"server":"production"}}}' \
| PYTHONPATH="$PWD" venv/bin/python -m serverlens_mcp --config "$HOME/.serverlens/config.yaml"
```

Replace `production` with the server name. Expected: the response with `"id":2` lists the server's tools (`logs_tail`, `system_overview`, …), and stderr shows `Initialized: ServerLens v…` and `Discovered N tools on 'production'`. An SSH or permission error here means the problem is between the client and the server — fix it now, not in the LLM tool.

### Step 6. Connect the LLM tool

Use real absolute paths. Back up any file you edit.

**Claude Code:**

```bash
cd "$REPO"
claude mcp add serverlens --scope user \
  -e PYTHONPATH="$PWD" \
  -- "$PWD/venv/bin/python" -m serverlens_mcp --config "$HOME/.serverlens/config.yaml"
claude mcp list
```

If `serverlens` already exists, show the user the current entry (`claude mcp get serverlens`) and ask before `claude mcp remove serverlens --scope user` and re-adding. Use `--scope user` unless the user wants it in one project only.

**Cursor / Claude Desktop / `.mcp.json`:** merge the entry into the JSON file without losing other servers:

```bash
cd "$REPO"
FILE="$HOME/.cursor/mcp.json"   # see the table below
mkdir -p "$(dirname "$FILE")"
test -f "$FILE" && cp "$FILE" "$FILE.bak.$(date +%s)"
venv/bin/python - "$FILE" "$PWD" "$HOME/.serverlens/config.yaml" <<'PY'
import json, os, sys
path, repo, cfg = sys.argv[1:]
data = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
data.setdefault("mcpServers", {})["serverlens"] = {
    "command": f"{repo}/venv/bin/python",
    "args": ["-m", "serverlens_mcp", "--config", cfg],
    "env": {"PYTHONPATH": repo},
}
json.dump(data, open(path, "w"), indent=2)
print(json.dumps(data["mcpServers"]["serverlens"], indent=2))
PY
```

| Tool | `FILE` |
|------|--------|
| Cursor, all projects | `~/.cursor/mcp.json` |
| Cursor, one project | `<project>/.cursor/mcp.json` |
| Claude Desktop, macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop, Linux | `~/.config/Claude/claude_desktop_config.json` |
| Claude Code, shared with the team | `<project>/.mcp.json` |

If the existing file isn't valid JSON, the script fails — show the error to the user instead of rewriting the file.

To limit a project to some servers, add `"--servers", "production,staging"` to `args` ([client.md → `--servers`](client.md#per-project-server-selection---servers)).

**Codex CLI:** append to `~/.codex/config.toml` (after a backup; if `[mcp_servers.serverlens]` already exists, ask before replacing it):

```bash
cd "$REPO"
cat >> ~/.codex/config.toml <<TOML

[mcp_servers.serverlens]
command = "$PWD/venv/bin/python"
args = ["-m", "serverlens_mcp", "--config", "$HOME/.serverlens/config.yaml"]
env = { PYTHONPATH = "$PWD" }
TOML
```

### Step 7. Hand over to the user

The tool loads MCP servers at startup, so the new `serverlens_list` / `serverlens_call` tools usually appear only after a restart (Cursor, Claude Desktop, Codex) or in a new session (Claude Code). Tell the user:

1. restart the tool / start a new session;
2. ask it: *"call serverlens_list"* — it should list the servers from `~/.serverlens/config.yaml`;
3. then e.g. *"show the last nginx errors on production"*.

Finish with a short summary: `REPO`, `PY`, `CFG`, configured servers, which tool config you changed and where the backup is.

---

## Fixes

### SSH fails

- `Permission denied (publickey)`: wrong user or key, or the key isn't on the server. Ask the user to check `ssh -i KEY USER@HOST` by hand.
- Key has a passphrase: `BatchMode=yes` can't prompt. The user should add it to the agent: `ssh-add KEY` (macOS: `ssh-add --apple-use-keychain KEY`).
- `Host key verification failed`: the host isn't in `known_hosts`. The user connects once by hand (`ssh USER@HOST`) and confirms the fingerprint. Don't disable host key checking.
- Timeout: wrong host/port, VPN or firewall. Ask the user.

### SSH user can't read the config or logs

This is a server-side setting; tell the user. The server admin adds the SSH user to the `serverlens` group (config) and to the groups that own the logs (`adm`, `postgres`, …) — see [quickstart.md, step 2](quickstart.md#step-2-install-and-configure-serverlens). New SSH sessions pick up the groups; then repeat step 3.

### Client errors

| Error | Cause |
|-------|-------|
| `No module named serverlens_mcp` | `PYTHONPATH` doesn't point to `REPO` |
| `No module named yaml` | `command` is not `$REPO/venv/bin/python`, or PyYAML isn't installed in the venv |
| `Error: config not found` | `--config` path is relative or uses `~` |
| `Unknown server(s) in --servers: …` | `--servers` names don't match keys under `servers:` |
| tool shows the server as failed, but step 5 works | the tool's config differs from what step 5 ran — compare paths; in Claude Code see `claude mcp get serverlens` |

More: [client.md → Troubleshooting](client.md#troubleshooting).
