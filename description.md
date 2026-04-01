# ServerLens — Безопасный Read-Only инструмент серверной диагностики

## 1. Обзор и цели

**ServerLens** — это защищённый серверный инструмент, предоставляющий read-only доступ к логам, конфигурациям и базам данных сервера. Инструмент работает как MCP-сервер (Model Context Protocol), что позволяет подключаться к нему из Cursor, Claude Desktop или любого MCP-совместимого клиента.

### Ключевые принципы

- **Только чтение** — инструмент физически не может модифицировать данные: ни файлы, ни базы
- **Абстрактные запросы** — никакого сырого SQL; клиент описывает *что* нужно (таблица, поля, условия), а сервер сам строит безопасный запрос
- **Whitelist-модель** — доступны только явно разрешённые логи, конфиги, базы и таблицы
- **Аутентификация уровня SSH** — подключение по ключу или токену с поддержкой ротации
- **Аудит** — каждый запрос логируется

---

## 2. Архитектура

```
┌──────────────────────────┐         SSH-туннель / mTLS
│  Компьютер разработчика  │◄────────────────────────────►┌─────────────────────────┐
│                          │                               │   Сервер                │
│  Cursor / Claude Desktop │       localhost:9600          │                         │
│  (MCP-клиент)            │◄──────────────────────────────│   ServerLens            │
│                          │       stdio / SSE              │   (MCP-сервер)          │
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

### Варианты транспорта (как подключаться)

| Вариант | Безопасность | Сложность | Рекомендация |
|---------|-------------|-----------|--------------|
| **SSH-туннель + stdio** | ★★★★★ | Низкая | **Рекомендуется** |
| **SSH-туннель + SSE на localhost** | ★★★★★ | Низкая | Альтернатива |
| **mTLS (клиентские сертификаты)** | ★★★★★ | Средняя | Для корпоративных сценариев |
| **HTTPS + Bearer token** | ★★★★☆ | Низкая | Только через VPN/Tailscale |
| **WireGuard/Tailscale + token** | ★★★★★ | Средняя | Для удалённого доступа |

#### Рекомендуемый вариант: SSH-туннель

Самый простой и безопасный подход — ServerLens слушает **только на localhost** (127.0.0.1), а ты пробрасываешь порт через SSH:

```bash
# На компьютере разработчика:
ssh -L 9600:127.0.0.1:9600 user@server

# Теперь MCP-клиент подключается к localhost:9600
```

Преимущества:
- Порт 9600 НЕ открыт наружу (bind на 127.0.0.1)
- Аутентификация через SSH-ключи (уже настроена)
- Шифрование SSH (не нужен свой TLS)
- Не нужна дополнительная инфраструктура (сертификаты, VPN)

Поверх SSH-туннеля ServerLens также проверяет **Bearer-токен** — это второй фактор на случай, если кто-то получит SSH-доступ к серверу.

---

## 3. Аутентификация и безопасность

### 3.1. Двухслойная аутентификация

**Слой 1: Транспорт (SSH-туннель)**
- Доступ по SSH-ключу (Ed25519)
- Отдельный системный пользователь `serverlens` с минимальными правами
- Опционально: ограничение команд в `authorized_keys`

**Слой 2: Приложение (Bearer-токен)**
- HMAC-SHA256 токен, 256 бит
- Передаётся в заголовке: `Authorization: Bearer <token>`
- Хранится хешированным (argon2id) в конфиге сервера

### 3.2. Ротация токенов

```yaml
# Конфигурация ротации
auth:
  tokens:
    - hash: "$argon2id$v=19$m=19456,t=2,p=1$..."   # текущий
      created: "2025-03-01"
      expires: "2025-06-01"                          # 90 дней
    - hash: "$argon2id$v=19$m=19456,t=2,p=1$..."   # предыдущий (grace period)
      created: "2024-12-01"
      expires: "2025-04-01"
  max_active_tokens: 2          # одновременно валидны максимум 2
  token_lifetime_days: 90       # рекомендуемый срок жизни
