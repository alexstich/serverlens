# ServerLens — Secure read-only server diagnostics tool

## 1. Overview and goals

**ServerLens** is a secure server-side tool that provides read-only access to logs, configuration, and databases. It runs as an MCP (Model Context Protocol) server so you can connect from Cursor, Claude Desktop, or any MCP-compatible client.

### Key principles

- **Read-only** — the tool cannot modify data: neither files nor databases
- **Abstract queries** — no raw SQL; the client describes *what* is needed (table, fields, conditions), and the server builds a safe query
- **Whitelist model** — only explicitly allowed logs, configs, databases, and tables are reachable
- **SSH-level authentication** — key or token auth with rotation support
- **Audit** — every request is logged

---

## 2. Architecture

```
┌──────────────────────────┐         SSH tunnel / mTLS
│  Developer machine       │◄────────────────────────────►┌─────────────────────────┐
│                          │                               │   Server                │
│  Cursor / Claude Desktop │       localhost:9600          │                         │
│  (MCP client)            │◄──────────────────────────────│   ServerLens            │
│                          │       stdio / SSE              │   (MCP server)          │
└──────────────────────────┘                               │                         │
                                                           │  ┌───────────────────┐  │
                                                           │  │ Auth Layer        │  │
                                                           │  │ (Token / mTLS)    │  │
                                                           │  └────────┬──────────┘  │
                                                           │           │              │
                                                           │  ┌────────▼──────────┐  │
                                                           │  │ Request Validator  │  │
                                                           │  │ & Rate Limiter     │  │
                                                           │  └────────┬──────────┘  │
                                                           │           │              │
                                                           │  ┌───────┬┴──────┬────┐ │
                                                           │  │ Logs  │Config │ DB │ │
                                                           │  │Reader │Reader │Qry │ │
                                                           │  └───────┴───────┴────┘ │
                                                           │           │              │
                                                           │  ┌────────▼──────────┐  │
                                                           │  │ Audit Logger       │  │
                                                           │  └───────────────────┘  │
                                                           └─────────────────────────┘
```

### Transport options (how to connect)

| Option | Security | Complexity | Recommendation |
|---------|-------------|-----------|--------------|
| **SSH tunnel + stdio** | ★★★★★ | Low | **Recommended** |
| **SSH tunnel + SSE on localhost** | ★★★★★ | Low | Alternative |
| **mTLS (client certificates)** | ★★★★★ | Medium | Enterprise scenarios |
| **HTTPS + Bearer token** | ★★★★☆ | Low | Only via VPN/Tailscale |
| **WireGuard/Tailscale + token** | ★★★★★ | Medium | Remote access |

#### Recommended: SSH tunnel

The simplest and safest approach is for ServerLens to listen **only on localhost** (127.0.0.1) while you forward the port over SSH:

```bash
# On the developer machine:
ssh -L 9600:127.0.0.1:9600 user@server

# The MCP client now connects to localhost:9600
```

Benefits:
- Port 9600 is NOT exposed externally (bind on 127.0.0.1)
- Authentication via SSH keys (already configured)
- SSH encryption (no separate TLS required)
- No extra infrastructure (certificates, VPN)

On top of the SSH tunnel, ServerLens also validates a **Bearer token** — a second factor if someone gains SSH access to the server.

---

## 3. Authentication and security

### 3.1. Two-layer authentication

**Layer 1: Transport (SSH tunnel)**
- Access via SSH key (Ed25519)
- Dedicated system user `serverlens` with minimal privileges
- Optional: command restrictions in `authorized_keys`

**Layer 2: Application (Bearer token)**
- HMAC-SHA256 token, 256 bits
- Sent in header: `Authorization: Bearer <token>`
- Stored hashed (argon2id) in the server config

### 3.2. Token rotation

```yaml
# Rotation configuration
auth:
  tokens:
    - hash: "$argon2id$v=19$m=19456,t=2,p=1$..."   # current
      created: "2025-03-01"
      expires: "2025-06-01"                          # 90 days
    - hash: "$argon2id$v=19$m=19456,t=2,p=1$..."   # previous (grace period)
      created: "2024-12-01"
      expires: "2025-04-01"
  max_active_tokens: 2          # at most 2 valid at once
  token_lifetime_days: 90       # recommended lifetime
```

