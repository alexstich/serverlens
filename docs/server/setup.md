# ServerLens — Server installation and configuration

> **Before you read:** if you are installing the system for the first time, start with [docs/quickstart.md](../quickstart.md) — it has a step-by-step guide from scratch.
> This document is a detailed reference for all configuration options.

## Requirements

- **PHP 8.1+** with extensions: `pdo_pgsql`, `json`, `mbstring`
- **Composer** (for dependency installation)
- **SSH access** to the server
- **PostgreSQL** (for the DbQuery module, optional)

---

## Installation

### Option A — Interactive installer (recommended)

```bash
git clone git@github.com:yourorg/serverlens.git ~/serverlens-src
cd ~/serverlens-src
sudo bash scripts/install.sh
```

> Clone into the home directory — `/opt` is owned by root. The script copies files into `/opt/serverlens` itself.

The installer does everything in one pass:
- Checks PHP (version >= 8.1, extensions)
- Creates system user `serverlens`
- Copies files to `/opt/serverlens`, runs `composer install`
- Creates directories `/etc/serverlens`, `/var/log/serverlens`
- **Runs an interactive setup wizard:**
  - Scans installed services (nginx, PostgreSQL, Redis, PHP-FPM, Docker, RabbitMQ...)
  - Discovers log files and configs — you choose what you need
  - Configures PostgreSQL connection (database selection, tables, auto-detection of sensitive columns, read-only user creation)
  - Generates a ready `/etc/serverlens/config.yaml`
- Installs the systemd service

To skip the wizard (automation, CI):

```bash
sudo bash scripts/install.sh --no-wizard
```

### Option B — Manual

```bash
git clone git@github.com:yourorg/serverlens.git ~/serverlens-src
sudo mkdir -p /opt/serverlens /etc/serverlens /var/log/serverlens
sudo cp -r ~/serverlens-src/src/ ~/serverlens-src/bin/ ~/serverlens-src/composer.json /opt/serverlens/
cd /opt/serverlens && sudo composer install --no-dev --optimize-autoloader
sudo chmod +x /opt/serverlens/bin/serverlens
sudo cp ~/serverlens-src/config.example.yaml /etc/serverlens/config.yaml
```

### PostgreSQL setup only

To reconfigure the database (add a database, recreate the user):

```bash
sudo bash scripts/setup_db.sh
```

The script connects to PostgreSQL, shows available databases and tables, creates a read-only user, and updates the `databases` section in config.yaml.

### When is systemd needed?

| Usage | Need systemd? |
|---------------------|:--------------:|
| MCP proxy over SSH (recommended) | **No** — the MCP proxy starts ServerLens over SSH itself |
| SSE over SSH tunnel | **Yes** — ServerLens must run continuously |

---

## Configuration

Main file: `/etc/serverlens/config.yaml`

### server — Server settings

```yaml
server:
  host: "127.0.0.1"    # localhost ONLY (security)
  port: 9600            # port for SSE transport
  transport: "sse"      # "sse" or "stdio"
```

> **Important:** `host` may only be `127.0.0.1`, `localhost`, or `::1`. ServerLens does not accept external connections — only via SSH tunnel or stdio.

### auth — Authentication

```yaml
auth:
  tokens:
    - hash: "$argon2id$v=19$m=65536,t=4,p=1$..."   # token hash
      created: "2026-03-25"
      expires: "2026-06-25"                          # 90 days
  max_failed_attempts: 5    # lockout after 5 failed attempts
  lockout_minutes: 15       # lockout duration
```

Token generation:

```bash
php bin/serverlens token generate
```

Output:
```
=== New ServerLens Token ===
Token:   sl_a1b2c3d4e5f6...
Created: 2026-03-25
Expires: 2026-06-23

Add this to your config.yaml under auth.tokens:
  - hash: "$argon2id$..."
    created: "2026-03-25"
    expires: "2026-06-23"
```

> The token is only needed for SSE transport. With stdio (MCP client over SSH), authentication is handled by SSH keys.

### rate_limiting — Request limiting

