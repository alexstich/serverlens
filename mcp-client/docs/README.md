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

1. **Cursor** запускает `serverlens-mcp` как stdio MCP-сервер.
2. **MCP-клиент** устанавливает SSH-соединение к удалённому серверу (keepalive встроен: `ServerAliveInterval=15`).
3. По SSH запускается **ServerLens** в stdio-режиме.
4. Cursor видит **два инструмента MCP** — не десятки префиксованных имён, а единый **диспетчер**:
   - **`serverlens_list`** — список подключённых серверов и удалённых инструментов (логи, БД, конфиги, система и т.д.).
   - **`serverlens_call`** — вызов конкретного инструмента на выбранном сервере: указываются `server`, `tool` и параметры, как у удалённого ServerLens.
5. Ответы возвращаются обратно через SSH.

**Модель v2 (dispatch):** раньше каждый удалённый инструмент экспортировался с префиксом (`production__logs_tail`, `staging__db_query` и десятки других). Теперь в MCP всегда ровно **2 инструмента**; выбор сервера и имени инструмента — внутри аргументов `serverlens_call`. Это упрощает список в Cursor и не раздувает число MCP-инструментов при нескольких серверах.

Cursor не знает про SSH — он общается с локальным MCP-сервером.

При потере соединения MCP-клиент может **автоматически переподключиться** к удалённым серверам (повторная установка SSH-сессий и повторная инициализация).

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

Имя ключа в `servers` (например `production`) — это **идентификатор сервера** для параметра `server` в `serverlens_call`. Несколько серверов задаются как несколько ключей в `servers:`; префиксы в именах MCP-инструментов больше не используются.

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

В конфиге перечисляются один или несколько серверов — каждый с блоком `ssh` и `remote`.

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
  options:                       # дополнительные SSH-опции (необязательно)
    ConnectTimeout: "10"         # таймаут подключения
    ServerAliveInterval: "30"    # переопределение keepalive (по умолчанию в клиенте уже 15 с)
    ServerAliveCountMax: "3"     # макс. пропущенных keepalive
```

Keepalive для SSH **встроен** в MCP-клиент (`ServerAliveInterval=15`). Блок `options` указывай, если нужны другие значения или дополнительные опции `ssh`.

### Параметры удалённого сервера

```yaml
remote:
  php: "php"                                         # путь к PHP (по умолчанию "php")
  serverlens_path: "/opt/serverlens/bin/serverlens"  # путь к ServerLens
  config_path: "/etc/serverlens/config.yaml"         # путь к конфигу ServerLens
```

---

## Доступные инструменты (Tools)

На стороне MCP в Cursor отображаются **только два инструмента**:

| Инструмент | Назначение |
|------------|------------|
| `serverlens_list` | Возвращает список настроенных серверов и **полный каталог удалённых инструментов** с каждого ServerLens (логи, конфиги, БД, система и т.д.) — по сути, то, что раньше «размножалось» префиксами, теперь агрегируется здесь. |
| `serverlens_call` | Выполняет один удалённый инструмент: в аргументах задаются `server` (имя из `config.yaml`), `tool` (например `logs_tail`, `db_query`) и параметры, как в [API ServerLens](../../docs/server/api.md). |

Фактические операции (`logs_tail`, `logs_search`, `db_query`, `config_read`, `system_docker` и остальные) по-прежнему выполняются **на удалённом ServerLens**; меняется только способ вызова через MCP — через диспетчер `serverlens_call`, а не через отдельное MCP-имя на каждую комбинацию «сервер + инструмент».

---

## Примеры использования в Cursor

После подключения MCP к Cursor, можно просто спрашивать на естественном языке:

- *«Покажи последние ошибки nginx»* → ассистент вызовет нужный инструмент через `serverlens_call` (например `logs_search`).
- *«Сколько пользователей зарегистрировалось за март?»* → `db_count` на выбранном сервере.
- *«Покажи конфигурацию PostgreSQL»* → `config_read`.
- *«Какой статус Docker-контейнеров?»* → `system_docker`.
- *«Найди в логах upstream timed out»* → `logs_search`.

Поведение для пользователя по смыслу то же; меняется внутренняя схема имён MCP (v2: два инструмента и диспетчеризация).

---

## Устранение проблем

### MCP не подключается

```bash
# Проверь, что скрипт запускается:
php mcp-client/bin/serverlens-mcp --config ~/.serverlens/config.yaml

# В stderr увидишь (пример):
# [MCP] Config: /Users/.../.serverlens/config.yaml
# [MCP] Connecting to server 'production'...
# [MCP:production] SSH command: ssh -o BatchMode=yes ...
# [MCP:production] Initialized: ServerLens v1.0.0
# [MCP] Discovered 17 tools on 'production'
# [MCP] Ready: 1 server(s), 17 remote tool(s), 2 MCP tools
```

При нескольких серверах первая цифра в `Ready` — число серверов, вторая — суммарное число удалённых инструментов; **MCP tools** всегда **2** (`serverlens_list` и `serverlens_call`).

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
