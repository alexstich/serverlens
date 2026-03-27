#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Интерактивная настройка PostgreSQL (setup_db.sh)
#
# Описание:
#   Настраивает read-only доступ ServerLens к базам данных PostgreSQL.
#   Скрипт выполняет:
#     1. Подключение к PostgreSQL (peer auth или по паролю суперпользователя)
#     2. Вывод списка баз данных с размерами и числом таблиц
#     3. Создание read-only пользователя (по умолчанию serverlens_readonly):
#        — default_transaction_read_only = on (запрет записи)
#        — statement_timeout = 30s (защита от тяжёлых запросов)
#     4. Для каждой выбранной БД:
#        — GRANT CONNECT, USAGE ON SCHEMA public
#        — Показ таблиц с автоматическим определением чувствительных колонок
#        — GRANT SELECT только на выбранные таблицы
#     5. Генерацию YAML-секции databases для config.yaml
#     6. Запись пароля в /etc/serverlens/env (SL_DB_PASS=...)
#     7. Обновление секции databases в config.yaml (с бэкапом)
#
# Запуск:
#   sudo bash scripts/setup_db.sh
#   (вызывается также из install.sh в рамках мастера настройки)
#
# Идемпотентен — безопасно запускать повторно:
#   - CREATE USER проверяет pg_roles перед созданием
#   - ALTER USER / GRANT идемпотентны в PostgreSQL
#   - config.yaml обновляется через замену секции databases (с бэкапом)
#   - env-файл обновляется через замену строки SL_DB_PASS
#
# Безопасность:
#   - Создаёт пользователя ТОЛЬКО с правами SELECT (read-only)
#   - Пароли экранируются перед использованием в SQL и sed
#   - При обновлении config.yaml создаёт резервную копию (.bak.YYYYMMDDHHMMSS)
#   - Чувствительные колонки (password, token, secret и т.д.) скрываются автоматически
#   - Env-файл получает права 640 (root:serverlens)
#   - PGPASSWORD экспортируется только в текущий процесс
#
# Что НЕ делает скрипт:
#   - Не удаляет базы данных, таблицы или существующих пользователей
#   - Не изменяет данные в таблицах
#   - Не меняет pg_hba.conf или postgresql.conf
#   - Не перезапускает PostgreSQL
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

CONFIG_DIR="/etc/serverlens"

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

escape_sql_password() {
    printf '%s' "${1//\'/\'\'}"
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[&\\/|]/\\&/g'
}

SENSITIVE_PATTERNS="password passwd pass_hash secret api_key apikey token reset_token remember_token private_key credit_card ssn pin_code otp mfa two_factor session_id auth_code refresh_token access_token"

is_sensitive_column() {
    local col="${1,,}"
    for p in $SENSITIVE_PATTERNS; do
        [[ "$col" == *"$p"* ]] && return 0
    done
    return 1
}

is_filterable_column() {
    local col="${1,,}"
    [[ "$col" == "id" || "$col" == *"_id" || "$col" == *"_at" ||
       "$col" == "status" || "$col" == "is_active" || "$col" == "type" ||
       "$col" == "email" || "$col" == "name" || "$col" == "role" ]] && return 0
    return 1
}

# ── Подключение ──

PG_CMD=""
PG_HOST="localhost"
PG_PORT="5432"

setup_connection() {
    echo -e "\n${BOLD}  Подключение к PostgreSQL${NC}\n"

    if [[ "$(id -u)" -eq 0 ]] && sudo -u postgres psql -t -A -c "SELECT 1" &>/dev/null 2>&1; then
        PG_CMD="sudo -u postgres psql"
        ok "Подключено через sudo -u postgres (peer auth)"
        return
    fi

    warn "Peer-подключение не работает, нужны параметры:"
    PG_HOST=$(ask_input "Хост" "localhost")
    PG_PORT=$(ask_input "Порт" "5432")
    local pg_user; pg_user=$(ask_input "Суперпользователь" "postgres")

    echo -n "  Пароль суперпользователя: "
    read -rs pg_pass
    echo ""

    export PGPASSWORD="$pg_pass"
    PG_CMD="psql -h ${PG_HOST} -p ${PG_PORT} -U ${pg_user}"

    if ! $PG_CMD -t -A -c "SELECT 1" &>/dev/null 2>&1; then
        fail "Не удалось подключиться к PostgreSQL"
    fi
    ok "Подключено к ${PG_HOST}:${PG_PORT}"
}