```yaml
rate_limiting:
  requests_per_minute: 60   # max requests per minute
  max_concurrent: 5          # max concurrent requests
```

### audit — Audit logging

```yaml
audit:
  enabled: true
  path: "/var/log/serverlens/audit.log"
  log_params: false          # do NOT log parameter values
  retention_days: 90
```

Audit log format (JSON Lines):
```json
{"timestamp":"2026-03-25T14:30:22Z","client_ip":"127.0.0.1","tool":"logs_search","params_summary":{"source":"nginx_error","query_length":18},"result":{"status":"ok","duration_ms":23}}
```

### logs — Log sources

```yaml
logs:
  sources:
    - name: "nginx_access"           # name for API calls
      path: "/var/log/nginx/access.log"
      format: "nginx_combined"       # parser type (plain, json, nginx_combined, postgres, docker)
      max_lines: 5000                # max lines per request

    - name: "nginx_error"
      path: "/var/log/nginx/error.log"
      format: "plain"
      max_lines: 2000

    - name: "myapp_api"
      path: "/var/log/myapp/api.log"
      format: "json"
      max_lines: 3000

    # Directories with logs (files rotate / roll over)
    - name: "myapp_api_logs"
      path: "/var/www/myapp/runtime/logs/api"
      type: "directory"            # automatic file listing
      pattern: "*.log"             # glob pattern (default *.log)
      format: "plain"
      max_lines: 5000
```

- `type: "directory"` — ServerLens finds files by pattern automatically and lists them in `logs_list` with sizes and dates.
- Files are addressed as `"myapp_api_logs/20251031.log"` in the `source` parameter.
- Useful for daily-rotated logs (Yii, Laravel, etc.).
- PathGuard prevents escaping the directory.

**Security:**
- Paths come ONLY from configuration, not from the client
- `realpath()` check — protection against symlink attacks
- Files are opened read-only
- Line limit is strictly enforced

### configs — Configuration files

```yaml
configs:
  sources:
    - name: "nginx_main"
      path: "/etc/nginx/nginx.conf"
      redact: []                      # redact nothing

    - name: "nginx_sites"
      path: "/etc/nginx/sites-enabled/"
      type: "directory"               # all files in directory
      redact: []

    - name: "postgres_main"
      path: "/etc/postgresql/16/main/postgresql.conf"
      redact:                          # hide parameters containing these words
        - "password"
        - "ssl_key_file"

    - name: "docker_compose"
      path: "/opt/myapp/docker-compose.yml"
      redact:
        - pattern: "(?i)(password|secret|key|token)\\s*[:=]\\s*\\S+"
          replacement: "$1: [REDACTED]"
```

**Automatic redaction:** In addition to config rules, ServerLens automatically hides:
- `password`, `passwd`, `pass`
- `secret`, `api_key`, `apikey`
- `token`, `auth_token`, `access_token`
- `private_key`
- `connection_string`, `dsn`, `database_url`

### databases — PostgreSQL connections

```yaml
databases:
  connections:
    - name: "app_prod"
      host: "localhost"
      port: 5432
      database: "app_production"
      user: "serverlens_readonly"      # read-only user
      password_env: "SL_DB_APP_PASS"   # password from environment variable

      tables:
        - name: "users"
          allowed_fields: ["id", "email", "created_at", "is_active"]
          denied_fields: ["password_hash", "api_key", "reset_token"]
          max_rows: 500
          allowed_filters: ["id", "email", "is_active", "created_at"]
          allowed_order_by: ["id", "created_at"]

        - name: "api_requests"
          allowed_fields: ["id", "endpoint", "method", "status_code", "response_time_ms", "created_at"]
          denied_fields: ["request_body", "response_body", "ip_address"]
          max_rows: 2000
          allowed_filters: ["endpoint", "method", "status_code", "created_at"]
          allowed_order_by: ["id", "created_at", "response_time_ms"]
```

**Creating a read-only PostgreSQL user:**

Easiest — via the interactive script:

```bash
sudo bash scripts/setup_db.sh
```

