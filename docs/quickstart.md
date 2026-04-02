# ServerLens — Full installation from scratch

This document describes a **step-by-step** installation of the whole stack: from the server to a working MCP in Cursor.

```
Steps 1–3: ON THE REMOTE SERVER
Steps 4–6: ON YOUR MACHINE (developer)
```

---

## Step 1. Clone the repository on the server

SSH into the server and clone:

```bash
ssh user@1.2.3.4

git clone git@github.com:your-org/serverlens.git ~/serverlens-src
cd ~/serverlens-src
```

> `/opt` is owned by root, so clone into your home directory. The install script copies the required files into `/opt/serverlens`.

---

## Step 2. Install and configure ServerLens

**Option A — interactive installer (recommended):**

```bash
sudo bash scripts/install.sh
```

The installer does everything in one pass:
1. Checks PHP (version and extensions)
2. Creates the `serverlens` system user and directories
3. Installs dependencies (Composer)
4. **Runs the setup wizard:**
   - Scans installed services (nginx, PostgreSQL, Redis, PHP-FPM, Docker…)
   - Shows discovered log files and configs — you pick what you need
   - Offers PostgreSQL setup — choose database, tables, columns
   - Automatically detects sensitive columns (passwords, tokens) and hides them
   - Creates a read-only PostgreSQL user
   - **Generates a ready-to-use `config.yaml`**
5. Installs the systemd service

> After installation you rarely need to edit the config by hand — the wizard covers the main settings.

**Option A without the wizard** (for automation / CI):

```bash
sudo bash scripts/install.sh --no-wizard
```

Installs everything but copies `config.example.yaml` without interaction. You must fill the config manually.

**Option B — fully manual:**

```bash
sudo mkdir -p /opt/serverlens /etc/serverlens /var/log/serverlens
sudo cp -r src/ bin/ composer.json /opt/serverlens/
cd /opt/serverlens && sudo composer install --no-dev --optimize-autoloader
sudo chmod +x /opt/serverlens/bin/serverlens
sudo cp ~/serverlens-src/config.example.yaml /etc/serverlens/config.yaml
sudo nano /etc/serverlens/config.yaml   # fill in manually
```

**Separate PostgreSQL setup** (can be run again later):

```bash
sudo bash scripts/setup_db.sh
```

The script connects to PostgreSQL, shows databases/tables/columns, creates a read-only user, and updates the `databases` section in `config.yaml`.

**SSH user permissions** — the MCP client connects over SSH as a normal user (for example `deploy`). That user must:
1. Read the ServerLens config (`/etc/serverlens/config.yaml`)
2. Read the log files listed in the config

Add that SSH user to the `serverlens` group and to groups that own the logs.

> **The installer (`install.sh`) automatically:**
> - adds the `serverlens` system user to `adm` (if it exists)
> - fixes permissions on PHP-FPM logs (adds group read)
> - fixes permissions on RabbitMQ logs (adds group read)
>
> You must add the **SSH user to groups manually** (the installer prints the commands).

Each service keeps logs with its own owning group. Add the SSH user to the group that owns the logs:

```bash
sudo usermod -aG serverlens deploy     # access to ServerLens config
```

Beyond that — it depends on your services. The installer detects groups and prints exact commands; typical cases:

| Service | Group (Ubuntu) | Group (CentOS/RHEL) | Command |
|--------|------------------|-----------------------|---------|
| syslog, auth.log, nginx | `adm` | `nginx` / `root` | `sudo usermod -aG adm deploy` |
| PHP-FPM | `adm` (after fix) | `root` | `sudo usermod -aG adm deploy` |
| PostgreSQL | `postgres` | `postgres` | `sudo usermod -aG postgres deploy` |
| RabbitMQ | `rabbitmq` | `rabbitmq` | `sudo usermod -aG rabbitmq deploy` |

> **What is `adm`?** A standard Ubuntu/Debian system group for reading logs. Created with the OS. Most files under `/var/log/` are `root:adm`. Check with: `getent group adm`

**PHP-FPM logs** — a common issue:

PHP-FPM writes to `/var/log/php*-fpm.log`. On Ubuntu these files are often `root:root` with mode `600` — not readable except by root. The installer sets group `adm` and mode `640`, but after **log rotation** (logrotate) permissions may reset.

To keep permissions stable, check the logrotate config:

```bash
cat /etc/logrotate.d/php8.2-fpm
# There should be a line (add if missing):
create 0640 root adm
```

**RabbitMQ logs:**

Logs under `/var/log/rabbitmq/` are `rabbitmq:rabbitmq`. The installer adds group read, but the SSH user must be in the `rabbitmq` group.

**How to see which group you need for any log:**

```bash
ls -la /var/log/nginx/
# -rw-r----- 1 root adm 12345 Mar 25 10:00 access.log
#                    ^^^ — add this group

ls -la /var/log/rabbitmq/
# drwxr-x--- 2 rabbitmq rabbitmq 4096 Mar 25 10:00 .
#                       ^^^^^^^^ — need rabbitmq group
```

> **Important:** after `usermod`, **log out and back in** (end the SSH session and reconnect) so new groups apply. Or run `newgrp serverlens` in the current session.

> The installer suggests the needed commands in its final output.

---

## Step 3. Verify ServerLens on the server

Test **as the SSH user** (the one the MCP client will use):

```bash
# Validate config:
php /opt/serverlens/bin/serverlens validate-config \
  --config /etc/serverlens/config.yaml

# Quick stdio test (Ctrl+C to exit):
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  php /opt/serverlens/bin/serverlens serve --config /etc/serverlens/config.yaml --stdio
```