pg_exec() {
    $PG_CMD -t -A -c "$1" 2>/dev/null || true
}

pg_exec_db() {
    $PG_CMD -t -A -d "$1" -c "$2" 2>/dev/null || true
}

# ── Выбор БД ──

declare -a SELECTED_DBS=()

select_databases() {
    echo -e "\n${BOLD}  Доступные базы данных${NC}\n"

    local databases
    databases=$(pg_exec "SELECT datname FROM pg_database WHERE datistemplate = false AND datname != 'postgres' ORDER BY datname;")

    if [[ -z "$databases" ]]; then
        fail "Базы данных не найдены"
    fi

    local idx=1
    declare -a db_list=()
    while IFS= read -r dbname; do
        [[ -z "$dbname" ]] && continue
        local sz; sz=$(pg_exec "SELECT pg_size_pretty(pg_database_size('${dbname}'));")
        local cnt; cnt=$(pg_exec_db "$dbname" "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
        info "[${idx}] ${dbname}  (${sz:-?}, ${cnt:-?} таблиц)"
        db_list+=("$dbname")
        ((idx++))
    done <<< "$databases"

    echo ""
    local sel; sel=$(ask_input "Какие БД мониторить? (номера через запятую или 'all')")

    if [[ "$sel" == "all" ]]; then
        SELECTED_DBS=("${db_list[@]}")
    else
        IFS=',' read -ra nums <<< "$sel"
        for n in "${nums[@]}"; do
            n=$(( ${n// /} - 1 ))
            if (( n >= 0 && n < ${#db_list[@]} )); then SELECTED_DBS+=("${db_list[$n]}"); fi
        done
    fi

    if (( ${#SELECTED_DBS[@]} == 0 )); then fail "Ни одна БД не выбрана"; fi
    ok "Выбрано: ${SELECTED_DBS[*]}"
}

# ── Создание пользователя (идемпотентно) ──

DB_USER=""
DB_PASS=""

create_readonly_user() {
    echo -e "\n${BOLD}  Read-only пользователь${NC}\n"

    DB_USER=$(ask_input "Имя пользователя" "serverlens_readonly")

    local user_exists
    user_exists=$(pg_exec "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}';")

    if [[ "$user_exists" == "1" ]]; then
        ok "Пользователь '${DB_USER}' уже существует"

        local env_file="${CONFIG_DIR}/env"
        local existing_pass=""
        if [[ -f "$env_file" ]]; then
            existing_pass=$(grep "^SL_DB_PASS=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
        fi

        if ask_yn "Обновить пароль?" "n"; then
            DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
            pg_exec "ALTER USER \"${DB_USER}\" WITH PASSWORD '$(escape_sql_password "$DB_PASS")';"
            ok "Пароль обновлён"
        elif [[ -n "$existing_pass" ]]; then
            DB_PASS="$existing_pass"
            ok "Пароль взят из ${env_file}"
        else
            warn "Пароль не найден в ${env_file}"
            info "После завершения запишите его вручную: SL_DB_PASS=... в ${env_file}"
        fi
        pg_exec "ALTER USER \"${DB_USER}\" SET default_transaction_read_only = on;"
        pg_exec "ALTER USER \"${DB_USER}\" SET statement_timeout = '30s';"
        ok "Настройки пользователя подтверждены"
    else
        if ask_yn "Сгенерировать пароль?" "y"; then
            DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
            echo -e "  ${CYAN}Пароль: ${DB_PASS}${NC}"
        else
            echo -n "  Введите пароль: "
            read -rs DB_PASS
            echo ""
        fi

        pg_exec "CREATE USER \"${DB_USER}\" WITH PASSWORD '$(escape_sql_password "$DB_PASS")';"
        pg_exec "ALTER USER \"${DB_USER}\" SET default_transaction_read_only = on;"
        pg_exec "ALTER USER \"${DB_USER}\" SET statement_timeout = '30s';"
        ok "Пользователь '${DB_USER}' создан"
    fi
}

# ── Настройка таблиц (идемпотентно — GRANT идемпотентен в PG) ──

YAML_OUTPUT=""

configure_tables() {
    for dbname in "${SELECTED_DBS[@]}"; do
        echo -e "\n${BOLD}${CYAN}  ══ БД: ${dbname} ══${NC}\n"

        pg_exec "GRANT CONNECT ON DATABASE \"${dbname}\" TO \"${DB_USER}\";"
        pg_exec_db "$dbname" "GRANT USAGE ON SCHEMA public TO \"${DB_USER}\";"

        local tables
        tables=$(pg_exec_db "$dbname" "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;")

        if [[ -z "$tables" ]]; then
            warn "Нет таблиц в БД ${dbname}"
            continue
        fi

        local tidx=1
        declare -a table_list=()
        while IFS= read -r tname; do
            [[ -z "$tname" ]] && continue
            local cnt; cnt=$(pg_exec_db "$dbname" "SELECT count(*) FROM \"${tname}\";" 2>/dev/null || echo "?")
            local cols; cols=$(pg_exec_db "$dbname" "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='${tname}';")
            info "[${tidx}] ${tname}  (${cnt:-?} строк, ${cols:-?} колонок)"
            table_list+=("$tname")
            ((tidx++))
        done <<< "$tables"

        echo ""
        local tsel; tsel=$(ask_input "Таблицы для мониторинга? (номера, 'all' или пустое)")
        [[ -z "$tsel" ]] && continue

        declare -a selected_tables=()
        if [[ "$tsel" == "all" ]]; then
            selected_tables=("${table_list[@]}")
        else
            IFS=',' read -ra tnums <<< "$tsel"
            for n in "${tnums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#table_list[@]} )); then selected_tables+=("${table_list[$n]}"); fi
            done
        fi

        if (( ${#selected_tables[@]} == 0 )); then continue; fi

        for t in "${selected_tables[@]}"; do
            pg_exec_db "$dbname" "GRANT SELECT ON \"${t}\" TO \"${DB_USER}\";"
        done

        YAML_OUTPUT+="    - name: \"${dbname}\"\n"
        YAML_OUTPUT+="      host: \"${PG_HOST}\"\n"
        YAML_OUTPUT+="      port: ${PG_PORT}\n"
        YAML_OUTPUT+="      database: \"${dbname}\"\n"
        YAML_OUTPUT+="      user: \"${DB_USER}\"\n"
        YAML_OUTPUT+="      password_env: \"SL_DB_PASS\"\n"
        YAML_OUTPUT+="      tables:\n"

        echo ""
        local max_rows; max_rows=$(ask_input "Max rows для всех таблиц" "500")

        local total_denied=0

        for tname in "${selected_tables[@]}"; do
            local columns
            columns=$(pg_exec_db "$dbname" "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='${tname}' ORDER BY ordinal_position;")

            declare -a allowed=() denied=() filters=() order_by=()

            while IFS= read -r col; do
                [[ -z "$col" ]] && continue
                if is_sensitive_column "$col"; then
                    denied+=("$col")
                else
                    allowed+=("$col")
                fi
                is_filterable_column "$col" && { filters+=("$col"); order_by+=("$col"); }
            done <<< "$columns"

            local denied_info=""
            if (( ${#denied[@]} > 0 )); then
                denied_info=" ${RED}скрыто: ${denied[*]}${NC}"
                ((total_denied += ${#denied[@]})) || true
            fi
            echo -e "    ${GREEN}✓${NC} ${tname} (${#allowed[@]} открыто, ${#denied[@]} скрыто)${denied_info}"

            YAML_OUTPUT+="        - name: \"${tname}\"\n"
            YAML_OUTPUT+="          allowed_fields: [$(printf '"%s", ' "${allowed[@]}" | sed 's/, $//')]"
            YAML_OUTPUT+="\n"
            if (( ${#denied[@]} > 0 )); then
                YAML_OUTPUT+="          denied_fields: [$(printf '"%s", ' "${denied[@]}" | sed 's/, $//')]"
            else
                YAML_OUTPUT+="          denied_fields: []"
            fi
            YAML_OUTPUT+="\n"
            YAML_OUTPUT+="          max_rows: ${max_rows}\n"
            if (( ${#filters[@]} > 0 )); then
                YAML_OUTPUT+="          allowed_filters: [$(printf '"%s", ' "${filters[@]}" | sed 's/, $//')]"
            else
                YAML_OUTPUT+="          allowed_filters: []"
            fi
            YAML_OUTPUT+="\n"
            if (( ${#order_by[@]} > 0 )); then
                YAML_OUTPUT+="          allowed_order_by: [$(printf '"%s", ' "${order_by[@]}" | sed 's/, $//')]"
            else
                YAML_OUTPUT+="          allowed_order_by: []"
            fi
            YAML_OUTPUT+="\n\n"
        done

        echo ""
        ok "БД ${dbname}: ${#selected_tables[@]} таблиц, ${total_denied} скрытых колонок"
        if (( total_denied > 0 )); then
            info "Скрытые колонки определены автоматически (password, token, secret и т.д.)"
            info "Отредактируйте denied_fields в config.yaml при необходимости"
        fi
    done
}

# ── Запись результатов (идемпотентно) ──

save_env() {
    local env_file="${CONFIG_DIR}/env"
    mkdir -p "${CONFIG_DIR}"

    if [[ -f "$env_file" ]] && grep -q "^SL_DB_PASS=" "$env_file" 2>/dev/null; then
        local escaped; escaped=$(escape_sed_replacement "$DB_PASS")
        sed -i "s|^SL_DB_PASS=.*|SL_DB_PASS=${escaped}|" "$env_file"
        ok "Пароль обновлён в ${env_file}"
    else
        printf 'SL_DB_PASS=%s\n' "$DB_PASS" >> "$env_file"
        ok "Пароль записан в ${env_file}"
    fi
    chmod 640 "$env_file" 2>/dev/null || true
    chown root:serverlens "$env_file" 2>/dev/null || true
}

update_config_yaml() {
    local cfg="${CONFIG_DIR}/config.yaml"

    if [[ ! -f "$cfg" ]]; then
        warn "Файл ${cfg} не найден — вставьте YAML вручную"
        return
    fi

    local backup="${cfg}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$cfg" "$backup"

    local tmp; tmp=$(mktemp)

    # Удаляем старую секцию databases (от databases: до следующего top-level ключа или EOF)
    awk '
        BEGIN { skip = 0 }
        /^databases:/ { skip = 1; next }
        skip && /^[a-zA-Z]/ { skip = 0 }
        !skip { print }
    ' "$cfg" > "$tmp"

    # Дописываем новую секцию
    {
        echo "databases:"
        echo "  connections:"
        echo -e "$YAML_OUTPUT"
    } >> "$tmp"

    mv "$tmp" "$cfg"
    chmod 640 "$cfg" 2>/dev/null || true
    chown root:serverlens "$cfg" 2>/dev/null || true

    ok "config.yaml обновлён (бэкап: ${backup})"
}

print_result() {
    echo -e "\n${BOLD}══════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Результат${NC}\n"

    echo -e "  ${BOLD}Сгенерированный YAML:${NC}\n"
    echo -e "${CYAN}databases:"
    echo -e "  connections:"
    echo -e "${YAML_OUTPUT}${NC}"

    echo ""
    if [[ -n "$DB_PASS" ]]; then
        if ask_yn "Записать пароль в ${CONFIG_DIR}/env?" "y"; then
            save_env
        else
            echo -e "\n  ${BOLD}Пароль:${NC} ${DB_PASS}"
            echo -e "  Запишите его вручную в ${CONFIG_DIR}/env как SL_DB_PASS=${DB_PASS}"
        fi
    else
        warn "Пароль не задан — запишите его вручную в ${CONFIG_DIR}/env как SL_DB_PASS=<пароль>"
    fi

    echo ""
    if ask_yn "Обновить секцию databases в ${CONFIG_DIR}/config.yaml?" "y"; then
        update_config_yaml
    else
        echo ""
        info "Вставьте YAML выше в ${CONFIG_DIR}/config.yaml вручную"
    fi

    echo ""
    ok "Настройка PostgreSQL завершена"
    echo ""
}

# ── Main ──

main() {
    echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║  ServerLens — Настройка PostgreSQL        ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

    command -v psql &>/dev/null || fail "psql не найден. Установите: apt install postgresql-client"

    setup_connection
    select_databases
    create_readonly_user
    configure_tables
    print_result
}

main "$@"