Rotation flow:
1. Generate a new token: `serverlens token generate`
2. Add the new token; the old one stays active (grace period — 30 days)
3. After the grace period the old token is deactivated automatically
4. Force revoke: `serverlens token revoke <prefix>`

### 3.3. Attack mitigations

| Threat | Mitigation |
|--------|--------|
| Token brute-force | Rate limiting: 5 attempts/min, IP block for 15 min |
| SQL injection | No raw SQL; parameterized queries via ORM |
| Path traversal (logs) | Whitelist of absolute paths; realpath checks |
| Error information leakage | Uniform errors; details only in server log |
| External scanning | Bind on 127.0.0.1; port not reachable from outside |
| Data volume | Row limit per request (default 1000); pagination |
| DoS | Rate limiting: 60 requests/min per client |

---

## 4. Modules

### 4.1. LogReader — Reading logs

Access to log files with whitelist control.

**Configuration:**
```yaml
logs:
  sources:
    - name: "nginx_access"
      path: "/var/log/nginx/access.log"
      format: "nginx_combined"      # format parser
      max_lines: 5000               # max lines per request
      
    - name: "nginx_error"
      path: "/var/log/nginx/error.log"
      format: "plain"
      max_lines: 2000
      
    - name: "speak_y_api"
      path: "/var/log/speak-y/api.log"
      format: "json"                # structured logs
      max_lines: 3000
      
    - name: "webapp_api"
      path: "/var/log/webapp/api.log"
      format: "json"
      max_lines: 3000
      
    - name: "postgresql"
      path: "/var/log/postgresql/postgresql-16-main.log"
      format: "postgres"
      max_lines: 2000

    - name: "docker_compose"
      path: "/opt/speak-y/docker-compose.log"
      format: "docker"
      max_lines: 3000
```

Besides a single file in `path`, logs support **`type: "directory"`**: a source can point at a directory with a glob pattern; files are picked automatically and appear in listings and the `source` parameter as `directory_name/file_name`.

**MCP tools:**

| Tool | Description | Parameters |
|------|----------|-----------|
| `logs_list` | List available logs | — |
| `logs_tail` | Last N lines | `source`, `lines` (max 500) |
| `logs_search` | Search by substring/regex | `source`, `query`, `regex: bool`, `lines` (max 1000) |
| `logs_count` | Line count / file size | `source` |
| `logs_time_range` | Records in a time range | `source`, `from`, `to`, `lines` |

**Example request (as seen by the MCP client):**
```json
{
  "tool": "logs_search",
  "params": {
    "source": "nginx_error",
    "query": "upstream timed out",
    "lines": 50
  }
}
```

**LogReader security:**
- File path comes ONLY from configuration (not from the client)
- `realpath()` check — even if the config has a symlink, the resolved path must stay within allowed directories
- File opened read-only
- Line limit enforced by configuration
- Regex queries have a timeout (5 sec) and complexity limits

---

### 4.2. ConfigReader — Reading configs

Access to configuration files (or safe fragments).

**Configuration:**
```yaml
configs:
  sources:
    - name: "nginx_main"
      path: "/etc/nginx/nginx.conf"
      
    - name: "nginx_sites"
      path: "/etc/nginx/sites-enabled/"
      type: "directory"                    # all files in the directory
      
    - name: "postgres_main"
      path: "/etc/postgresql/16/main/postgresql.conf"
      redact:                              # hide sensitive parameters
        - "password"
        - "ssl_key_file"
        - "ssl_cert_file"
      
    - name: "postgres_hba"
      path: "/etc/postgresql/16/main/pg_hba.conf"
      
    - name: "docker_compose_speaky"
      path: "/opt/speak-y/docker-compose.yml"
      redact:
        - pattern: "(?i)(password|secret|key|token)\\s*[:=]\\s*\\S+"
          replacement: "$1: [REDACTED]"

    - name: "rabbitmq"
      path: "/etc/rabbitmq/rabbitmq.conf"
      redact:
        - "default_pass"
```