```

Механизм ротации:
1. Генерация нового токена: `serverlens token generate`
2. Новый токен добавляется, старый остаётся активным (grace period — 30 дней)
3. После grace period старый токен автоматически деактивируется
4. Принудительная отмена: `serverlens token revoke <prefix>`

### 3.3. Защита от атак

| Угроза | Защита |
|--------|--------|
| Brute-force токена | Rate limiting: 5 попыток/мин, блокировка IP на 15 мин |
| SQL-инъекция | Нет сырого SQL; параметризованные запросы через ORM |
| Path traversal (логи) | Whitelist абсолютных путей; realpath-проверка |
| Информация об ошибках | Унифицированные ошибки; детали только в серверном логе |
| Сканирование снаружи | Bind на 127.0.0.1; порт не доступен извне |
| Объём данных | Лимит строк на запрос (default 1000); пагинация |
| DoS | Rate limiting: 60 запросов/мин на клиента |

---

## 4. Модули

### 4.1. LogReader — Чтение логов

Доступ к файлам логов с whitelist-контролем.

**Конфигурация:**
```yaml
logs:
  sources:
    - name: "nginx_access"
      path: "/var/log/nginx/access.log"
      format: "nginx_combined"      # парсер формата
      max_lines: 5000               # максимум строк за запрос
      
    - name: "nginx_error"
      path: "/var/log/nginx/error.log"
      format: "plain"
      max_lines: 2000
      
    - name: "speak_y_api"
      path: "/var/log/speak-y/api.log"
      format: "json"                # структурированные логи
      max_lines: 3000
      
    - name: "servicebook_api"
      path: "/var/log/servicebook/api.log"
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

Кроме одиночного файла в `path`, для логов поддерживается **`type: "directory"`**: источник может указывать на каталог с glob-шаблоном; файлы подбираются автоматически, в списке и в параметре `source` они фигурируют как `имя_каталога/имя_файла`.

**MCP-инструменты (tools):**

| Tool | Описание | Параметры |
|------|----------|-----------|
| `logs_list` | Список доступных логов | — |
| `logs_tail` | Последние N строк | `source`, `lines` (max 500) |
| `logs_search` | Поиск по подстроке/regex | `source`, `query`, `regex: bool`, `lines` (max 1000) |
| `logs_count` | Количество строк / размер файла | `source` |
| `logs_time_range` | Записи за период | `source`, `from`, `to`, `lines` |

**Пример запроса (как это выглядит для MCP-клиента):**
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

**Безопасность LogReader:**
- Путь к файлу берётся ТОЛЬКО из конфигурации (не от клиента)
- `realpath()` проверка — даже если в конфиге симлинк, проверяем, что resolved path не выходит за пределы разрешённых директорий
- Файл открывается в read-only режиме
- Лимит строк жёстко ограничен конфигурацией
- Regex-запросы имеют таймаут (5 сек) и ограничение сложности

---

### 4.2. ConfigReader — Чтение конфигов

Доступ к конфигурационным файлам (или их безопасным фрагментам).

**Конфигурация:**
```yaml
configs:
  sources:
    - name: "nginx_main"
      path: "/etc/nginx/nginx.conf"
      
    - name: "nginx_sites"
      path: "/etc/nginx/sites-enabled/"
      type: "directory"                    # все файлы в директории
      
    - name: "postgres_main"
      path: "/etc/postgresql/16/main/postgresql.conf"
      redact:                              # скрыть чувствительные параметры
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

**MCP-инструменты:**

| Tool | Описание | Параметры |
|------|----------|-----------|
| `config_list` | Список доступных конфигов | — |
| `config_read` | Содержимое конфига | `source` |
| `config_search` | Поиск по конфигу | `source`, `query` |

**Безопасность ConfigReader:**
- Whitelist путей (как в LogReader)
- **Автоматическая редакция (redaction)** — пароли, токены, ключи заменяются на `[REDACTED]`
- Встроенные regex-паттерны для типичных секретов (пароли, API-ключи, connection strings)
- Файлы открываются в read-only

---

### 4.3. DBQuery — Безопасные запросы к базам данных

Абстрактный интерфейс для чтения данных из PostgreSQL без прямого SQL.

**Конфигурация:**
```yaml
databases:
  connections:
    - name: "speaky_prod"
      host: "localhost"
      port: 5432
      database: "speaky_production"
      user: "serverlens_readonly"         # специальный read-only пользователь
      password_env: "SL_DB_SPEAKY_PASS"   # пароль из переменной окружения
      
      # Whitelist таблиц и полей
      tables:
        - name: "users"
          allowed_fields: ["id", "email", "created_at", "is_active", "plan"]
          # Исключённые поля (даже если allowed_fields = "*"):
          denied_fields: ["password_hash", "api_key", "reset_token"]
          max_rows: 500
          allowed_filters: ["id", "email", "is_active", "created_at", "plan"]
          allowed_order_by: ["id", "created_at"]
          
        - name: "transcriptions"
          allowed_fields: ["id", "user_id", "language", "duration", "provider", "status", "created_at"]
          denied_fields: ["raw_text", "audio_path"]    # содержимое транскрипций — приватное
          max_rows: 1000
          allowed_filters: ["user_id", "language", "provider", "status", "created_at"]
          allowed_order_by: ["id", "created_at", "duration"]
          
        - name: "api_requests"
          allowed_fields: ["id", "endpoint", "method", "status_code", "response_time_ms", "created_at"]
          denied_fields: ["request_body", "response_body", "ip_address"]
          max_rows: 2000
          allowed_filters: ["endpoint", "method", "status_code", "created_at"]
          allowed_order_by: ["id", "created_at", "response_time_ms"]

    - name: "servicebook_prod"
      host: "localhost"
      port: 5432
      database: "servicebook_production"
      user: "serverlens_readonly"
      password_env: "SL_DB_SERVICEBOOK_PASS"
      
      tables:
        - name: "service_requests"
          allowed_fields: ["id", "type", "status", "priority", "created_at", "updated_at"]
          denied_fields: ["description", "requester_phone", "requester_address"]
          max_rows: 1000
          allowed_filters: ["type", "status", "priority", "created_at"]
          allowed_order_by: ["id", "created_at", "priority"]
          
        - name: "categories"
          allowed_fields: "*"              # все поля разрешены
          denied_fields: []
          max_rows: 500
