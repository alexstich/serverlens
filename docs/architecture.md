# ServerLens — Архитектура системы

> Для пошаговой установки: [quickstart.md](quickstart.md) | Для настройки сервера: [server/setup.md](server/setup.md) | API: [server/api.md](server/api.md)

## Общая схема

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        Компьютер разработчика                                │
│                                                                              │
│   ┌─────────────┐    stdio (JSON-RPC)    ┌──────────────────────────┐       │
│   │   Cursor /   │◄────────────────────►│   ServerLens MCP Proxy    │       │
│   │ Claude Desktop│                      │   (mcp-client/)           │       │
│   │  (MCP-клиент)│                      │                            │       │
│   └─────────────┘                       │  ┌──────────────────────┐ │       │
│                                          │  │  SSH Connection       │ │       │
│                                          │  │  Manager              │ │       │
│                                          │  └──────────┬───────────┘ │       │
│                                          └─────────────┼─────────────┘       │
│                                                        │                     │
└────────────────────────────────────────────────────────┼─────────────────────┘
                                                         │
                                                    SSH (ключ)
                                                         │
┌────────────────────────────────────────────────────────┼─────────────────────┐
│                        Удалённый сервер                 │                     │
│                                                        │                     │
│   ┌────────────────────────────────────────────────────┼───────────────────┐ │
│   │  ServerLens (stdio режим)                          │                   │ │
│   │                                                    ▼                   │ │
│   │  ┌──────────────────┐    ┌────────────────────────────────────────┐   │ │
│   │  │  MCP Server       │    │  Модули                                │   │ │
│   │  │  (JSON-RPC 2.0)  │───►│                                        │   │ │
│   │  │                   │    │  ┌──────────┐ ┌──────────────┐        │   │ │
│   │  │  - initialize    │    │  │ LogReader │ │ ConfigReader │        │   │ │
│   │  │  - tools/list    │    │  │           │ │ (+ Redactor) │        │   │ │
│   │  │  - tools/call    │    │  └──────────┘ └──────────────┘        │   │ │
│   │  │                   │    │                                        │   │ │
│   │  └──────────────────┘    │  ┌──────────┐ ┌──────────────┐        │   │ │
│   │                           │  │ DbQuery  │ │ SystemInfo   │        │   │ │
│   │  ┌──────────────────┐    │  │ (PDO)    │ │ (shell_exec) │        │   │ │
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

## Компоненты

### 1. MCP Proxy (mcp-client/)

Локальный MCP-сервер на машине разработчика.

**Назначение:** Cursor не знает про SSH. MCP Proxy — мост между Cursor и удалёнными серверами.

**Протокол:** stdio (JSON-RPC 2.0) — Cursor запускает его как команду.

**Функции:**
- Читает локальную конфигурацию с SSH-параметрами
- Устанавливает SSH-соединения к серверам
- Запускает ServerLens в stdio-режиме на каждом сервере
- При нескольких серверах — добавляет префикс к именам инструментов
- Транслирует запросы от Cursor к нужному серверу
- Возвращает ответы обратно

**Жизненный цикл:**
1. Cursor запускает `serverlens-mcp` → SSH → удалённый ServerLens
2. Получает список инструментов от всех серверов
3. Ожидает запросы от Cursor
4. Для каждого `tools/call` — определяет сервер по имени инструмента
5. Пересылает запрос через SSH
6. Возвращает ответ Cursor

### 2. ServerLens (серверная часть)

MCP-сервер, работающий на удалённом сервере.

**Протокол:** stdio (для использования через MCP Proxy) или SSE (для прямого подключения).

**Модули:**

| Модуль | Что делает | Источник данных |
|--------|-----------|-----------------|
| **LogReader** | Чтение логов | Файлы из whitelist |
| **ConfigReader** | Чтение конфигов (секреты скрыты) | Файлы из whitelist |
| **DbQuery** | Безопасные запросы к БД | PostgreSQL (read-only user) |
| **SystemInfo** | Состояние системы | shell_exec (systemctl, docker, free, df) |

**Безопасность:**
- Whitelist-модель: доступны ТОЛЬКО явно разрешённые ресурсы
- Нет raw SQL — только структурированные запросы через whitelist полей
- Автоматическая редакция секретов (пароли, ключи, токены)
- Read-only пользователь PostgreSQL
- Rate limiting + аудит-логирование

---

## Поток данных

### Запрос: «Покажи последние ошибки nginx»

```
1. Cursor: "Покажи последние ошибки nginx"
   │
2. Claude/AI определяет нужный инструмент:
   │  tools/call → logs_search(source="nginx_error", query="error", lines=50)
   │
3. MCP Proxy (локально):
   │  ├── Определяет сервер по имени инструмента
   │  ├── Пересылает JSON-RPC через SSH
   │  │
4. ServerLens (на сервере):
   │  ├── Rate Limiter: OK
   │  ├── Проверяет "nginx_error" в whitelist: OK
   │  ├── Открывает /var/log/nginx/error.log (read-only)
   │  ├── Ищет строки, содержащие "error"
   │  ├── Ограничивает до 50 строк
   │  ├── Пишет в аудит-лог
   │  └── Возвращает результат
   │
5. MCP Proxy → Cursor → AI показывает пользователю
```