**MCP tools:**

| Tool | Description | Parameters |
|------|----------|-----------|
| `config_list` | List available configs | — |
| `config_read` | Config contents | `source` |
| `config_search` | Search within config | `source`, `query` |

**ConfigReader security:**
- Whitelist paths (as in LogReader)
- **Automatic redaction** — passwords, tokens, keys replaced with `[REDACTED]`
- Built-in regex patterns for typical secrets (passwords, API keys, connection strings)
- Files opened read-only

---

### 4.3. DBQuery — Safe database queries

Abstract interface for reading PostgreSQL data without raw SQL.

**Configuration:**
```yaml
databases:
  connections:
    - name: "speaky_prod"
      host: "localhost"
      port: 5432
      database: "speaky_production"
      user: "serverlens_readonly"         # dedicated read-only user
      password_env: "SL_DB_SPEAKY_PASS"   # password from environment variable
      
      # Whitelist tables and fields
      tables:
        - name: "users"
          allowed_fields: ["id", "email", "created_at", "is_active", "plan"]
          # Excluded fields (even if allowed_fields = "*"):
          denied_fields: ["password_hash", "api_key", "reset_token"]
          max_rows: 500
          allowed_filters: ["id", "email", "is_active", "created_at", "plan"]
          allowed_order_by: ["id", "created_at"]
          
        - name: "transcriptions"
          allowed_fields: ["id", "user_id", "language", "duration", "provider", "status", "created_at"]
          denied_fields: ["raw_text", "audio_path"]    # transcription content is private
          max_rows: 1000
          allowed_filters: ["user_id", "language", "provider", "status", "created_at"]
          allowed_order_by: ["id", "created_at", "duration"]
          
        - name: "api_requests"
          allowed_fields: ["id", "endpoint", "method", "status_code", "response_time_ms", "created_at"]
          denied_fields: ["request_body", "response_body", "ip_address"]
          max_rows: 2000
          allowed_filters: ["endpoint", "method", "status_code", "created_at"]
          allowed_order_by: ["id", "created_at", "response_time_ms"]

    - name: "myapp_prod"
      host: "localhost"
      port: 5432
      database: "myapp_production"
      user: "serverlens_readonly"
      password_env: "SL_DB_MYAPP_PASS"
      
      tables:
        - name: "service_requests"
          allowed_fields: ["id", "type", "status", "priority", "created_at", "updated_at"]
          denied_fields: ["description", "requester_phone", "requester_address"]
          max_rows: 1000
          allowed_filters: ["type", "status", "priority", "created_at"]
          allowed_order_by: ["id", "created_at", "priority"]
          
        - name: "categories"
          allowed_fields: "*"              # all fields allowed
          denied_fields: []
          max_rows: 500
```

**MCP tools:**

| Tool | Description | Parameters |
|------|----------|-----------|
| `db_list` | List databases and tables | — |
| `db_describe` | Table structure (allowed fields) | `database`, `table` |
| `db_query` | Select rows | `database`, `table`, `fields`, `filters`, `order_by`, `limit`, `offset` |
| `db_count` | Row count | `database`, `table`, `filters` |
| `db_stats` | Basic field statistics | `database`, `table`, `field` (COUNT, MIN, MAX, AVG for numeric types) |

**Request format (abstract, not SQL):**
```json
{
  "tool": "db_query",
  "params": {
    "database": "speaky_prod",
    "table": "transcriptions",
    "fields": ["id", "language", "provider", "status", "created_at"],
    "filters": {
      "status": {"eq": "completed"},
      "created_at": {"gte": "2025-03-01", "lt": "2025-03-25"},
      "language": {"in": ["ru", "en", "ka"]}
    },
    "order_by": ["-created_at"],
    "limit": 50,
    "offset": 0
  }
}
```