```

**MCP-инструменты:**

| Tool | Описание | Параметры |
|------|----------|-----------|
| `db_list` | Список баз и таблиц | — |
| `db_describe` | Структура таблицы (разрешённые поля) | `database`, `table` |
| `db_query` | Выборка записей | `database`, `table`, `fields`, `filters`, `order_by`, `limit`, `offset` |
| `db_count` | Количество записей | `database`, `table`, `filters` |
| `db_stats` | Базовая статистика по полю | `database`, `table`, `field` (COUNT, MIN, MAX, AVG для числовых) |

**Формат запроса (абстрактный, не SQL):**
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

**Поддерживаемые операторы фильтрации:**
- `eq` — равно
- `neq` — не равно
- `gt`, `gte`, `lt`, `lte` — сравнение
- `in` — входит в список (максимум 50 значений)
- `like` — LIKE с автоматическим экранированием (только `%` в начале/конце)
- `is_null` — IS NULL / IS NOT NULL

**Безопасность DBQuery (КРИТИЧНО):**

1. **Отдельный пользователь PostgreSQL:**
```sql
-- Создаётся один раз при установке
CREATE USER serverlens_readonly WITH PASSWORD '...';
-- ТОЛЬКО SELECT, НИКАКИХ других привилегий
GRANT CONNECT ON DATABASE speaky_production TO serverlens_readonly;
GRANT USAGE ON SCHEMA public TO serverlens_readonly;
-- Выдаём SELECT только на конкретные таблицы
GRANT SELECT ON users, transcriptions, api_requests TO serverlens_readonly;
-- Явный запрет на всё остальное
ALTER USER serverlens_readonly SET default_transaction_read_only = on;
```

2. **Построение запросов:**
   - Клиент передаёт структурированный JSON, НЕ SQL-строку
   - Сервер строит SQL через query builder (SQLAlchemy Core)
   - Все значения передаются как **параметры** (prepared statements)
   - Имена таблиц и полей проверяются по whitelist (строковая подстановка только из разрешённого набора)
   - **Нет** UNION, JOIN, подзапросов, функций, raw expressions

3. **Валидация:**
   - Проверка: таблица в whitelist?
   - Проверка: все запрошенные поля в `allowed_fields` и не в `denied_fields`?
   - Проверка: все поля фильтров в `allowed_filters`?
   - Проверка: order_by в `allowed_order_by`?
   - Проверка: limit ≤ max_rows таблицы?
   - Проверка: значения фильтров — скалярные типы (str, int, float, bool, date)?
   - **Любая** проверка не прошла → отказ с кодом ошибки (без деталей о структуре)

---

### 4.4. SystemInfo — Системная информация (опционально)

Базовая информация о состоянии сервера.

**MCP-инструменты:**

| Tool | Описание |
|------|----------|
| `system_overview` | CPU, RAM, disk usage, uptime |
| `system_services` | Статус systemd-сервисов (из whitelist) |
| `system_docker` | Статус Docker-контейнеров (из whitelist) |
| `system_connections` | Количество активных соединений (PostgreSQL, RabbitMQ) |

**Конфигурация:**
```yaml
system:
  enabled: true
  allowed_services:
    - "nginx"
    - "postgresql"
    - "rabbitmq-server"
    - "speak-y-api"
    - "servicebook-api"
  allowed_docker_stacks:
    - "speak-y"
    - "servicebook"
