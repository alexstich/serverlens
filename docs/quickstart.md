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

**Права доступа для SSH-пользователя** — MCP-клиент подключается по SSH от имени обычного пользователя (например `rucode`). Этот пользователь должен:
1. Читать конфиг ServerLens (`/etc/serverlens/config.yaml`)
2. Читать лог-файлы, указанные в конфиге

Для этого SSH-пользователя нужно добавить в группу `serverlens` и в группы, владеющие логами.

> **Установщик (`install.sh`) автоматически:**
> - добавляет системного пользователя `serverlens` в группу `adm` (если она есть)
> - исправляет права на PHP-FPM логи (добавляет group read)
> - исправляет права на RabbitMQ логи (добавляет group read)
>
> Но SSH-пользователя нужно добавить в группы **вручную** (установщик покажет нужные команды).

Каждый сервис хранит логи с собственной группой-владельцем. SSH-пользователя нужно добавить в группу, которой принадлежат логи:

```bash
sudo usermod -aG serverlens rucode     # доступ к конфигу ServerLens
```

Дальше — зависит от сервисов. Установщик определяет группы автоматически и покажет точные команды, но типичные:

| Сервис | Группа (Ubuntu) | Группа (CentOS/RHEL) | Команда |
|--------|------------------|-----------------------|---------|
| syslog, auth.log, nginx | `adm` | `nginx` / `root` | `sudo usermod -aG adm rucode` |
| PHP-FPM | `adm` (после фикса) | `root` | `sudo usermod -aG adm rucode` |
| PostgreSQL | `postgres` | `postgres` | `sudo usermod -aG postgres rucode` |
| RabbitMQ | `rabbitmq` | `rabbitmq` | `sudo usermod -aG rabbitmq rucode` |

> **Что такое `adm`?** Это стандартная системная группа Ubuntu/Debian для чтения логов. Создаётся при установке ОС. Большинство файлов в `/var/log/` принадлежат `root:adm`. Проверить: `getent group adm`

**PHP-FPM логи** — частая проблема:

PHP-FPM пишет логи в `/var/log/php*-fpm.log`. На Ubuntu эти файлы по умолчанию принадлежат `root:root` с правами `600` — не читаемы никем, кроме root. Установщик автоматически меняет группу на `adm` и ставит `640`, но после **ротации логов** (logrotate) права могут сброситься.

Чтобы права сохранялись, проверьте конфиг logrotate:

```bash
cat /etc/logrotate.d/php8.2-fpm
# Должна быть строка (добавьте, если нет):
create 0640 root adm
```

**RabbitMQ логи:**

Логи в `/var/log/rabbitmq/` принадлежат `rabbitmq:rabbitmq`. Установщик добавляет group read, но SSH-пользователь должен быть в группе `rabbitmq`.

**Как проверить, какая группа нужна для любого лога:**

```bash
ls -la /var/log/nginx/
# -rw-r----- 1 root adm 12345 Mar 25 10:00 access.log
#                    ^^^ — вот эту группу нужно добавить

ls -la /var/log/rabbitmq/
# drwxr-x--- 2 rabbitmq rabbitmq 4096 Mar 25 10:00 .
#                       ^^^^^^^^ — нужна группа rabbitmq
```

> **Важно:** после `usermod` нужно **перелогиниться** (завершить SSH-сессию и зайти заново), чтобы новые группы применились. Или выполнить `newgrp serverlens` в текущей сессии.

> Установщик подскажет нужные команды в финальном выводе.

---

## Шаг 3. Проверить, что ServerLens работает на сервере

Проверяем **от имени SSH-пользователя** (того, под которым будет подключаться MCP-клиент):

```bash
# Проверить конфиг:
php /opt/serverlens/bin/serverlens validate-config \
  --config /etc/serverlens/config.yaml

# Быстрый тест stdio (Ctrl+C для выхода):
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  php /opt/serverlens/bin/serverlens serve --config /etc/serverlens/config.yaml --stdio
```

Должен вернуть JSON с `"serverInfo":{"name":"ServerLens","version":"1.0.0"}`.

