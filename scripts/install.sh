#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Установочный скрипт (install.sh)
#
# Описание:
#   Полная установка ServerLens на сервер. Скрипт выполняет:
#     1. Проверку системных требований (PHP 8.1+, расширения json, mbstring)
#     2. Создание системного пользователя 'serverlens' (без login shell)
#     3. Создание директорий: /opt/serverlens, /etc/serverlens, /var/log/serverlens
#     4. Копирование файлов проекта (src/, bin/, composer.json) в /opt/serverlens
#     5. Установку PHP-зависимостей через Composer (--no-dev)
#     6. Интерактивный мастер настройки:
#        — автообнаружение сервисов (nginx, postgresql, mysql, redis и др.)
#        — сканирование логов и конфигурационных файлов
#        — генерация /etc/serverlens/config.yaml
#        — опционально: вызов setup_db.sh для настройки PostgreSQL
#     7. Установку systemd-сервиса (не запускает его)
#
# Запуск:
#   sudo bash scripts/install.sh              — с интерактивным мастером
#   sudo bash scripts/install.sh --no-wizard  — только установка, конфиг вручную
#
# Безопасность:
#   - Требует root-прав (проверяет id -u)
#   - При перезаписи config.yaml создаёт резервную копию (.bak.YYYYMMDDHHMMSS)
#   - НЕ удаляет и НЕ изменяет существующие конфигурации системных сервисов
#   - Все файлы конфигурации ServerLens получают права 640 (root:serverlens)
#   - Мастер настройки только ЧИТАЕТ обнаруженные логи/конфиги, ничего не изменяет
#   - Идемпотентен: повторный запуск безопасен (useradd проверяет существование)
#   - Composer устанавливается только при отсутствии (с официального источника)
#
# Что НЕ делает скрипт:
#   - Не удаляет никакие системные пакеты или пользователей
#   - Не изменяет настройки nginx, postgresql, mysql и других сервисов
#   - Не открывает сетевые порты и не меняет файрвол
#   - Не запускает сервис автоматически
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/opt/serverlens"
CONFIG_DIR="/etc/serverlens"
LOG_DIR="/var/log/serverlens"
SERVICE_USER="serverlens"
SCRIPT_DIR=""

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "  ${BLUE}▸${NC} $1"; }
ok()      { echo -e "  ${GREEN}✓${NC} $1"; }
warn()    { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()    { echo -e "  ${RED}✗${NC} $1"; exit 1; }

ask_yn() {
    local prompt="$1" default="${2:-y}" answer
    if [[ "$default" == "y" ]]; then
        read -rp "  $prompt [Y/n]: " answer
        [[ -z "$answer" || "${answer,,}" == "y" ]]
    else
        read -rp "  $prompt [y/N]: " answer
        [[ "${answer,,}" == "y" ]]
    fi
}

ask_input() {
    local prompt="$1" default="${2:-}" answer
    if [[ -n "$default" ]]; then
        read -rp "  $prompt [$default]: " answer
        echo "${answer:-$default}"
    else
        read -rp "  $prompt: " answer
        echo "$answer"
    fi
}

WIZARD=true
for arg in "$@"; do
    [[ "$arg" == "--no-wizard" ]] && WIZARD=false
done

# ═══════════════════════════════════════════════════
# Наборы известных сервисов, логов, конфигов
# ═══════════════════════════════════════════════════

ALL_SERVICES=(nginx apache2 postgresql mysql redis php-fpm docker rabbitmq)

declare -A SVC_UNITS
SVC_UNITS=(
    [nginx]="nginx"
    [apache2]="apache2 httpd"
    [postgresql]="postgresql"
    [mysql]="mysql mysqld mariadb"
    [redis]="redis-server redis"
    [php-fpm]=""
    [docker]="docker"
    [rabbitmq]="rabbitmq-server"
)

declare -A SVC_LOGS
SVC_LOGS=(
    [nginx]="/var/log/nginx/access.log:nginx_combined /var/log/nginx/error.log:plain"
    [apache2]="/var/log/apache2/access.log:plain /var/log/apache2/error.log:plain /var/log/httpd/access_log:plain /var/log/httpd/error_log:plain"
    [postgresql]="/var/log/postgresql:postgres"
    [mysql]="/var/log/mysql/error.log:plain"
    [redis]="/var/log/redis/redis-server.log:plain"
    [php-fpm]=""
    [docker]=""
    [rabbitmq]="/var/log/rabbitmq:plain"
)

declare -A SVC_CONFIGS
SVC_CONFIGS=(
    [nginx]="/etc/nginx/nginx.conf /etc/nginx/sites-enabled/ /etc/nginx/conf.d/"
    [apache2]="/etc/apache2/apache2.conf /etc/httpd/conf/httpd.conf"
    [postgresql]=""
    [mysql]="/etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/my.cnf"
    [redis]="/etc/redis/redis.conf"
    [php-fpm]=""
    [docker]="/etc/docker/daemon.json"
    [rabbitmq]="/etc/rabbitmq/rabbitmq.conf"
)

FOUND_SERVICES=()
SELECTED_SERVICES=()

LOG_YAML=""
CONFIG_YAML=""
SYSTEM_SVC_YAML=""
DOCKER_YAML=""

# ═══════════════════════════════════════════════════
# Обнаружение сервисов
# ═══════════════════════════════════════════════════

CACHED_UNITS=""

cache_systemctl() {
    CACHED_UNITS=$(systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null || true)
}

service_is_active() {
    echo "$CACHED_UNITS" | grep -qE "^\s*${1}\.service\s.*\sactive\s" 2>/dev/null
}

service_exists() {
    echo "$CACHED_UNITS" | grep -qF "${1}.service" 2>/dev/null
}

discover_services() {
    cache_systemctl

    for svc in "${ALL_SERVICES[@]}"; do
        local found=false

        case "$svc" in
            php-fpm)
                local unit
                unit=$(echo "$CACHED_UNITS" | grep -oP 'php[0-9.]+-fpm(?=\.service)' | head -1 || true)
                if [[ -n "$unit" ]]; then
                    SVC_UNITS[php-fpm]="$unit"
                    found=true
                fi
                ;;
            postgresql)
                if echo "$CACHED_UNITS" | grep -qE 'postgresql' 2>/dev/null; then
                    found=true
                fi
                ;;
            *)
                for unit in ${SVC_UNITS[$svc]}; do
                    if service_exists "$unit"; then
                        found=true
                        break
                    fi
                done
                ;;
        esac

        if $found; then FOUND_SERVICES+=("$svc"); fi
    done
}