```

---

## 5. Конфигурация

Единый файл конфигурации: `/etc/serverlens/config.yaml`

```yaml
# ═══════════════════════════════════════════
# ServerLens Configuration
# ═══════════════════════════════════════════

server:
  host: "127.0.0.1"            # ТОЛЬКО localhost!
  port: 9600
  transport: "sse"             # "sse" или "stdio"
  
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
  log_params: false             # НЕ логировать значения фильтров (приватность)
  retention_days: 90

# Секции logs, configs, databases, system — как описано выше
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

**Права на конфигурацию:**
```bash
chown root:serverlens /etc/serverlens/config.yaml
chmod 640 /etc/serverlens/config.yaml
```

---

## 6. Технологический стек

| Компонент | Технология | Почему |
|-----------|-----------|--------|
| Язык | **PHP 8.1+** | Современный PHP, типизация, экосистема Composer |
| MCP, HTTP/SSE | **ReactPHP** (`react/http`, `react/socket`) | Асинхронный цикл событий для транспорта (SSE, stdio) |
| Конфигурация | **Symfony YAML** | Парсинг YAML-конфигов |
| БД | **PDO (PostgreSQL)** | Параметризованные запросы, prepared statements |
| Хеширование токенов | **`password_hash` (Argon2id)** | Встроенное в PHP |
| Процесс | **systemd** | Надёжное управление процессом |

### Зависимости (минимальные)
```
php: >=8.1
react/http: ^1.9
react/socket: ^1.15
symfony/yaml: ^6.0|^7.0
ext-pdo_pgsql
```

---

## 7. Структура проекта

```
serverlens/
├── README.md
├── description.md
├── composer.json
│
├── src/                        # Серверная часть (ServerLens)
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
├── mcp-client/                 # MCP-прокси (dispatch model, 2 tools)
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

## 8. Протокол взаимодействия (MCP)

### Регистрация инструментов

ServerLens регистрирует следующие MCP tools при подключении:

```
logs_list          — Список доступных источников логов
logs_tail          — Последние N строк из лога
logs_search        — Поиск по логу (текст или regex)
logs_count         — Размер/количество строк лога
logs_time_range    — Записи за временной период

config_list        — Список доступных конфигов
config_read        — Чтение конфига (с редакцией секретов)
config_search      — Поиск по конфигу

db_list            — Список баз, таблиц и доступных полей
db_describe        — Описание таблицы
db_query           — Выборка записей (абстрактный запрос)
db_count           — Количество записей
db_stats           — Статистика по полю

system_overview    — CPU, RAM, Disk, Uptime
system_services    — Статус systemd-сервисов
system_docker      — Статус Docker-контейнеров
system_connections — Активные соединения БД
```

### Пример сессии (как это выглядит в Cursor/Claude)

**Разработчик спрашивает:** *«Покажи последние ошибки nginx за сегодня»*

Claude/Cursor вызывает:
```json
{"tool": "logs_search", "params": {"source": "nginx_error", "query": "error", "lines": 50}}
```

ServerLens:
1. Проверяет токен ✓
2. Проверяет rate limit ✓
3. Проверяет что "nginx_error" в whitelist ✓
4. Читает файл, фильтрует, ограничивает строки
5. Записывает в аудит-лог
6. Возвращает результат

**Разработчик:** *«Сколько транскрипций было за март со статусом completed?»*

Claude/Cursor:
```json
{"tool": "db_count", "params": {"database": "speaky_prod", "table": "transcriptions", "filters": {"status": {"eq": "completed"}, "created_at": {"gte": "2025-03-01", "lt": "2025-04-01"}}}}
```

---

## 9. Процесс установки

### Шаг 1: Создание системного пользователя
```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/serverlens serverlens
```

### Шаг 2: Создание read-only пользователя PostgreSQL
```sql
CREATE USER serverlens_readonly WITH PASSWORD 'сгенерировать_надёжный_пароль';
ALTER USER serverlens_readonly SET default_transaction_read_only = on;