You should get JSON with `"serverInfo":{"name":"ServerLens","version":"1.0.0"}`.

> If you see `File ... cannot be read` — the SSH user is not in the `serverlens` group (see step 2 — “SSH user permissions”).

> **Note:** the systemd service (`systemctl start serverlens`) is only needed for SSE mode. For MCP over SSH you **do not** need to start the service — the MCP client launches ServerLens on connect.

---

## Step 4. Install the MCP client on your machine

Switch to **your computer** (developer machine):

```bash
git clone git@github.com:your-org/serverlens.git ~/serverlens
cd ~/serverlens/mcp-client
composer install
```

---

## Step 5. Point the client at your servers

The MCP client is a **local** program on your machine. It reaches **remote** servers over SSH. You need a config that lists servers and SSH credentials.

Copy the template:

```bash
mkdir -p ~/.serverlens
cp ~/serverlens/mcp-client/config.example.yaml ~/.serverlens/config.yaml
```

Open `~/.serverlens/config.yaml` and fill in the **remote server** where you installed ServerLens (steps 1–3):

```yaml
servers:
  # ↓ Arbitrary name in the config (monitor, production, web1…).
  #   In Cursor you only see serverlens_list and serverlens_call via MCP;
  #   in serverlens_call use this name in "server" and the operation in "tool" (see API Reference).
  monitor:
    ssh:
      host: "1.2.3.4"                # IP or hostname of the remote server
      user: "deploy"                  # SSH user (same as you use for SSH)
      port: 22
      key: "~/.ssh/id_ed25519"       # path to SSH key ON YOUR MACHINE
    remote:
      php: "php"                      # PHP path ON THE REMOTE SERVER
      serverlens_path: "/opt/serverlens/bin/serverlens"  # ServerLens path ON THE SERVER
      config_path: "/etc/serverlens/config.yaml"         # config path ON THE SERVER
```

> **Multiple servers?** Add another block with a different name:
> ```yaml
>   staging:
>     ssh:
>       host: "5.6.7.8"
>       user: "deploy"
>       key: "~/.ssh/id_ed25519"
>     remote:
>       php: "php"
>       serverlens_path: "/opt/serverlens/bin/serverlens"
>       config_path: "/etc/serverlens/config.yaml"
> ```

Verify SSH works:

```bash
ssh -i ~/.ssh/id_ed25519 deploy@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config --config /etc/serverlens/config.yaml"
```

---

## Step 6. Connect to Cursor

### Option A — global (all servers)

Add to `~/.cursor/mcp.json` (create the file if missing):

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/Users/YOUR_USERNAME/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/Users/YOUR_USERNAME/.serverlens/config.yaml"
      ]
    }
  }
}
```

### Option B — per-project (specific servers only)

Create `.cursor/mcp.json` **in the project root** and add `--servers` with comma-separated server names:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/Users/YOUR_USERNAME/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/Users/YOUR_USERNAME/.serverlens/config.yaml",
        "--servers",
        "production"
      ]
    }
  }
}
```

For multiple servers: `"--servers", "production,staging"`.

This way different projects can access different servers from the same `config.yaml`. Server names must match keys under `servers:` in your config.

> **Important:** paths must be **absolute**.

Restart Cursor. In MCP logs (Output → MCP) you should see:

```
[MCP] Config: /Users/.../.serverlens/config.yaml
[MCP] Server filter: production                     ← only with --servers
[MCP] Connecting to server 'production'...
[MCP:production] Initialized: ServerLens v1.0.0
[MCP] Discovered 17 tools on 'production'
[MCP] Ready: 1 server(s), 17 remote tool(s), 2 MCP tools
```

---

## Updates

### On the server

SSH in and go to the source directory:

```bash
ssh user@1.2.3.4
cd ~/serverlens-src
```

**Recommended** (git pull as normal user + update as root):

```bash
git pull                                # as normal user
sudo bash scripts/update.sh --no-pull   # as root
```

**If the server has direct Git access**, you can use one command:

```bash
sudo bash scripts/update.sh
```

The script will:
1. Run `git pull` (detects repo owner and runs as that user)
2. Copy updated files (`src/`, `bin/`, `composer.json`) to `/opt/serverlens/`
3. Refresh PHP dependencies (`composer install`)
4. Update the systemd unit if it changed
5. Validate the config

**`/etc/serverlens/config.yaml` is not touched** — your settings stay.

Flags:
- `--no-pull` — skip `git pull` (if you already updated manually)
- `--restart` — restart the systemd service after update

```bash
# If the service is running (SSE mode), restart automatically:
sudo bash scripts/update.sh --restart --no-pull
```

> For SSH+stdio you do not need to restart the service — the MCP client starts ServerLens on each connection, so new code is picked up automatically.

### On the developer machine (MCP client)

```bash
cd ~/serverlens
git pull
cd mcp-client && composer install
```

Restart Cursor so the MCP client picks up changes.

---

## Done

You can phrase requests in **natural language** — Cursor will call `serverlens_list` / `serverlens_call` with the right server and tool. Examples:
- *“Show the latest nginx errors”*
- *“How many users registered in March?”*
- *“What is the status of Docker containers?”*
- *“Show PostgreSQL configuration”*

---

## References

| Document | Contents |
|----------|-------------|
| [Architecture](architecture.md) | System layout, data flow, diagrams |
| [Server setup](server/setup.md) | Detailed configuration of all ServerLens modules |
| [API Reference](server/api.md) | Full reference for remote ServerLens tools (logs, configs, DB, system) |
| [MCP client](../mcp-client/docs/README.md) | MCP proxy documentation |
