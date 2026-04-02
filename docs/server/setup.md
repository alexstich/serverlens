# ServerLens — Установка и настройка сервера

> **Перед чтением:** если вы устанавливаете систему впервые, начните с [docs/quickstart.md](../quickstart.md) — там пошаговая инструкция от нуля.
> Этот документ — подробный справочник по всем параметрам конфигурации.

## Требования

- **PHP 8.1+** с расширениями: `pdo_pgsql`, `json`, `mbstring`
- **Composer** (для установки зависимостей)
- **SSH-доступ** к серверу
- **PostgreSQL** (для модуля DbQuery, опционально)

---

## Установка

### Вариант A — Интерактивный установщик (рекомендуется)

```bash
git clone git@gitlab.rucode.org:devtools/serverlens.git ~/serverlens-src
cd ~/serverlens-src
sudo bash scripts/install.sh
```

> Клонируем в домашнюю директорию — `/opt` принадлежит root. Скрипт сам скопирует файлы в `/opt/serverlens`.

Установщик выполняет всё за один проход:
- Проверяет PHP (версия >= 8.1, расширения)
- Создаёт системного пользователя `serverlens`
- Копирует файлы в `/opt/serverlens`, запускает `composer install`
- Создаёт директории `/etc/serverlens`, `/var/log/serverlens`
- **Запускает интерактивный мастер настройки:**
  - Сканирует установленные сервисы (nginx, PostgreSQL, Redis, PHP-FPM, Docker, RabbitMQ...)
  - Обнаруживает лог-файлы и конфиги — вы выбираете нужные
  - Настраивает подключение к PostgreSQL (выбор БД, таблиц, автоопределение чувствительных колонок, создание read-only пользователя)
  - Генерирует готовый `/etc/serverlens/config.yaml`
- Устанавливает systemd-сервис

Для пропуска визарда (автоматизация, CI):

```bash
sudo bash scripts/install.sh --no-wizard
```

### Вариант B — Ручная

```bash
git clone git@gitlab.rucode.org:devtools/serverlens.git ~/serverlens-src
sudo mkdir -p /opt/serverlens /etc/serverlens /var/log/serverlens
sudo cp -r ~/serverlens-src/src/ ~/serverlens-src/bin/ ~/serverlens-src/composer.json /opt/serverlens/
cd /opt/serverlens && sudo composer install --no-dev --optimize-autoloader
sudo chmod +x /opt/serverlens/bin/serverlens
sudo cp ~/serverlens-src/config.example.yaml /etc/serverlens/config.yaml
```

### Отдельная настройка PostgreSQL

Для повторной настройки БД (добавить базу, пересоздать пользователя):

```bash
sudo bash scripts/setup_db.sh
```

Скрипт подключится к PostgreSQL, покажет доступные базы и таблицы, создаст read-only пользователя и обновит секцию `databases` в config.yaml.

### Когда нужен systemd?

| Способ использования | systemd нужен? |
|---------------------|:--------------:|
| MCP-прокси через SSH (рекомендуемый) | **Нет** — MCP-прокси сам запускает ServerLens по SSH |
| SSE через SSH-туннель | **Да** — ServerLens должен работать постоянно |

---

## Конфигурация

Основной файл: `/etc/serverlens/config.yaml`

### server — Настройки сервера

```yaml
server:
  host: "127.0.0.1"    # ТОЛЬКО localhost (безопасность)
  port: 9600            # порт для SSE-транспорта
  transport: "sse"      # "sse" или "stdio"
```

> **Важно:** `host` может быть только `127.0.0.1`, `localhost` или `::1`. ServerLens не принимает подключения извне — только через SSH-туннель или stdio.

### auth — Аутентификация

```yaml
auth:
  tokens:
    - hash: "$argon2id$v=19$m=65536,t=4,p=1$..."   # хеш токена
      created: "2026-03-25"
      expires: "2026-06-25"                          # 90 дней
  max_failed_attempts: 5    # блокировка после 5 неверных попыток
  lockout_minutes: 15       # длительность блокировки
```

Генерация токена:

```bash
php bin/serverlens token generate
```

Вывод:
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