> Если получаете `File ... cannot be read` — значит SSH-пользователь не добавлен в группу `serverlens` (см. шаг 2 — раздел «Права доступа для SSH-пользователя»).

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

## Шаг 5. Указать, к каким серверам подключаться

MCP-клиент — это **локальная** программа на твоей машине. Она подключается к **удалённым** серверам по SSH. Для этого нужен конфиг, в котором перечислены серверы и SSH-доступы к ним.

Скопировать шаблон:

```bash
mkdir -p ~/.serverlens
cp ~/serverlens/mcp-client/config.example.yaml ~/.serverlens/config.yaml
```

Открыть `~/.serverlens/config.yaml` и заполнить данные **удалённого сервера** (того, на который ставили ServerLens в шагах 1–3):

```yaml
servers:
  # ↓ Это произвольное имя. Придумай сам: monitor, production, web1 — что угодно.
  #   Если серверов несколько, имя станет префиксом инструментов: monitor_logs_tail, web1_logs_tail
  #   Если сервер один — префикс не добавляется.
  monitor:
    ssh:
      host: "1.2.3.4"                # IP или hostname удалённого сервера
      user: "rucode"                  # SSH-пользователь (тот, под которым заходишь по SSH)
      port: 22
      key: "~/.ssh/id_ed25519"       # путь к SSH-ключу НА ТВОЕЙ МАШИНЕ
    remote:
      php: "php"                      # путь к PHP НА УДАЛЁННОМ СЕРВЕРЕ
      serverlens_path: "/opt/serverlens/bin/serverlens"  # путь к ServerLens НА СЕРВЕРЕ
      config_path: "/etc/serverlens/config.yaml"         # путь к конфигу НА СЕРВЕРЕ
```

> **Несколько серверов?** Просто добавь ещё один блок ниже с другим именем:
> ```yaml
>   staging:
>     ssh:
>       host: "5.6.7.8"
>       user: "rucode"
>       key: "~/.ssh/id_ed25519"
>     remote:
>       php: "php"
>       serverlens_path: "/opt/serverlens/bin/serverlens"
>       config_path: "/etc/serverlens/config.yaml"
> ```

Проверить, что SSH-подключение работает:

```bash
ssh -i ~/.ssh/id_ed25519 rucode@1.2.3.4 "php /opt/serverlens/bin/serverlens validate-config --config /etc/serverlens/config.yaml"
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

## Обновление

### На сервере

Заходим на сервер, переходим в директорию с исходниками:

```bash
ssh alex@1.2.3.4
cd ~/serverlens-src
```

**Рекомендуемый способ** (git pull от обычного пользователя + обновление от root):

```bash
git pull                                # от обычного пользователя
sudo bash scripts/update.sh --no-pull   # от root
```

**Если на сервере есть прямой доступ к Git-репозиторию**, можно в одну команду:

```bash
sudo bash scripts/update.sh
```

Скрипт автоматически:
1. Выполнит `git pull` (определит владельца репо и запустит от его имени)
2. Скопирует обновлённые файлы (`src/`, `bin/`, `composer.json`) в `/opt/serverlens/`
3. Обновит PHP-зависимости (`composer install`)
4. Обновит systemd-сервис, если он изменился
5. Проверит валидность конфига

**Конфиг `/etc/serverlens/config.yaml` не затрагивается** — все пользовательские настройки сохраняются.

Флаги:
- `--no-pull` — не делать `git pull` (если уже обновили вручную)
- `--restart` — перезапустить systemd-сервис после обновления

```bash
# Если сервис запущен (SSE-режим), перезапустить автоматически:
sudo bash scripts/update.sh --restart --no-pull
```

> Для SSH+stdio режима перезапуск сервиса не нужен — MCP-клиент запускает ServerLens при каждом подключении, поэтому обновлённый код подхватится автоматически.

### На машине разработчика (MCP-клиент)

```bash
cd ~/serverlens
git pull
cd mcp-client && composer install
```

Перезапустите Cursor, чтобы MCP-клиент подхватил изменения.

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
