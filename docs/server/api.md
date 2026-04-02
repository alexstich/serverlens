# ServerLens — API Reference

Complete reference for tools available through the MCP protocol.

## Dispatch model in MCP Proxy

When Cursor is connected via **ServerLens MCP Proxy** (`mcp-client/`), the client sees not individual remote tools but two universal tools:

- **`serverlens_list`** — no arguments: list of servers; with `{ "server": "<name>" }`: list of tools on that server (equivalent to remote `tools/list`).
- **`serverlens_call`** — single call entry point: `{ "server": "<name>", "tool": "<tool_name>", "arguments": { ... } }`. Arguments match the parameters described below for each tool (for example, for `logs_tail` — an object with `source`, `lines`, etc.).

Direct connection to ServerLens on the server (stdio/SSE without the proxy) still uses the tool names from this document without wrapping.

See also: [Architecture](../architecture.md) | [MCP proxy](../../mcp-client/docs/README.md)

---

## Logs (LogReader)

### logs_list

Returns the list of available log sources.

**Parameters:** none

For a source with **`type: "directory"`** in the configuration, this is a directory with multiple files matching a pattern: the response lists files (up to **50**, sorted by mtime, newest first). For such sources the config supports a **`pattern`** field — a glob relative to the directory (default `*.log`).

**Example response (plain file sources):**
```json
[
  {
    "name": "nginx_access",
    "format": "nginx_combined",
    "max_lines": 5000,
    "available": true
  },
  {
    "name": "nginx_error",
    "format": "plain",
    "max_lines": 2000,
    "available": true
  }
]
```

**Example list entry for a `directory` source (`logs_list`):**
```json
{
  "name": "app_logs",
  "type": "directory",
  "path_pattern": "*.log",
  "format": "plain",
  "max_lines": 5000,
  "available": true,
  "files_count": 5,
  "files": [
    {"name": "app_logs/20251031.log", "size": "6.57 KB", "modified": "2025-10-31 10:30:01"}
  ],
  "hint": "Use \"app_logs/<filename>\" as source name in logs_tail/logs_search"
}
```

To read a specific file from a directory, pass a string like **`source_name/filename`** in **`source`** for `logs_tail`, `logs_search`, `logs_count` (and others), as in the `name` field of `files` entries.

---

### logs_tail

Returns the last N lines of a log.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|----------|-----|:---:|:---:|----------|
| `source` | string | yes | — | Log source name; for `directory` sources — `dir/file`, e.g. `app_logs/20251031.log` |
| `lines` | integer | no | 100 | Number of lines (max 500) |

**Example:**
```json
{"source": "nginx_error", "lines": 20}
```

---

### logs_search

Search a log by substring or regular expression.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|----------|-----|:---:|:---:|----------|
| `source` | string | yes | — | Log source name; for `directory` — `dir/file` |
| `query` | string | yes | — | Search query |
| `regex` | boolean | no | false | Use regex |
| `lines` | integer | no | 100 | Max matches (max 1000) |

**Example (text search):**
```json
{"source": "nginx_error", "query": "upstream timed out", "lines": 50}
```

**Example (regex):**
```json
{"source": "myapp_api", "query": "status\":\\s*(4|5)\\d{2}", "regex": true, "lines": 100}
```

> Regex queries have a 5 second timeout.

---

### logs_count

Returns line count and file size.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `source` | string | yes | Log source name; for `directory` — `dir/file` |

**Example response:**
```json
{
  "source": "nginx_access",
  "lines": 142857,
  "size_bytes": 52428800,
  "size_human": "50 MB"
}
```

---

### logs_time_range

Returns log entries for a time range.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|----------|-----|:---:|:---:|----------|
| `source` | string | yes | — | Log source name |
| `from` | string | yes | — | Range start (ISO 8601 or common format) |
| `to` | string | yes | — | Range end |
| `lines` | integer | no | 200 | Max lines (max 1000) |

**Example:**
```json
{"source": "nginx_error", "from": "2026-03-25 10:00:00", "to": "2026-03-25 12:00:00"}
```

Supported date formats:
- ISO 8601: `2026-03-25T10:00:00`
- Common: `2026-03-25 10:00:00`
- Syslog: `Mar 25 10:00:00`
- nginx: `25/Mar/2026:10:00:00 +0300`

---

## Configurations (ConfigReader)

### config_list

Returns the list of available configuration files.

**Parameters:** none

**Example response:**
```json
[
  {"name": "nginx_main", "type": "file", "available": true},
  {"name": "nginx_sites", "type": "directory", "available": true},
  {"name": "postgres_main", "type": "file", "available": true}
]
```

---

### config_read

Reads configuration file content. Secrets are replaced with `[REDACTED]`.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `source` | string | yes | Config source name |

For `directory` sources, content of all files in the directory is returned.

---

### config_search

Search within configuration file content.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `source` | string | yes | Config source name |
| `query` | string | yes | Search query (case-insensitive) |

**Example response:**
```
23: listen 80;
45: listen 443 ssl;
```

---

## Database (DbQuery)

### db_list

Returns databases, tables, and allowed fields.

**Parameters:** none

**Example response:**
```json
[
  {
    "database": "app_prod",
    "tables": [
      {"name": "users", "allowed_fields": ["id", "email", "created_at", "is_active"], "max_rows": 500},
      {"name": "api_requests", "allowed_fields": ["id", "endpoint", "method", "status_code"], "max_rows": 2000}
    ]
  }
]
```