> Токен нужен только для SSE-транспорта. При использовании stdio (через MCP-клиент по SSH) аутентификация обеспечивается SSH-ключами.

### rate_limiting — Ограничение запросов

```yaml
rate_limiting:
  requests_per_minute: 60   # максимум запросов в минуту
  max_concurrent: 5          # максимум одновременных запросов
```

### audit — Аудит-логирование

```yaml
audit:
  enabled: true
  path: "/var/log/serverlens/audit.log"
  log_params: false          # НЕ логировать значения параметров
  retention_days: 90
```

Формат аудит-лога (JSON Lines):
```json
{"timestamp":"2026-03-25T14:30:22Z","client_ip":"127.0.0.1","tool":"logs_search","params_summary":{"source":"nginx_error","query_length":18},"result":{"status":"ok","duration_ms":23}}
```

### logs — Источники логов

```yaml
logs:
  sources:
    - name: "nginx_access"           # имя для обращения
      path: "/var/log/nginx/access.log"
      format: "nginx_combined"       # тип парсера (plain, json, nginx_combined, postgres, docker)
      max_lines: 5000                # максимум строк за запрос

    - name: "nginx_error"
      path: "/var/log/nginx/error.log"
      format: "plain"
      max_lines: 2000

    - name: "app_api"
      path: "/var/log/app/api.log"
      format: "json"
      max_lines: 3000

    # Директории с логами (файлы меняются / ротируются)
    - name: "app_api_logs"
      path: "/var/www/app/runtime/logs/api"
      type: "directory"            # автоматический листинг файлов
      pattern: "*.log"             # glob-паттерн (по умолчанию *.log)
      format: "plain"
      max_lines: 5000
```

- `type: "directory"` — ServerLens автоматически находит файлы по паттерну, показывает их в `logs_list` с размерами и датами.
- Файлы доступны как `"app_api_logs/20251031.log"` в параметре `source`.
- Удобно для логов с ежедневной ротацией (Yii, Laravel и т.п.).
- PathGuard защищает от выхода за пределы директории.

**Безопасность:**
- Путь берётся ТОЛЬКО из конфигурации, не от клиента
- `realpath()` проверка — защита от symlink-атак
- Файлы открываются в read-only режиме
- Лимит строк жёстко ограничен

### configs — Конфигурационные файлы

```yaml
configs:
  sources:
    - name: "nginx_main"
      path: "/etc/nginx/nginx.conf"
      redact: []                      # ничего не скрывать

    - name: "nginx_sites"
      path: "/etc/nginx/sites-enabled/"
      type: "directory"               # все файлы в директории
      redact: []

    - name: "postgres_main"
      path: "/etc/postgresql/16/main/postgresql.conf"
      redact:                          # скрыть параметры, содержащие эти слова
        - "password"
        - "ssl_key_file"

    - name: "docker_compose"
      path: "/opt/app/docker-compose.yml"
      redact:
        - pattern: "(?i)(password|secret|key|token)\\s*[:=]\\s*\\S+"
          replacement: "$1: [REDACTED]"
```

**Автоматическая редакция:** Помимо правил из конфига, ServerLens автоматически скрывает:
- `password`, `passwd`, `pass`
- `secret`, `api_key`, `apikey`
- `token`, `auth_token`, `access_token`
- `private_key`
- `connection_string`, `dsn`, `database_url`

### databases — Подключения к PostgreSQL

```yaml
databases:
  connections:
    - name: "app_prod"
      host: "localhost"
      port: 5432
      database: "app_production"
      user: "serverlens_readonly"      # read-only пользователь
      password_env: "SL_DB_APP_PASS"   # пароль из переменной окружения

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

**Создание read-only пользователя PostgreSQL:**

Проще всего — через интерактивный скрипт:

```bash
sudo bash scripts/setup_db.sh
```

Скрипт сам: подключится к PostgreSQL, покажет базы и таблицы, создаст пользователя и обновит config.yaml.

Вручную:

```sql
CREATE USER serverlens_readonly WITH PASSWORD 'надёжный_пароль';
ALTER USER serverlens_readonly SET default_transaction_read_only = on;
ALTER USER serverlens_readonly SET statement_timeout = '30s';