-- Для каждой базы:
\c speaky_production
GRANT CONNECT ON DATABASE speaky_production TO serverlens_readonly;
GRANT USAGE ON SCHEMA public TO serverlens_readonly;
GRANT SELECT ON users, transcriptions, api_requests TO serverlens_readonly;
```

### Шаг 3: Настройка конфигурации
```bash
sudo mkdir -p /etc/serverlens
sudo cp config.example.yaml /etc/serverlens/config.yaml
sudo chown root:serverlens /etc/serverlens/config.yaml
sudo chmod 640 /etc/serverlens/config.yaml
# Редактируем конфигурацию...
```

### Шаг 4: Генерация токена
```bash
serverlens token generate
# Выведет: Token: sl_a1b2c3d4e5f6... (сохрани!)
# Хеш автоматически добавится в config.yaml
```

### Шаг 5: Systemd-юнит
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

# Дополнительная защита через systemd
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

### Шаг 6: Подключение с компьютера разработчика

Рекомендуемый способ — **локальный MCP-прокси** (`mcp-client/`): Cursor подключается по **stdio** к `serverlens-mcp`, а прокси сам устанавливает **SSH** к удалённому ServerLens (прямое подключение Cursor к SSE на сервере больше не является основным сценарием).

После настройки `~/.serverlens/config.yaml` (SSH, пути к PHP и `serverlens` на сервере) добавь в Cursor файл `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/абсолютный/путь/к/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/абсолютный/путь/к/.serverlens/config.yaml"
      ]
    }
  }
}
```

Пути к бинарнику прокси и к конфигу должны быть **абсолютными**. Отдельный SSH port-forward на порт ServerLens для MCP-клиента не требуется — туннель строит сам прокси.

---

## 10. Аудит-логирование

Каждый запрос записывается:

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

**Важно:** Значения фильтров НЕ логируются по умолчанию (приватность). Логируются только метаданные: какой инструмент, какая таблица, сколько строк.

---

## 11. Ограничения и границы

### Что ServerLens НЕ делает:
- ❌ Не модифицирует файлы, конфиги или базы
- ❌ Не выполняет произвольные shell-команды
- ❌ Не поддерживает JOIN, UNION, подзапросы
- ❌ Не показывает пароли, токены, ключи (автоматическая редакция)
- ❌ Не принимает сырой SQL
- ❌ Не открывает порты наружу
- ❌ Не даёт доступ к файлам вне whitelist

### Ограничения по дизайну:
- Максимум 1000 строк из БД за запрос (настраивается per-table)
- Максимум 5000 строк из лога за запрос
- Rate limit: 60 запросов/мин
- Regex в логах: таймаут 5 сек
- Токен экспирится через 90 дней
- Один конфигурационный файл — источник истины

---

## 12. Дорожная карта реализации

### Фаза 1 — MVP (1-2 дня)
- [x] Скелет MCP-сервера на FastMCP
- [x] Аутентификация по Bearer-токену
- [x] LogReader (logs_list, logs_tail, logs_search)
- [x] Конфигурация (Pydantic)
- [x] Systemd-юнит

### Фаза 2 — База данных (1-2 дня)
- [x] DBQuery со всеми инструментами
- [x] Валидатор запросов (whitelist полей, фильтры)
- [x] Read-only пользователь PostgreSQL
- [x] Пагинация

### Фаза 3 — Конфиги и система (0.5 дня)
- [x] ConfigReader с автоматической редакцией секретов
- [x] SystemInfo

### Фаза 4 — Hardening (0.5-1 день)
- [x] Rate limiting
- [x] Аудит-логирование
- [x] CLI для управления токенами
- [x] Тесты безопасности
- [x] Systemd hardening (sandbox)

**Общая оценка: 3-5 дней до production-ready.**

---

## 13. Альтернативы и почему MCP

| Подход | Плюсы | Минусы |
|--------|-------|--------|
| **MCP-сервер (выбран)** | Нативная интеграция с Cursor/Claude; структурированные инструменты; готовый протокол | Относительно новый стандарт |
| REST API + Swagger | Знакомый подход; любой HTTP-клиент | Нужен отдельный клиент; нет интеграции с AI |
| SSH + скрипты | Максимальная простота | Нет структуры; каждый раз вручную |
| Grafana + Loki | Мощная визуализация | Тяжёлая инфраструктура; overkill для задачи |

MCP — оптимальный выбор, потому что:
1. Ты уже работаешь с MCP-серверами в Cursor
2. AI-ассистент сам выбирает нужный инструмент по контексту запроса
3. Структурированные ответы (не нужно парсить текст)
4. Стандартный протокол с растущей экосистемой