---

### db_describe

Table structure: allowed fields, filters, sorting.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `database` | string | yes | Database connection name |
| `table` | string | yes | Table name |

---

### db_query

Select rows from a table. Uses an abstract query format, NOT SQL.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|----------|-----|:---:|:---:|----------|
| `database` | string | yes | — | Database connection name |
| `table` | string | yes | — | Table name |
| `fields` | string[] | no | all allowed | Fields to return |
| `filters` | object | no | — | Filter conditions |
| `order_by` | string[] | no | — | Sort order (prefix `-` for DESC) |
| `limit` | integer | no | 100 | Max rows |
| `offset` | integer | no | 0 | Offset (pagination) |

**Filter operators:**

| Operator | Description | Example |
|----------|----------|--------|
| `eq` | Equals | `{"status": {"eq": "active"}}` |
| `neq` | Not equals | `{"status": {"neq": "deleted"}}` |
| `gt` | Greater than | `{"age": {"gt": 18}}` |
| `gte` | Greater or equal | `{"created_at": {"gte": "2026-03-01"}}` |
| `lt` | Less than | `{"price": {"lt": 100}}` |
| `lte` | Less or equal | `{"created_at": {"lte": "2026-03-31"}}` |
| `in` | In list (max 50) | `{"language": {"in": ["en", "es"]}}` |
| `like` | LIKE search | `{"email": {"like": "%@gmail.com"}}` |
| `is_null` | NULL / NOT NULL | `{"deleted_at": {"is_null": true}}` |

**Full example:**
```json
{
  "database": "app_prod",
  "table": "users",
  "fields": ["id", "email", "created_at", "is_active"],
  "filters": {
    "is_active": {"eq": true},
    "created_at": {"gte": "2026-03-01", "lt": "2026-04-01"}
  },
  "order_by": ["-created_at"],
  "limit": 50,
  "offset": 0
}
```

**Example response:**
```json
{
  "database": "app_prod",
  "table": "users",
  "rows_returned": 47,
  "offset": 0,
  "limit": 50,
  "data": [
    {"id": 1234, "email": "user@example.com", "created_at": "2026-03-15T10:30:00", "is_active": true}
  ]
}
```

---

### db_count

Row count matching filters.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `database` | string | yes | Database connection name |
| `table` | string | yes | Table name |
| `filters` | object | no | Filter conditions |

**Example:**
```json
{"database": "app_prod", "table": "users", "filters": {"is_active": {"eq": true}}}
```

---

### db_stats

Basic statistics on a numeric field: COUNT, MIN, MAX, AVG.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `database` | string | yes | Database connection name |
| `table` | string | yes | Table name |
| `field` | string | yes | Field name |

**Example response:**
```json
{
  "database": "app_prod",
  "table": "api_requests",
  "field": "response_time_ms",
  "count": 15423,
  "min": "2",
  "max": "4521",
  "avg": 145.3
}
```

---

## System (SystemInfo)

### system_overview

Overall server state: CPU, RAM, disk, uptime.

**Parameters:** none

---

### system_services

Status of systemd services from the whitelist.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `service` | string | no | Specific service (if omitted — all from whitelist) |

---

### system_docker

Status of Docker containers from allowed stacks.

**Parameters:**

| Parameter | Type | Required | Description |
|----------|-----|:---:|----------|
| `stack` | string | no | Specific stack (if omitted — all from whitelist) |

---

### system_connections

Count of active connections (PostgreSQL, RabbitMQ, TCP).

For RabbitMQ both directions are counted:
- `rabbitmq_incoming` — incoming (local RabbitMQ serving clients, `sport = :5672`)
- `rabbitmq_outgoing` — outgoing (local workers connected to remote RabbitMQ, `dport = :5672`)
- `rabbitmq_connections` — sum of incoming and outgoing

**Parameters:** none

**Example response:**
```json
{
  "postgresql_active": 3,
  "postgresql_total": 15,
  "rabbitmq_incoming": 0,
  "rabbitmq_outgoing": 12,
  "rabbitmq_connections": 12,
  "tcp_established": 47
}
```

---

### system_processes

Process list sorted by CPU or memory (like htop/top).

**Parameters:**

| Parameter | Type | Required | Default | Description |
|----------|-----|:---:|:---:|----------|
| `sort_by` | string | no | `cpu` | Sort: `cpu` or `memory` |
| `limit` | integer | no | 20 | Number of processes (max 100) |
| `user` | string | no | — | Filter by OS username |
| `filter` | string | no | — | Filter by substring in command name (case-insensitive) |

**Examples:**
```json
{"sort_by": "memory", "limit": 10}
```

```json
{"filter": "php", "limit": 30}
```

```json
{"user": "www-data", "sort_by": "cpu"}
```

**Example response:**
```json
{
  "sort_by": "cpu",
  "total_shown": 3,
  "filters": {},
  "processes": [
    {
      "user": "www-data",
      "pid": 12345,
      "cpu": 45.2,
      "mem": 8.1,
      "vsz_kb": 524288,
      "rss_kb": 131072,
      "stat": "Sl",
      "time": "12:34:56",
      "command": "php /var/www/myapp/artisan queue:work"
    }
  ]
}
```