-- Для каждой базы:
\c app_production
GRANT CONNECT ON DATABASE app_production TO serverlens_readonly;
GRANT USAGE ON SCHEMA public TO serverlens_readonly;
GRANT SELECT ON users, api_requests TO serverlens_readonly;
```

> Пароль передаётся через переменную окружения (`password_env`), а не в конфиге. Установите переменную в `/etc/serverlens/env`.

### system — Системная информация

```yaml
system:
  enabled: true
  allowed_services:             # whitelist systemd-сервисов
    - "nginx"
    - "postgresql"
    - "rabbitmq-server"
  allowed_docker_stacks:        # whitelist Docker-стеков
    - "app"
```

---

## Запуск

### Ручной запуск (для тестирования)

```bash
# SSE-транспорт
php bin/serverlens serve --config /etc/serverlens/config.yaml

# Stdio-транспорт (используется MCP-клиентом через SSH)
php bin/serverlens serve --config /etc/serverlens/config.yaml --stdio
```

### Через systemd (для продакшена)

```bash
sudo systemctl start serverlens
sudo systemctl enable serverlens   # автозапуск
sudo systemctl status serverlens   # проверка статуса
```

---

## CLI-команды

```bash
# Запуск сервера
php bin/serverlens serve [--config <path>] [--stdio]

# Генерация токена
php bin/serverlens token generate

# Хеширование токена (для ручного добавления в конфиг)
php bin/serverlens token hash <token>

# Проверка конфигурации
php bin/serverlens validate-config [--config <path>]
```

---

## Безопасность

### Модель защиты

| Уровень | Механизм |
|---------|----------|
| Сеть | Bind на 127.0.0.1 — порт закрыт снаружи |
| Транспорт | SSH-ключи (для stdio) или SSH-туннель (для SSE) |
| Приложение | Bearer-токен (argon2id), rate limiting, блокировка IP |
| Данные | Whitelist путей/таблиц/полей, редакция секретов |
| ОС | systemd sandbox (NoNewPrivileges, ProtectSystem, MemoryDenyWriteExecute) |
| БД | Read-only PostgreSQL пользователь, параметризованные запросы |

### Защита от атак

| Угроза | Защита |
|--------|--------|
| SQL-инъекция | Нет raw SQL; только параметризованные запросы через whitelist полей |
| Path traversal | Whitelist абсолютных путей + `realpath()` проверка |
| Brute-force | Rate limiting + блокировка IP после 5 попыток |
| Утечка секретов | Автоматическая редакция паролей, ключей, токенов |
| Сканирование извне | Bind на 127.0.0.1 — порт не виден снаружи |

---

## Права доступа

### Файлы ServerLens

```bash
# Конфигурация
chown root:serverlens /etc/serverlens/config.yaml
chmod 640 /etc/serverlens/config.yaml

# Переменные окружения (пароли БД)
chown root:serverlens /etc/serverlens/env
chmod 640 /etc/serverlens/env

# Аудит-лог
chown serverlens:serverlens /var/log/serverlens/
chmod 750 /var/log/serverlens/
```

### SSH-пользователь

MCP-клиент подключается по SSH от имени обычного пользователя (например `rucode`). Этот пользователь **должен** быть в группе `serverlens`, чтобы читать конфиг и env-файл:

```bash
sudo usermod -aG serverlens rucode
```

### Доступ к логам

SSH-пользователь также должен быть в группах, которым принадлежат лог-файлы.

**Ubuntu / Debian** (логи обычно в группе `adm`):

```bash
sudo usermod -aG adm rucode            # /var/log/nginx/, /var/log/syslog
sudo usermod -aG postgres rucode       # /var/log/postgresql/
```

**CentOS / RHEL / Alma / Rocky** (логи принадлежат группам сервисов):

```bash
sudo usermod -aG nginx rucode          # /var/log/nginx/
sudo usermod -aG postgres rucode       # /var/log/postgresql/
```

**Определить нужную группу** для конкретного лог-файла:

```bash
stat -c '%G' /var/log/nginx/access.log
# adm      — на Ubuntu
# nginx    — на CentOS
```

> **Важно:** после `usermod` нужно перелогиниться (выйти и зайти заново по SSH), чтобы новые группы применились.
