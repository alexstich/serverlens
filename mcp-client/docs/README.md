# ServerLens MCP-прокси — Документация

> **Предусловие:** на удалённом сервере уже должен быть установлен ServerLens.
> Если ещё не установлен — сначала выполните [docs/quickstart.md](../../docs/quickstart.md) (шаги 1–4).

Локальный MCP-сервер, который работает на машине разработчика и подключается к удалённым серверам ServerLens через SSH.

## Как это работает

```
┌─────────────┐    stdio    ┌──────────────────┐    SSH     ┌──────────────┐
│   Cursor /   │◄──────────►│  ServerLens MCP   │◄─────────►│  ServerLens   │
│   Claude     │            │  (на твоей машине)│            │  (на сервере) │
└─────────────┘             └──────────────────┘            └──────────────┘
```

1. **Cursor** запускает `serverlens-mcp` как stdio MCP-сервер
2. **MCP-клиент** устанавливает SSH-соединение к удалённому серверу
3. По SSH запускается **ServerLens** в stdio-режиме
4. Все запросы от Cursor транслируются через SSH к ServerLens
5. Ответы возвращаются обратно

Cursor не знает про SSH — он просто общается с локальным MCP-сервером.

---

## Установка

### 1. Клонировать репозиторий

```bash
git clone git@gitlab.rucode.org:devtools/sauron.git
cd sauron/mcp-client
composer install
```

### 2. Создать конфигурацию

```bash
mkdir -p ~/.serverlens
cp config.example.yaml ~/.serverlens/config.yaml
```

Отредактируй `~/.serverlens/config.yaml`:

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"        # IP или hostname сервера
      user: "alex"            # SSH-пользователь
      port: 22
      key: "~/.ssh/id_ed25519"
    remote:
      php: "php"
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"
```

### 3. Проверить SSH-доступ

Убедись, что SSH-ключ работает:

```bash
ssh -i ~/.ssh/id_ed25519 alex@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config"
```

### 4. Подключить к Cursor

Добавь в `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/полный/путь/к/sauron/mcp-client/bin/serverlens-mcp",
        "--config",
        "/Users/ваш_пользователь/.serverlens/config.yaml"
      ]
    }
  }
}
```

Перезапусти Cursor. ServerLens появится в списке доступных MCP-серверов.

---

## Конфигурация

### Один сервер

При одном сервере инструменты доступны без префикса:
- `logs_tail`, `logs_search`, `db_query` и т.д.

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"
      user: "alex"
      key: "~/.ssh/id_ed25519"
    remote:
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"
```

### Несколько серверов

При нескольких серверах инструменты получают префикс с именем сервера:
- `production__logs_tail`, `staging__logs_tail`
- `production__db_query`, `staging__db_query`

```yaml
servers:
  production:
    ssh:
      host: "1.2.3.4"
      user: "alex"
      key: "~/.ssh/id_ed25519"
    remote:
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"

  staging:
    ssh:
      host: "5.6.7.8"
      user: "alex"
      key: "~/.ssh/id_ed25519"
    remote:
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"
```

### Параметры SSH

```yaml
ssh:
  host: "1.2.3.4"              # обязательно
  user: "alex"                  # обязательно
  port: 22                      # по умолчанию 22
  key: "~/.ssh/id_ed25519"      # путь к SSH-ключу (~ раскрывается)
  options:                       # дополнительные SSH-опции
    ConnectTimeout: "10"         # таймаут подключения
    ServerAliveInterval: "30"    # keepalive каждые 30 сек
    ServerAliveCountMax: "3"     # макс пропущенных keepalive
```

### Параметры удалённого сервера

```yaml
remote:
  php: "php"                                         # путь к PHP (по умолчанию "php")
  serverlens_path: "/opt/serverlens/bin/serverlens"  # путь к ServerLens
  config_path: "/etc/serverlens/config.yaml"         # путь к конфигу ServerLens
```

---

## Доступные инструменты (Tools)

MCP-клиент автоматически получает список инструментов от каждого подключённого сервера.

### Логи

| Инструмент | Описание | Параметры |
|-----------|----------|-----------|
| `logs_list` | Список доступных логов | — |
| `logs_tail` | Последние N строк | `source`, `lines` (max 500) |
| `logs_search` | Поиск по тексту/regex | `source`, `query`, `regex`, `lines` |
| `logs_count` | Размер и количество строк | `source` |
| `logs_time_range` | Записи за период | `source`, `from`, `to`, `lines` |

### Конфигурации

| Инструмент | Описание | Параметры |
|-----------|----------|-----------|
| `config_list` | Список доступных конфигов | — |
| `config_read` | Чтение конфига (секреты скрыты) | `source` |
| `config_search` | Поиск по конфигу | `source`, `query` |

### База данных

| Инструмент | Описание | Параметры |
|-----------|----------|-----------|
| `db_list` | Список баз и таблиц | — |
| `db_describe` | Структура таблицы | `database`, `table` |
| `db_query` | Выборка записей | `database`, `table`, `fields`, `filters`, `order_by`, `limit`, `offset` |
| `db_count` | Количество записей | `database`, `table`, `filters` |
| `db_stats` | Статистика по полю | `database`, `table`, `field` |

### Система

| Инструмент | Описание | Параметры |
|-----------|----------|-----------|
| `system_overview` | CPU, RAM, Disk, Uptime | — |
| `system_services` | Статус systemd-сервисов | `service` (опционально) |
| `system_docker` | Статус Docker-контейнеров | `stack` (опционально) |
| `system_connections` | Активные соединения | — |

---

## Примеры использования в Cursor

После подключения MCP к Cursor, можно просто спрашивать на естественном языке:

- *«Покажи последние ошибки nginx»* → вызовет `logs_search`
- *«Сколько пользователей зарегистрировалось за март?»* → вызовет `db_count`
- *«Покажи конфигурацию PostgreSQL»* → вызовет `config_read`
- *«Какой статус Docker-контейнеров?»* → вызовет `system_docker`
- *«Найди в логах upstream timed out»* → вызовет `logs_search`

---

## Устранение проблем

### MCP не подключается

```bash
# Проверь, что скрипт запускается:
php mcp-client/bin/serverlens-mcp --config ~/.serverlens/config.yaml

# В stderr увидишь:
# [MCP] Config: /Users/.../.serverlens/config.yaml
# [MCP] Connecting to server 'production'...
# [MCP:production] SSH command: ssh -o BatchMode=yes ...
# [MCP:production] Initialized: ServerLens v1.0.0
# [MCP] Discovered 17 tools on 'production'
# [MCP] Ready: 1 server(s), 17 tool(s)
```

### SSH не подключается

```bash
# Проверь SSH вручную:
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 alex@1.2.3.4 echo "ok"

# Проверь ServerLens на сервере:
ssh alex@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config"
```

### Инструменты не появляются в Cursor

1. Проверь `~/.cursor/mcp.json` — пути должны быть **абсолютными**
2. Перезапусти Cursor после изменения конфигурации
3. Посмотри логи MCP в терминале Cursor (Output → MCP)

---

## Связанные документы

| Документ | Описание |
|----------|----------|
| [Быстрый старт (от нуля)](../../docs/quickstart.md) | Пошаговая установка всей системы |
| [Настройка сервера](../../docs/server/setup.md) | Подробная конфигурация ServerLens |
| [API Reference](../../docs/server/api.md) | Справочник всех инструментов |
| [Архитектура](../../docs/architecture.md) | Как устроена система |