### Запрос: «Сколько пользователей за март?»

```
1. Cursor: "Сколько пользователей зарегистрировалось за март?"
   │
2. AI:
   │  tools/call → db_count(database="app_prod", table="users",
   │                         filters={"created_at": {"gte": "2026-03-01", "lt": "2026-04-01"}})
   │
3. MCP Proxy → SSH →
   │
4. ServerLens:
   │  ├── Проверяет "app_prod" в whitelist: OK
   │  ├── Проверяет "users" в whitelist: OK
   │  ├── Проверяет "created_at" в allowed_filters: OK
   │  ├── Строит SQL: SELECT COUNT(*) FROM "users" WHERE "created_at" >= $1 AND "created_at" < $2
   │  ├── Выполняет через PDO (prepared statement, read-only user)
   │  ├── Пишет в аудит-лог (без значений фильтров)
   │  └── Возвращает {"count": 1234}
   │
5. MCP Proxy → Cursor → "За март зарегистрировалось 1234 пользователя"
```

---

## Транспорты

### stdio (рекомендуемый)

```
Cursor ←stdin/stdout→ MCP Proxy ←SSH stdin/stdout→ ServerLens
```

- Каждое сообщение — JSON-объект на одной строке
- Без дополнительного фреймирга
- SSH обеспечивает шифрование и аутентификацию
- Нет открытых портов
- Нет необходимости в токенах (SSH-ключ = аутентификация)

### SSE (альтернативный)

```
MCP-клиент ←HTTP SSE→ ServerLens (через SSH-туннель)
```

- GET /sse — SSE-поток (server → client)
- POST /message?sessionId=xxx — сообщения (client → server)
- Bearer-токен для аутентификации
- Нужен SSH-туннель: `ssh -L 9600:127.0.0.1:9600 user@server`

---

## Структура проекта

```
sauron/
├── README.md
├── description.md              # Исходное описание
│
├── src/                        # Серверная часть (ServerLens)
│   ├── Application.php
│   ├── Config.php
│   ├── Mcp/
│   │   ├── Server.php          # MCP-протокол
│   │   └── Tool.php
│   ├── Transport/
│   │   ├── TransportInterface.php
│   │   ├── SseTransport.php    # SSE (ReactPHP)
│   │   └── StdioTransport.php  # stdio
│   ├── Auth/
│   │   ├── TokenAuth.php       # Bearer-токен (argon2id)
│   │   └── RateLimiter.php
│   ├── Module/
│   │   ├── ModuleInterface.php
│   │   ├── LogReader.php       # Логи
│   │   ├── ConfigReader.php    # Конфиги
│   │   ├── DbQuery.php         # PostgreSQL
│   │   └── SystemInfo.php      # Системная информация
│   ├── Security/
│   │   ├── PathGuard.php       # Защита от path traversal
│   │   └── Redactor.php        # Редакция секретов
│   └── Audit/
│       └── AuditLogger.php
│
├── bin/serverlens              # CLI сервера
├── composer.json               # Зависимости сервера
├── config.example.yaml         # Пример конфигурации сервера
│
├── mcp-client/                 # MCP-клиент (для машины разработчика)
│   ├── src/
│   │   ├── Config.php
│   │   ├── SshConnection.php   # SSH-подключение
│   │   └── McpProxy.php        # MCP-прокси
│   ├── bin/serverlens-mcp      # CLI клиента
│   ├── composer.json
│   ├── config.example.yaml     # SSH-конфигурация
│   └── docs/
│       └── README.md           # Документация MCP-клиента
│
├── docs/                       # Документация
│   ├── architecture.md         # Этот документ
│   └── server/
│       ├── setup.md            # Установка сервера
│       └── api.md              # API Reference
│
├── scripts/
│   ├── install.sh              # Установка на сервер
│   └── setup_db_users.sql      # SQL для PostgreSQL
│
└── etc/
    └── serverlens.service      # systemd unit
```

---

## Технологический стек

| Компонент | Технология | Почему |
|-----------|-----------|--------|
| Язык | **PHP 8.1+** | Широкая доступность, нет внешних runtime |
| MCP-протокол | **JSON-RPC 2.0** (реализован вручную) | Минимум зависимостей, полный контроль |
| HTTP (SSE) | **ReactPHP** | Async PHP для long-lived SSE-соединений |
| Конфигурация | **Symfony YAML** | Стандартный YAML-парсер для PHP |
| БД | **PDO + pdo_pgsql** | Встроенный в PHP, prepared statements |
| SSH | **openssh-client** (через proc_open) | Стандартный SSH, нет PHP-расширений |
| Хеширование | **password_hash (ARGON2ID)** | Встроенный в PHP 7.2+, безопасный |
| Процесс | **systemd** | Стандарт Linux для управления сервисами |
