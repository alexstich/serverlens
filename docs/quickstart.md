# ServerLens — Полная установка от нуля

Этот документ описывает **пошаговую** установку системы целиком: от сервера до работающего MCP в Cursor.

```
Шаг 1–3: НА УДАЛЁННОМ СЕРВЕРЕ
Шаг 4–6: НА ТВОЕЙ МАШИНЕ (разработчика)
```

---

## Шаг 1. Клонировать репозиторий на сервер

Заходим на сервер по SSH и клонируем:

```bash
ssh alex@1.2.3.4

git clone git@gitlab.rucode.org:devtools/sauron.git ~/serverlens-src
cd ~/serverlens-src
```

> `/opt` принадлежит root, поэтому клонируем в домашнюю директорию. Скрипт установки сам скопирует нужные файлы в `/opt/serverlens`.

---

## Шаг 2. Установить и настроить ServerLens

**Вариант A — интерактивный установщик (рекомендуется):**

```bash
sudo bash scripts/install.sh
```

Установщик сделает всё за один проход:
1. Проверит PHP (версию и расширения)
2. Создаст системного пользователя `serverlens` и директории
3. Установит зависимости (Composer)
4. **Запустит мастер настройки:**
   - Просканирует установленные сервисы (nginx, PostgreSQL, Redis, PHP-FPM, Docker...)
   - Покажет найденные лог-файлы и конфиги — вы выберете нужные
   - Предложит настроить подключение к PostgreSQL — выбрать БД, таблицы, колонки
   - Автоматически определит чувствительные колонки (пароли, токены) и скроет их
   - Создаст read-only пользователя PostgreSQL
   - **Сгенерирует готовый `config.yaml`**
5. Установит systemd-сервис

> После установки конфиг почти не нужно редактировать вручную — визард покрывает все основные настройки.

**Вариант A без визарда** (для автоматизации / CI):

```bash
sudo bash scripts/install.sh --no-wizard
```

Установит всё, но скопирует `config.example.yaml` без интерактива. Конфиг нужно будет заполнить вручную.

**Вариант B — полностью вручную:**

```bash
sudo mkdir -p /opt/serverlens /etc/serverlens /var/log/serverlens
sudo cp -r src/ bin/ composer.json /opt/serverlens/
cd /opt/serverlens && sudo composer install --no-dev --optimize-autoloader
sudo chmod +x /opt/serverlens/bin/serverlens
sudo cp ~/serverlens-src/config.example.yaml /etc/serverlens/config.yaml
sudo nano /etc/serverlens/config.yaml   # заполнить вручную
```

**Отдельная настройка PostgreSQL** (можно запустить позже повторно):

```bash
sudo bash scripts/setup_db.sh
```

Скрипт подключится к PostgreSQL, покажет базы/таблицы/колонки, создаст read-only пользователя и обновит секцию `databases` в `config.yaml`.

**Права на логи** — после установки нужно добавить пользователя `serverlens` в группы:

```bash
sudo usermod -aG adm serverlens        # для /var/log/nginx/
sudo usermod -aG postgres serverlens   # для /var/log/postgresql/
```

> Установщик подскажет нужные команды в финальном выводе.

---

## Шаг 3. Проверить, что ServerLens работает на сервере

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

## Шаг 4. Установить MCP-клиент на своей машине

Теперь переходим **на свой компьютер** (машина разработчика):

```bash
git clone git@gitlab.rucode.org:devtools/sauron.git ~/serverlens
cd ~/serverlens/mcp-client
composer install
```

---

## Шаг 5. Настроить SSH-подключение

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

## Шаг 6. Подключить к Cursor

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