discover_pg_version_paths() {
    if [[ -d /etc/postgresql ]]; then
        for d in /etc/postgresql/*/main; do
            [[ -d "$d" ]] && echo "$d"
        done
    fi
}

discover_phpfpm_paths() {
    local paths=""
    for f in /var/log/php*-fpm.log /var/log/php/*/fpm.log; do
        [[ -f "$f" ]] && paths+="$f:plain "
    done
    SVC_LOGS[php-fpm]="$paths"

    local cpath=""
    for f in /etc/php/*/fpm/php-fpm.conf /etc/php/*/fpm/pool.d/www.conf; do
        [[ -f "$f" ]] && cpath+="$f "
    done
    SVC_CONFIGS[php-fpm]="$cpath"
}

# ═══════════════════════════════════════════════════
# Визард — логи
# ═══════════════════════════════════════════════════

make_log_name() {
    local path="$1" svc="$2"
    local base
    base=$(basename "$path" .log)
    base="${base//[^a-zA-Z0-9_]/_}"
    echo "${svc}_${base}"
}

wizard_logs() {
    echo -e "\n${BOLD}  ── Лог-файлы ──${NC}\n"

    local idx=1
    declare -a found_logs=() found_formats=() found_names=()

    for svc in "${SELECTED_SERVICES[@]}"; do
        [[ "$svc" == "php-fpm" ]] && discover_phpfpm_paths

        if [[ "$svc" == "postgresql" ]]; then
            while IFS= read -r d; do
                [[ -z "$d" ]] && continue
                local ver
                ver=$(basename "$(dirname "$d")")
                for f in /var/log/postgresql/postgresql-"${ver}"-main.log; do
                    if [[ -f "$f" ]]; then
                        found_logs+=("$f"); found_formats+=("postgres"); found_names+=("postgresql_${ver}")
                        info "[${idx}] $f (postgres)"
                        ((idx++))
                    fi
                done
            done < <(discover_pg_version_paths)
            continue
        fi

        for entry in ${SVC_LOGS[$svc]:-}; do
            local path="${entry%%:*}" fmt="${entry##*:}"
            if [[ -d "$path" ]]; then
                for f in "$path"/*.log; do
                    [[ -f "$f" ]] || continue
                    found_logs+=("$f"); found_formats+=("$fmt"); found_names+=("$(make_log_name "$f" "$svc")")
                    local sz; sz=$(du -sh "$f" 2>/dev/null | cut -f1)
                    info "[${idx}] $f ($fmt, ${sz})"
                    ((idx++))
                done
            elif [[ -f "$path" ]]; then
                found_logs+=("$path"); found_formats+=("$fmt"); found_names+=("$(make_log_name "$path" "$svc")")
                local sz; sz=$(du -sh "$path" 2>/dev/null | cut -f1)
                info "[${idx}] $path ($fmt, ${sz})"
                ((idx++))
            fi
        done
    done

    for syslog in /var/log/syslog /var/log/messages /var/log/auth.log; do
        if [[ -f "$syslog" ]]; then
            found_logs+=("$syslog"); found_formats+=("plain"); found_names+=("$(basename "$syslog" .log)")
            local sz; sz=$(du -sh "$syslog" 2>/dev/null | cut -f1)
            info "[${idx}] $syslog (plain, ${sz})"
            ((idx++))
        fi
    done

    if (( ${#found_logs[@]} == 0 )); then
        warn "Лог-файлы не обнаружены"
    else
        echo ""
        if ask_yn "Включить все найденные логи?" "y"; then
            for i in "${!found_logs[@]}"; do
                append_log "${found_names[$i]}" "${found_logs[$i]}" "${found_formats[$i]}"
            done
        else
            local sel
            sel=$(ask_input "Номера через запятую (например 1,2,4)")
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#found_logs[@]} )); then append_log "${found_names[$n]}" "${found_logs[$n]}" "${found_formats[$n]}"; fi
            done
        fi
    fi

    echo ""
    while ask_yn "Добавить свой путь к логу?" "n"; do
        local cpath cname cfmt
        cpath=$(ask_input "Путь к файлу лога")
        [[ ! -f "$cpath" ]] && { warn "Файл не найден: $cpath"; continue; }
        cname=$(ask_input "Имя источника" "$(make_log_name "$cpath" "custom")")
        cfmt=$(ask_input "Формат (plain/json/nginx_combined/postgres)" "plain")
        append_log "$cname" "$cpath" "$cfmt"
        ok "Добавлен: $cname"
    done
}

append_log() {
    LOG_YAML+="    - name: \"$1\"\n      path: \"$2\"\n      format: \"$3\"\n      max_lines: 5000\n\n"
}

# ═══════════════════════════════════════════════════
# Визард — конфиги
# ═══════════════════════════════════════════════════

wizard_configs() {
    echo -e "\n${BOLD}  ── Конфигурационные файлы ──${NC}\n"

    local idx=1
    declare -a found_cfgs=() found_cfg_names=() found_cfg_redacts=()

    for svc in "${SELECTED_SERVICES[@]}"; do
        if [[ "$svc" == "postgresql" ]]; then
            while IFS= read -r d; do
                [[ -z "$d" ]] && continue
                local ver; ver=$(basename "$(dirname "$d")")
                for f in "$d"/postgresql.conf "$d"/pg_hba.conf; do
                    [[ -f "$f" ]] || continue
                    local bname; bname=$(basename "$f" .conf)
                    found_cfgs+=("$f"); found_cfg_names+=("postgres_${ver}_${bname}")
                    [[ "$bname" == "postgresql" ]] && found_cfg_redacts+=("password ssl_key_file ssl_cert_file") || found_cfg_redacts+=("")
                    info "[${idx}] $f"; ((idx++))
                done
            done < <(discover_pg_version_paths)
            continue
        fi

        for cpath in ${SVC_CONFIGS[$svc]:-}; do
            if [[ -d "$cpath" ]]; then
                found_cfgs+=("$cpath")
                found_cfg_names+=("${svc}_$(basename "$cpath")")
                found_cfg_redacts+=("")
                local cnt; cnt=$(find "$cpath" -maxdepth 1 -type f 2>/dev/null | wc -l)
                info "[${idx}] $cpath (директория, ${cnt} файлов)"; ((idx++))
            elif [[ -f "$cpath" ]]; then
                found_cfgs+=("$cpath")
                local bname; bname=$(basename "$cpath" .conf); bname="${bname//[^a-zA-Z0-9_]/_}"
                found_cfg_names+=("${svc}_${bname}")
                local redact=""
                [[ "$svc" == "redis" ]] && redact="requirepass masterauth"
                [[ "$svc" == "mysql" ]] && redact="password"
                found_cfg_redacts+=("$redact")
                info "[${idx}] $cpath"; ((idx++))
            fi
        done
    done

    if (( ${#found_cfgs[@]} == 0 )); then
        warn "Конфигурационные файлы не обнаружены"
    else
        echo ""
        if ask_yn "Включить все найденные конфиги?" "y"; then
            for i in "${!found_cfgs[@]}"; do
                append_config "${found_cfg_names[$i]}" "${found_cfgs[$i]}" "${found_cfg_redacts[$i]}"
            done
        else
            local sel; sel=$(ask_input "Номера через запятую")
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#found_cfgs[@]} )); then append_config "${found_cfg_names[$n]}" "${found_cfgs[$n]}" "${found_cfg_redacts[$n]}"; fi
            done
        fi
    fi

    echo ""
    while ask_yn "Добавить свой путь к конфигу?" "n"; do
        local cpath cname
        cpath=$(ask_input "Путь к файлу/директории")
        [[ ! -e "$cpath" ]] && { warn "Путь не найден: $cpath"; continue; }
        cname=$(ask_input "Имя источника" "custom_$(basename "$cpath" | sed 's/[^a-zA-Z0-9_]/_/g')")
        append_config "$cname" "$cpath" ""
        ok "Добавлен: $cname"
    done
}

append_config() {
    local name="$1" path="$2" redact="$3"
    CONFIG_YAML+="    - name: \"${name}\"\n      path: \"${path}\"\n"
    [[ -d "$path" ]] && CONFIG_YAML+="      type: \"directory\"\n"
    if [[ -n "$redact" ]]; then
        CONFIG_YAML+="      redact:\n"
        for r in $redact; do CONFIG_YAML+="        - \"${r}\"\n"; done
    else
        CONFIG_YAML+="      redact: []\n"
    fi
    CONFIG_YAML+="\n"
}

# ═══════════════════════════════════════════════════
# Визард — системный мониторинг
# ═══════════════════════════════════════════════════

wizard_system() {
    echo -e "\n${BOLD}  ── Системный мониторинг ──${NC}\n"

    if ! ask_yn "Включить мониторинг системы (диск, память, сервисы)?" "y"; then
        return
    fi

    for svc in "${SELECTED_SERVICES[@]}"; do
        local unit="${SVC_UNITS[$svc]%% *}"
        [[ -z "$unit" ]] && continue
        SYSTEM_SVC_YAML+="    - \"${unit}\"\n"
    done

    if [[ " ${SELECTED_SERVICES[*]} " == *" docker "* ]]; then
        echo ""
        local stacks
        stacks=$(docker stack ls --format '{{.Name}}' 2>/dev/null || docker compose ls --format '{{.Name}}' 2>/dev/null || true)
        if [[ -n "$stacks" ]]; then
            info "Найденные Docker-стеки:"
            local didx=1
            declare -a stack_list=()
            while IFS= read -r s; do
                [[ -z "$s" ]] && continue
                info "  [${didx}] $s"; stack_list+=("$s"); ((didx++))
            done <<< "$stacks"
            echo ""
            if ask_yn "Мониторить все стеки?" "y"; then
                for s in "${stack_list[@]}"; do DOCKER_YAML+="    - \"${s}\"\n"; done
            fi
        fi
    fi
}

# ═══════════════════════════════════════════════════
# Генерация config.yaml (без секции databases)
# ═══════════════════════════════════════════════════

generate_config() {
    echo -e "\n${BOLD}  ── Генерация config.yaml ──${NC}\n"

    local cfg="${CONFIG_DIR}/config.yaml"

    if [[ -f "$cfg" ]]; then
        warn "Конфиг ${cfg} уже существует"
        if ! ask_yn "Перезаписать? (резервная копия будет создана)" "n"; then
            ok "Конфиг не изменён"
            return
        fi
        local backup="${cfg}.bak.$(date +%Y%m%d%H%M%S)"
        cp "$cfg" "$backup"
        ok "Резервная копия: ${backup}"
    fi

    cat > "$cfg" <<'HEADER'
# ServerLens Configuration
# Generated by install.sh

server:
  host: "127.0.0.1"
  port: 9600
  transport: "stdio"

auth:
  tokens: []
  max_failed_attempts: 5
  lockout_minutes: 15

rate_limiting:
  requests_per_minute: 60
  max_concurrent: 5

audit:
  enabled: true
  path: "/var/log/serverlens/audit.log"
  log_params: false
  retention_days: 90

HEADER

    if [[ -n "$LOG_YAML" ]]; then
        { echo "logs:"; echo "  sources:"; echo -e "$LOG_YAML"; } >> "$cfg"
    else
        { echo "logs:"; echo "  sources: []"; echo ""; } >> "$cfg"
    fi

    if [[ -n "$CONFIG_YAML" ]]; then
        { echo "configs:"; echo "  sources:"; echo -e "$CONFIG_YAML"; } >> "$cfg"
    else
        { echo "configs:"; echo "  sources: []"; echo ""; } >> "$cfg"
    fi

    { echo "databases:"; echo "  connections: []"; echo ""; } >> "$cfg"

    local sys_enabled="false"
    [[ -n "$SYSTEM_SVC_YAML" || -n "$DOCKER_YAML" ]] && sys_enabled="true"
    echo "system:" >> "$cfg"
    echo "  enabled: ${sys_enabled}" >> "$cfg"
    [[ -n "$SYSTEM_SVC_YAML" ]] && { echo "  allowed_services:"; echo -e "$SYSTEM_SVC_YAML"; } >> "$cfg" || echo "  allowed_services: []" >> "$cfg"
    [[ -n "$DOCKER_YAML" ]] && { echo "  allowed_docker_stacks:"; echo -e "$DOCKER_YAML"; } >> "$cfg" || echo "  allowed_docker_stacks: []" >> "$cfg"

    chown root:${SERVICE_USER} "$cfg" 2>/dev/null || true
    chmod 640 "$cfg"

    ok "Конфигурация записана: ${cfg}"
}

# ═══════════════════════════════════════════════════
# Фазы установки
# ═══════════════════════════════════════════════════

phase_checks() {
    echo -e "\n${BOLD}[1/7] Проверка системы${NC}"

    [[ "$(id -u)" -ne 0 ]] && fail "Запустите от root: sudo bash scripts/install.sh"

    command -v php &>/dev/null || fail "PHP не найден. Установите PHP 8.1+"

    local php_major php_minor
    php_major=$(php -r 'echo PHP_MAJOR_VERSION;')
    php_minor=$(php -r 'echo PHP_MINOR_VERSION;')

    if (( php_major < 8 || (php_major == 8 && php_minor < 1) )); then fail "Требуется PHP 8.1+, найден ${php_major}.${php_minor}"; fi
    ok "PHP ${php_major}.${php_minor}"

    for ext in json mbstring; do
        if php -r "if(!extension_loaded('${ext}')) exit(1);" 2>/dev/null; then
            ok "ext-${ext}"
        else
            fail "Отсутствует расширение ${ext}"
        fi
    done

    if php -r "if(!extension_loaded('pdo_pgsql')) exit(1);" 2>/dev/null; then
        ok "ext-pdo_pgsql"
    else
        warn "ext-pdo_pgsql не найден (нужен только для модуля БД)"
    fi
}

phase_create_user() {
    echo -e "\n${BOLD}[2/7] Системный пользователь${NC}"
    if ! id "${SERVICE_USER}" &>/dev/null; then
        useradd -r -s /usr/sbin/nologin -d "${INSTALL_DIR}" "${SERVICE_USER}"
        ok "Пользователь '${SERVICE_USER}' создан"
    else
        ok "Пользователь '${SERVICE_USER}' уже существует"
    fi
}

phase_directories() {
    echo -e "\n${BOLD}[3/7] Директории${NC}"
    mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}" "${LOG_DIR}"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${LOG_DIR}"
    chmod 750 "${LOG_DIR}"
    ok "${INSTALL_DIR}, ${CONFIG_DIR}, ${LOG_DIR}"
}

phase_copy_files() {
    echo -e "\n${BOLD}[4/7] Копирование файлов${NC}"
    cp -r "${SCRIPT_DIR}/src" "${INSTALL_DIR}/"
    cp -r "${SCRIPT_DIR}/bin" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/composer.json" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/composer.lock" "${INSTALL_DIR}/" 2>/dev/null || true
    chmod +x "${INSTALL_DIR}/bin/serverlens"
    ok "Файлы скопированы в ${INSTALL_DIR}"
}

phase_dependencies() {
    echo -e "\n${BOLD}[5/7] PHP-зависимости${NC}"
    if ! command -v composer &>/dev/null; then
        info "Устанавливаю Composer..."
        curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null
    fi
    ok "Composer найден"
    cd "${INSTALL_DIR}"
    composer install --no-dev --optimize-autoloader --no-interaction --quiet
    ok "Зависимости установлены"
}

phase_wizard() {
    echo -e "\n${BOLD}[6/7] Мастер настройки${NC}"

    echo -e "\n  Сканирование сервисов...\n"
    discover_services

    if (( ${#FOUND_SERVICES[@]} == 0 )); then
        warn "Сервисы не обнаружены"
    else
        local idx=1
        for svc in "${FOUND_SERVICES[@]}"; do
            local unit="${SVC_UNITS[$svc]%% *}"
            local status
            service_is_active "${unit:-$svc}" 2>/dev/null && status="${GREEN}active${NC}" || status="${YELLOW}installed${NC}"
            echo -e "  [${idx}] ${svc} (${status})"
            ((idx++))
        done

        echo ""
        local sel; sel=$(ask_input "Какие сервисы мониторить? (номера, 'all' или пустое)" "all")
        if [[ "$sel" == "all" ]]; then
            SELECTED_SERVICES=("${FOUND_SERVICES[@]}")
        elif [[ -n "$sel" ]]; then
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#FOUND_SERVICES[@]} )); then SELECTED_SERVICES+=("${FOUND_SERVICES[$n]}"); fi
            done
        fi
    fi

    if (( ${#SELECTED_SERVICES[@]} > 0 )); then ok "Выбрано: ${SELECTED_SERVICES[*]}"; fi

    wizard_logs
    wizard_configs
    wizard_system
    generate_config

    # Настройка БД — вызываем отдельный скрипт
    echo ""
    if ask_yn "Настроить подключение к PostgreSQL?" "y"; then
        local db_script="${SCRIPT_DIR}/scripts/setup_db.sh"
        if [[ -f "$db_script" ]]; then
            bash "$db_script"
        else
            warn "Скрипт ${db_script} не найден, пропускаем"
            warn "Запустите позже: sudo bash scripts/setup_db.sh"
        fi
    fi
}

phase_default_config() {
    echo -e "\n${BOLD}[6/7] Конфигурация (без визарда)${NC}"
    local cfg="${CONFIG_DIR}/config.yaml"
    if [[ -f "$cfg" ]]; then
        ok "Конфиг уже существует: ${cfg}"
    else
        cp "${SCRIPT_DIR}/config.example.yaml" "$cfg"
        chown root:${SERVICE_USER} "$cfg" 2>/dev/null || true
        chmod 640 "$cfg"
        ok "Скопирован config.example.yaml → ${cfg}"
        warn "Отредактируйте вручную: sudo nano ${cfg}"
    fi
}

phase_systemd() {
    echo -e "\n${BOLD}[7/7] Systemd${NC}"
    if [[ -f "${SCRIPT_DIR}/etc/serverlens.service" ]]; then
        cp "${SCRIPT_DIR}/etc/serverlens.service" /etc/systemd/system/serverlens.service
        systemctl daemon-reload
        ok "Сервис установлен (не запущен)"
        info "Запуск нужен только для SSE-режима, для SSH+stdio — не нужен"
    else
        warn "serverlens.service не найден, пропуск"
    fi
}

phase_summary() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║         Установка завершена!             ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Конфигурация: ${CYAN}${CONFIG_DIR}/config.yaml${NC}"
    echo -e "  Программа:    ${CYAN}${INSTALL_DIR}/bin/serverlens${NC}"
    echo -e "  Логи аудита:  ${CYAN}${LOG_DIR}/audit.log${NC}"
    echo ""
    echo -e "  ${BOLD}Проверка:${NC}"
    echo "    php ${INSTALL_DIR}/bin/serverlens validate-config \\"
    echo "      --config ${CONFIG_DIR}/config.yaml"
    echo ""
    echo -e "  ${BOLD}Быстрый тест:${NC}"
    echo "    echo '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"test\",\"version\":\"1.0\"}}}' | \\"
    echo "      php ${INSTALL_DIR}/bin/serverlens serve --config ${CONFIG_DIR}/config.yaml --stdio"
    echo ""

    # SSH-пользователь и группы
    echo -e "  ${BOLD}Права SSH-пользователя:${NC}"
    echo -e "  MCP-клиент подключается по SSH. SSH-пользователь должен быть в нужных группах."
    echo -e "  Замените ${CYAN}ВАШUSER${NC} на имя SSH-пользователя (тот, под которым заходите на сервер):"
    echo ""
    echo "    sudo usermod -aG serverlens ВАШUSER   # доступ к конфигу ServerLens"

    declare -A shown_groups=()
    if (( ${#SELECTED_SERVICES[@]} > 0 )); then
        for svc in "${SELECTED_SERVICES[@]}"; do
            local log_dir=""
            case "$svc" in
                nginx)      log_dir="/var/log/nginx" ;;
                apache2)    log_dir="/var/log/apache2"; [[ ! -d "$log_dir" ]] && log_dir="/var/log/httpd" ;;
                postgresql) log_dir="/var/log/postgresql" ;;
                redis)      log_dir="/var/log/redis" ;;
                rabbitmq)   log_dir="/var/log/rabbitmq" ;;
            esac
            if [[ -n "$log_dir" && -d "$log_dir" ]]; then
                local grp
                grp=$(stat -c '%G' "$log_dir" 2>/dev/null || stat -f '%Sg' "$log_dir" 2>/dev/null || echo "")
                if [[ -n "$grp" && "$grp" != "root" && -z "${shown_groups[$grp]:-}" ]]; then
                    echo "    sudo usermod -aG ${grp} ВАШUSER           # ${log_dir}/"
                    shown_groups[$grp]=1
                elif [[ "$grp" == "root" ]]; then
                    echo "    sudo setfacl -R -m u:ВАШUSER:rX ${log_dir}/"
                fi
            fi
        done
    fi
    echo ""
    echo -e "  ${YELLOW}⚠${NC} После usermod нужно перелогиниться (выйти и зайти по SSH заново)"
    echo ""

    echo -e "  ${BOLD}Далее:${NC} настройте MCP-клиент на машине разработчика"
    echo -e "  См. docs/quickstart.md (шаги 4–6)"
    echo ""
}

# ═══════════════════════════════════════════════════

main() {
    SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

    echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║     ServerLens Installer v1.0             ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

    phase_checks
    phase_create_user
    phase_directories
    phase_copy_files
    phase_dependencies

    if $WIZARD; then
        phase_wizard
    else
        phase_default_config
    fi

    phase_systemd
    phase_summary
}

main "$@"