**Supported filter operators:**
- `eq` — equals
- `neq` — not equals
- `gt`, `gte`, `lt`, `lte` — comparisons
- `in` — in list (max 50 values)
- `like` — LIKE with automatic escaping (only `%` at start/end)
- `is_null` — IS NULL / IS NOT NULL

**DBQuery security (CRITICAL):**

1. **Dedicated PostgreSQL user:**
```sql
-- Created once during installation
CREATE USER serverlens_readonly WITH PASSWORD '...';
-- SELECT ONLY, no other privileges
GRANT CONNECT ON DATABASE speaky_production TO serverlens_readonly;
GRANT USAGE ON SCHEMA public TO serverlens_readonly;
-- Grant SELECT only on specific tables
GRANT SELECT ON users, transcriptions, api_requests TO serverlens_readonly;
-- Explicit read-only default
ALTER USER serverlens_readonly SET default_transaction_read_only = on;
```

2. **Query construction:**
   - Client sends structured JSON, NOT a SQL string
   - Server builds SQL via a query builder (SQLAlchemy Core)
   - All values are **parameters** (prepared statements)
   - Table and column names validated against whitelist (string interpolation only from the allowed set)
   - **No** UNION, JOIN, subqueries, functions, or raw expressions

3. **Validation:**
   - Table in whitelist?
   - All requested fields in `allowed_fields` and not in `denied_fields`?
   - All filter fields in `allowed_filters`?
   - `order_by` in `allowed_order_by`?
   - `limit` ≤ table `max_rows`?
   - Filter values are scalar types (str, int, float, bool, date)?
   - **Any** check fails → error response (no structural details leaked)

---

### 4.4. SystemInfo — System information (optional)

Basic server state information.

**MCP tools:**

| Tool | Description |
|------|----------|
| `system_overview` | CPU, RAM, disk usage, uptime |
| `system_services` | systemd service status (from whitelist) |
| `system_docker` | Docker container status (from whitelist) |
| `system_connections` | Active connection counts (PostgreSQL, RabbitMQ) |

**Configuration:**
```yaml
system:
  enabled: true
  allowed_services:
    - "nginx"
    - "postgresql"
    - "rabbitmq-server"
    - "speak-y-api"
    - "myapp-api"
  allowed_docker_stacks:
    - "speak-y"
    - "webapp"
```

---

## 5. Configuration

Single configuration file: `/etc/serverlens/config.yaml`

```yaml
# ═══════════════════════════════════════════
# ServerLens Configuration
# ═══════════════════════════════════════════

server:
  host: "127.0.0.1"            # localhost ONLY!
  port: 9600
  transport: "sse"             # "sse" or "stdio"
  
auth:
  tokens:
    - hash: "$argon2id$..."
      created: "2025-03-25"
      expires: "2025-06-25"
  max_failed_attempts: 5
  lockout_minutes: 15

rate_limiting:
  requests_per_minute: 60
  max_concurrent: 5

audit:
  enabled: true
  path: "/var/log/serverlens/audit.log"
  log_params: false             # do NOT log filter values (privacy)
  retention_days: 90

# Sections logs, configs, databases, system — as described above
logs:
  sources: [...]

configs:
  sources: [...]

databases:
  connections: [...]

system:
  enabled: true
  allowed_services: [...]
```

**Config file permissions:**
```bash
chown root:serverlens /etc/serverlens/config.yaml
chmod 640 /etc/serverlens/config.yaml
```

---

## 6. Technology stack

| Component | Technology | Why |
|-----------|-----------|--------|
| Language | **PHP 8.1+** | Modern PHP, typing, Composer ecosystem |
| MCP, HTTP/SSE | **ReactPHP** (`react/http`, `react/socket`) | Event loop for transport (SSE, stdio) |
| Configuration | **Symfony YAML** | YAML config parsing |
| DB | **PDO (PostgreSQL)** | Parameterized queries, prepared statements |
| Token hashing | **`password_hash` (Argon2id)** | Built into PHP |
| Process | **systemd** | Reliable process management |

### Dependencies (minimal)
```
php: >=8.1
react/http: ^1.9
react/socket: ^1.15
symfony/yaml: ^6.0|^7.0
ext-pdo_pgsql
```