The script connects to PostgreSQL, shows databases and tables, creates the user, and updates config.yaml.

Manually:

```sql
CREATE USER serverlens_readonly WITH PASSWORD 'strong_password';
ALTER USER serverlens_readonly SET default_transaction_read_only = on;
ALTER USER serverlens_readonly SET statement_timeout = '30s';

-- For each database:
\c app_production
GRANT CONNECT ON DATABASE app_production TO serverlens_readonly;
GRANT USAGE ON SCHEMA public TO serverlens_readonly;
GRANT SELECT ON users, api_requests TO serverlens_readonly;
```

> Pass the password via environment variable (`password_env`), not in the config file. Set the variable in `/etc/serverlens/env`.

### system — System information

```yaml
system:
  enabled: true
  allowed_services:             # systemd service whitelist
    - "nginx"
    - "postgresql"
    - "rabbitmq-server"
  allowed_docker_stacks:        # Docker stack whitelist
    - "myapp"
```

---

## Running

### Manual run (for testing)

```bash
# SSE transport
php bin/serverlens serve --config /etc/serverlens/config.yaml

# Stdio transport (used by MCP client over SSH)
php bin/serverlens serve --config /etc/serverlens/config.yaml --stdio
```

### Via systemd (production)

```bash
sudo systemctl start serverlens
sudo systemctl enable serverlens   # start on boot
sudo systemctl status serverlens   # check status
```

---

## CLI commands

```bash
# Start server
php bin/serverlens serve [--config <path>] [--stdio]

# Generate token
php bin/serverlens token generate

# Hash token (for manual config entry)
php bin/serverlens token hash <token>

# Validate configuration
php bin/serverlens validate-config [--config <path>]
```

---

## Security

### Defense model

| Layer | Mechanism |
|---------|----------|
| Network | Bind to 127.0.0.1 — port not exposed externally |
| Transport | SSH keys (stdio) or SSH tunnel (SSE) |
| Application | Bearer token (argon2id), rate limiting, IP lockout |
| Data | Whitelist paths/tables/fields, secret redaction |
| OS | systemd sandbox (NoNewPrivileges, ProtectSystem, MemoryDenyWriteExecute) |
| DB | Read-only PostgreSQL user, parameterized queries |

### Attack mitigations

| Threat | Mitigation |
|--------|------------|
| SQL injection | No raw SQL; only parameterized queries via field whitelist |
| Path traversal | Whitelist of absolute paths + `realpath()` check |
| Brute-force | Rate limiting + IP lockout after 5 attempts |
| Secret leakage | Automatic redaction of passwords, keys, tokens |
| External scanning | Bind to 127.0.0.1 — port not visible from outside |

---

## Permissions

### ServerLens files

```bash
# Configuration
chown root:serverlens /etc/serverlens/config.yaml
chmod 640 /etc/serverlens/config.yaml

# Environment variables (DB passwords)
chown root:serverlens /etc/serverlens/env
chmod 640 /etc/serverlens/env

# Audit log
chown serverlens:serverlens /var/log/serverlens/
chmod 750 /var/log/serverlens/
```

### SSH user

The MCP client connects over SSH as a normal user (for example `deploy`). That user **must** be in the `serverlens` group to read the config and env file:

```bash
sudo usermod -aG serverlens deploy
```

### Log access

The SSH user must also be in groups that own the log files.

**Ubuntu / Debian** (logs often in `adm` group):

```bash
sudo usermod -aG adm deploy            # /var/log/nginx/, /var/log/syslog
sudo usermod -aG postgres deploy       # /var/log/postgresql/
```

**CentOS / RHEL / Alma / Rocky** (logs owned by service groups):

```bash
sudo usermod -aG nginx deploy          # /var/log/nginx/
sudo usermod -aG postgres deploy       # /var/log/postgresql/
```

**Find the right group** for a specific log file:

```bash
stat -c '%G' /var/log/nginx/access.log
# adm      — on Ubuntu
# nginx    — on CentOS
```

> **Important:** after `usermod`, log out and log back in over SSH so new group memberships apply.
