# ServerLens — Полная установка от нуля

Этот документ описывает **пошаговую** установку системы целиком: от сервера до работающего MCP в Cursor.

```
Шаг 1–4: НА УДАЛЁННОМ СЕРВЕРЕ
Шаг 5–7: НА ТВОЕЙ МАШИНЕ (разработчика)
```

---

## Шаг 1. Клонировать репозиторий на сервер

Заходим на сервер по SSH и клонируем:

```bash
ssh alex@1.2.3.4

git clone git@gitlab.rucode.org:devtools/sauron.git /opt/serverlens-src
cd /opt/serverlens-src
```

---

## Шаг 2. Установить ServerLens на сервере

**Вариант A — автоматически (рекомендуется):**

```bash
sudo bash scripts/install.sh
```

Скрипт сам создаст пользователя `serverlens`, скопирует файлы в `/opt/serverlens`, установит зависимости через Composer и настроит systemd.

**Вариант B — вручную:**

```bash
sudo mkdir -p /opt/serverlens /etc/serverlens /var/log/serverlens
sudo cp -r src/ bin/ composer.json /opt/serverlens/
cd /opt/serverlens && sudo composer install --no-dev --optimize-autoloader
sudo chmod +x /opt/serverlens/bin/serverlens
sudo cp /opt/serverlens-src/config.example.yaml /etc/serverlens/config.yaml
```

---

## Шаг 3. Настроить конфигурацию ServerLens

Отредактировать `/etc/serverlens/config.yaml` — указать свои логи, конфиги и базы данных:

```bash
sudo nano /etc/serverlens/config.yaml
```

**Обязательно настроить:**

1. **Логи** — прописать пути к файлам логов, которые нужны:
```yaml
logs:
  sources:
    - name: "nginx_error"
      path: "/var/log/nginx/error.log"
      format: "plain"
      max_lines: 2000
```

2. **Конфиги** (если нужны):
```yaml
configs:
  sources:
    - name: "nginx_main"
      path: "/etc/nginx/nginx.conf"
      redact: []
```

3. **Базы данных** (если нужны) — сначала создать read-only пользователя PostgreSQL:
```bash
sudo -u postgres psql -f /opt/serverlens-src/scripts/setup_db_users.sql
```
Затем прописать подключение в конфиге и создать файл с паролем:
```bash
echo "SL_DB_APP_PASS=надёжный_пароль" | sudo tee /etc/serverlens/env
sudo chmod 640 /etc/serverlens/env
```

4. **Права на логи** — пользователь `serverlens` должен читать логи:
```bash
sudo usermod -aG adm serverlens        # для /var/log/nginx/
sudo usermod -aG postgres serverlens   # для /var/log/postgresql/
```

---

## Шаг 4. Проверить, что ServerLens работает на сервере

```bash
# Проверить конфиг:
sudo -u serverlens php /opt/serverlens/bin/serverlens validate-config \
  --config /etc/serverlens/config.yaml

# Быстрый тест stdio (Ctrl+C для выхода):
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  sudo -u serverlens php /opt/serverlens/bin/serverlens serve --config /etc/serverlens/config.yaml --stdio
```

Должен вернуть JSON с `"serverInfo":{"name":"ServerLens","version":"1.0.0"}`.

> **Примечание:** systemd-сервис (`systemctl start serverlens`) нужен только для режима SSE. Для работы через MCP-клиент по SSH сервис запускать **не нужно** — MCP-клиент сам запускает ServerLens при подключении.

---

## Шаг 5. Установить MCP-клиент на своей машине

Теперь переходим **на свой компьютер** (машина разработчика):

```bash
git clone git@gitlab.rucode.org:devtools/sauron.git ~/serverlens
cd ~/serverlens/mcp-client
composer install
```

---

## Шаг 6. Настроить SSH-подключение

Создать конфигурацию MCP-клиента:

```bash
mkdir -p ~/.serverlens
cp config.example.yaml ~/.serverlens/config.yaml
```

Отредактировать `~/.serverlens/config.yaml`:

```yaml
servers:
  production:                         # имя сервера (любое)
    ssh:
      host: "1.2.3.4"                # IP или hostname
      user: "alex"                    # SSH-пользователь
      port: 22
      key: "~/.ssh/id_ed25519"       # путь к SSH-ключу
    remote:
      php: "php"                      # путь к PHP на сервере
      serverlens_path: "/opt/serverlens/bin/serverlens"
      config_path: "/etc/serverlens/config.yaml"
```

Проверить, что SSH работает:

```bash
ssh -i ~/.ssh/id_ed25519 alex@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config"
```

---

## Шаг 7. Подключить к Cursor

Добавить в `~/.cursor/mcp.json` (создать файл, если его нет):

```json
{
  "mcpServers": {
    "serverlens": {
      "command": "php",
      "args": [
        "/Users/ТВОЙ_ПОЛЬЗОВАТЕЛЬ/serverlens/mcp-client/bin/serverlens-mcp",
        "--config",
        "/Users/ТВОЙ_ПОЛЬЗОВАТЕЛЬ/.serverlens/config.yaml"
      ]
    }
  }
}
```

> **Важно:** пути должны быть **абсолютными**.

Перезапустить Cursor. В логах MCP (Output → MCP) должно появиться:

```
[MCP] Connecting to server 'production'...
[MCP:production] Initialized: ServerLens v1.0.0
[MCP] Discovered 17 tools on 'production'
[MCP] Ready: 1 server(s), 17 tool(s)
```

---

## Готово!

Теперь можно спрашивать в Cursor на естественном языке:
- *«Покажи последние ошибки nginx»*
- *«Сколько пользователей зарегистрировалось за март?»*
- *«Какой статус Docker-контейнеров?»*
- *«Покажи конфигурацию PostgreSQL»*

---

## Ссылки

| Документ | Что содержит |
|----------|-------------|
| [Архитектура](architecture.md) | Как устроена система, поток данных, диаграммы |
| [Настройка сервера](server/setup.md) | Подробная конфигурация всех модулей ServerLens |
| [API Reference](server/api.md) | Полный справочник всех 17 инструментов |
| [MCP-клиент](../mcp-client/docs/README.md) | Детальная документация MCP-прокси |