---

## 7. Project layout

```
serverlens/
├── README.md
├── description.md
├── composer.json
│
├── src/                        # Server (ServerLens)
│   ├── Application.php
│   ├── Config.php
│   ├── Mcp/
│   │   ├── Server.php
│   │   └── Tool.php
│   ├── Transport/
│   │   ├── TransportInterface.php
│   │   ├── SseTransport.php
│   │   └── StdioTransport.php
│   ├── Auth/
│   │   ├── TokenAuth.php
│   │   └── RateLimiter.php
│   ├── Module/
│   │   ├── ModuleInterface.php
│   │   ├── LogReader.php
│   │   ├── ConfigReader.php
│   │   ├── DbQuery.php
│   │   └── SystemInfo.php
│   ├── Security/
│   │   ├── PathGuard.php
│   │   └── Redactor.php
│   └── Audit/
│       └── AuditLogger.php
│
├── bin/serverlens
├── config.example.yaml
│
├── mcp-client/                 # MCP proxy (dispatch model, 2 tools)
│   ├── src/
│   │   ├── Config.php
│   │   ├── SshConnection.php
│   │   └── McpProxy.php
│   ├── bin/serverlens-mcp
│   ├── composer.json
│   └── config.example.yaml
│
├── scripts/
│   ├── install.sh
│   └── setup_db_users.sql
│
├── docs/
├── etc/
└── tests/
```

---

## 8. Interaction protocol (MCP)

### Tool registration

ServerLens registers the following MCP tools on connect:

```
logs_list          — List available log sources
logs_tail          — Last N lines from a log
logs_search        — Search a log (text or regex)
logs_count         — Log size / line count
logs_time_range    — Records in a time range

config_list        — List available configs
config_read        — Read config (with secret redaction)
config_search      — Search within config

db_list            — List databases, tables, and allowed fields
db_describe        — Table description
db_query           — Select rows (abstract query)
db_count           — Row count
db_stats           — Field statistics

system_overview    — CPU, RAM, Disk, Uptime
system_services    — systemd service status
system_docker      — Docker container status
system_connections — Active DB connections
```

### Example session (as in Cursor/Claude)

**Developer asks:** *“Show the latest nginx errors for today”*

Claude/Cursor calls:
```json
{"tool": "logs_search", "params": {"source": "nginx_error", "query": "error", "lines": 50}}
```

ServerLens:
1. Validates token ✓
2. Checks rate limit ✓
3. Ensures "nginx_error" is whitelisted ✓
4. Reads file, filters, caps lines
5. Writes audit log
6. Returns result

**Developer:** *“How many transcriptions in March with status completed?”*

Claude/Cursor:
```json
{"tool": "db_count", "params": {"database": "speaky_prod", "table": "transcriptions", "filters": {"status": {"eq": "completed"}, "created_at": {"gte": "2025-03-01", "lt": "2025-04-01"}}}}
```

---

## 9. Installation steps

### Step 1: Create system user
```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/serverlens serverlens
```

### Step 2: Create read-only PostgreSQL user
```sql
CREATE USER serverlens_readonly WITH PASSWORD 'generate_a_strong_password';
ALTER USER serverlens_readonly SET default_transaction_read_only = on;

-- For each database:
\c speaky_production
GRANT CONNECT ON DATABASE speaky_production TO serverlens_readonly;
GRANT USAGE ON SCHEMA public TO serverlens_readonly;
GRANT SELECT ON users, transcriptions, api_requests TO serverlens_readonly;
```

### Step 3: Configure
```bash
sudo mkdir -p /etc/serverlens
sudo cp config.example.yaml /etc/serverlens/config.yaml
sudo chown root:serverlens /etc/serverlens/config.yaml
sudo chmod 640 /etc/serverlens/config.yaml
# Edit configuration...
```

### Step 4: Generate token
```bash
serverlens token generate
# Prints: Token: sl_a1b2c3d4e5f6... (save it!)
# Hash is appended to config.yaml automatically
```

### Step 5: Systemd unit
```ini
[Unit]
Description=ServerLens MCP Server
After=network.target postgresql.service

[Service]
Type=simple
User=serverlens
Group=serverlens
ExecStart=/opt/serverlens/venv/bin/python -m serverlens --config /etc/serverlens/config.yaml
Restart=on-failure
RestartSec=5

# Extra hardening via systemd
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadOnlyPaths=/
ReadWritePaths=/var/log/serverlens
PrivateTmp=yes
CapabilityBoundingSet=
SystemCallFilter=@system-service

EnvironmentFile=/etc/serverlens/env

[Install]
WantedBy=multi-user.target
```

### Step 6: Connect from the developer machine

Recommended path — **local MCP proxy** (`mcp-client/`): Cursor connects over **stdio** to `serverlens-mcp`, and the proxy opens **SSH** to remote ServerLens (direct Cursor-to-SSE on the server is no longer the primary flow).

After configuring `~/.serverlens/config.yaml` (SSH, paths to PHP and `serverlens` on the server), add to Cursor `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/absolute/path/to/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/absolute/path/to/.serverlens/config.yaml"
      ]
    }
  }
}
```

Paths to the proxy binary and config must be **absolute**. A separate SSH port-forward to ServerLens for the MCP client is not required — the proxy builds the tunnel itself.

---

## 10. Audit logging

Every request is recorded:

```json
{
  "timestamp": "2025-03-25T14:30:22Z",
  "client_ip": "127.0.0.1",
  "tool": "db_query",
  "params_summary": {
    "database": "speaky_prod",
    "table": "transcriptions",
    "fields_count": 5,
    "has_filters": true,
    "limit": 50
  },
  "result": {
    "status": "ok",
    "rows_returned": 47,
    "duration_ms": 23
  }
}
```

**Important:** Filter values are NOT logged by default (privacy). Only metadata is logged: which tool, which table, how many rows.

---

## 11. Limitations and boundaries

### What ServerLens does NOT do:
- ❌ Does not modify files, configs, or databases
- ❌ Does not run arbitrary shell commands
- ❌ Does not support JOIN, UNION, or subqueries
- ❌ Does not expose passwords, tokens, or keys (automatic redaction)
- ❌ Does not accept raw SQL
- ❌ Does not expose ports externally
- ❌ Does not grant access to files outside the whitelist

### Design limits:
- Up to 1000 DB rows per request (configurable per table)
- Up to 5000 log lines per request
- Rate limit: 60 requests/min
- Log regex: 5 sec timeout
- Token expires after 90 days
- Single configuration file — source of truth

---

## 12. Implementation roadmap

### Phase 1 — MVP (1–2 days)
- [x] MCP server skeleton on FastMCP
- [x] Bearer token authentication
- [x] LogReader (logs_list, logs_tail, logs_search)
- [x] Configuration (Pydantic)
- [x] Systemd unit

### Phase 2 — Database (1–2 days)
- [x] DBQuery with all tools
- [x] Request validator (whitelist fields, filters)
- [x] Read-only PostgreSQL user
- [x] Pagination

### Phase 3 — Configs and system (0.5 day)
- [x] ConfigReader with automatic secret redaction
- [x] SystemInfo

### Phase 4 — Hardening (0.5–1 day)
- [x] Rate limiting
- [x] Audit logging
- [x] CLI for token management
- [x] Security tests
- [x] Systemd hardening (sandbox)

**Overall estimate: 3–5 days to production-ready.**

---

## 13. Alternatives and why MCP

| Approach | Pros | Cons |
|--------|-------|--------|
| **MCP server (chosen)** | Native Cursor/Claude integration; structured tools; standard protocol | Relatively new standard |
| REST API + Swagger | Familiar; any HTTP client | Separate client; no AI integration |
| SSH + scripts | Maximum simplicity | Unstructured; manual each time |
| Grafana + Loki | Powerful visualization | Heavy infrastructure; overkill |

MCP is a good fit because:
1. You already use MCP servers in Cursor
2. The AI assistant picks the right tool from context
3. Structured responses (no text parsing)
4. Standard protocol with a growing ecosystem
