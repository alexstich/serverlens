#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Interactive PostgreSQL Setup (setup_db.sh)
#
# Description:
#   Configures read-only access for ServerLens to PostgreSQL databases.
#   The script performs:
#     1. Connecting to PostgreSQL (peer auth or superuser password)
#     2. Listing databases with sizes and table counts
#     3. Creating a read-only user (default: serverlens_readonly):
#        — default_transaction_read_only = on (write protection)
#        — statement_timeout = 30s (protection from heavy queries)
#     4. For each selected database:
#        — GRANT CONNECT, USAGE ON SCHEMA public
#        — Displaying tables with automatic sensitive column detection
#        — GRANT SELECT only on selected tables
#     5. Generating YAML databases section for config.yaml
#     6. Saving password to /etc/serverlens/env (SL_DB_PASS=...)
#     7. Updating the databases section in config.yaml (with backup)
#
# Usage:
#   sudo bash scripts/setup_db.sh
#   (also called from install.sh during the setup wizard)
#
# Idempotent — safe to run multiple times:
#   - CREATE USER checks pg_roles before creating
#   - ALTER USER / GRANT are idempotent in PostgreSQL
#   - config.yaml is updated by replacing the databases section (with backup)
#   - env file is updated by replacing the SL_DB_PASS line
#
# Security:
#   - Creates a user with SELECT-only privileges (read-only)
#   - Passwords are escaped before use in SQL and sed
#   - Creates a backup (.bak.YYYYMMDDHHMMSS) when updating config.yaml
#   - Sensitive columns (password, token, secret, etc.) are hidden automatically
#   - Env file gets permissions 640 (root:serverlens)
#   - PGPASSWORD is exported only to the current process
#
# What the script does NOT do:
#   - Does not delete databases, tables, or existing users
#   - Does not modify data in tables
#   - Does not change pg_hba.conf or postgresql.conf
#   - Does not restart PostgreSQL
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

# -- Connection --

PG_CMD=""
PG_HOST="localhost"
PG_PORT="5432"

setup_connection() {
    echo -e "\n${BOLD}  PostgreSQL connection${NC}\n"

    if [[ "$(id -u)" -eq 0 ]] && sudo -u postgres psql -t -A -c "SELECT 1" &>/dev/null 2>&1; then
        PG_CMD="sudo -u postgres psql"
        local detected_port
        detected_port=$(sudo -u postgres psql -t -A -c "SHOW port" 2>/dev/null | tr -d '[:space:]')
        if [[ -n "$detected_port" ]]; then
            PG_PORT="$detected_port"
        fi
        ok "Connected via sudo -u postgres (peer auth, port ${PG_PORT})"
        return
    fi

    warn "Peer connection failed."
    info "Enter PostgreSQL superuser credentials to create the read-only user"
    info "(needed once — to create the monitoring user and grant permissions):"
    echo ""
    PG_HOST=$(ask_input "PostgreSQL host" "localhost")
    PG_PORT=$(ask_input "PostgreSQL port" "5432")
    local pg_user; pg_user=$(ask_input "PostgreSQL superuser" "postgres")

    echo -n "  Superuser password: "
    read -rs pg_pass
    echo ""

    export PGPASSWORD="$pg_pass"
    PG_CMD="psql -h ${PG_HOST} -p ${PG_PORT} -U ${pg_user}"

    if ! $PG_CMD -t -A -c "SELECT 1" &>/dev/null 2>&1; then
        fail "Failed to connect to PostgreSQL"
    fi
    ok "Connected to ${PG_HOST}:${PG_PORT}"
}

pg_exec() {
    $PG_CMD -t -A -c "$1" 2>/dev/null || true
}

pg_exec_db() {
    $PG_CMD -t -A -d "$1" -c "$2" 2>/dev/null || true
}

# -- Database selection --

declare -a SELECTED_DBS=()

select_databases() {
    echo -e "\n${BOLD}  Available databases${NC}\n"

    local databases
    databases=$(pg_exec "SELECT datname FROM pg_database WHERE datistemplate = false AND datname != 'postgres' ORDER BY datname;")

    if [[ -z "$databases" ]]; then
        fail "No databases found"
    fi

    local idx=1
    declare -a db_list=()
    while IFS= read -r dbname; do
        [[ -z "$dbname" ]] && continue
        local sz; sz=$(pg_exec "SELECT pg_size_pretty(pg_database_size('${dbname}'));")
        local cnt; cnt=$(pg_exec_db "$dbname" "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
        info "[${idx}] ${dbname}  (${sz:-?}, ${cnt:-?} tables)"
        db_list+=("$dbname")
        ((idx++))
    done <<< "$databases"

    echo ""
    local sel; sel=$(ask_input "Which databases to monitor? (all / comma-separated numbers / Enter = all)" "all")

    if [[ "$sel" == "all" ]]; then
        SELECTED_DBS=("${db_list[@]}")
    else
        IFS=',' read -ra nums <<< "$sel"
        for n in "${nums[@]}"; do
            n=$(( ${n// /} - 1 ))
            if (( n >= 0 && n < ${#db_list[@]} )); then SELECTED_DBS+=("${db_list[$n]}"); fi
        done
    fi

    if (( ${#SELECTED_DBS[@]} == 0 )); then fail "No databases selected"; fi
    ok "Selected: ${SELECTED_DBS[*]}"
}

# -- Create read-only user (idempotent) --

DB_USER=""
DB_PASS=""

create_readonly_user() {
    echo -e "\n${BOLD}  Read-only user${NC}\n"

    info "ServerLens connects to PostgreSQL via a dedicated user"
    info "with SELECT-only permissions. Safe for production databases."
    echo ""
    DB_USER=$(ask_input "PostgreSQL username for ServerLens" "serverlens_readonly")

    local user_exists
    user_exists=$(pg_exec "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}';")

    if [[ "$user_exists" == "1" ]]; then
        ok "User '${DB_USER}' already exists"

        local env_file="${CONFIG_DIR}/env"
        local existing_pass=""
        if [[ -f "$env_file" ]]; then
            existing_pass=$(grep "^SL_DB_PASS=" "$env_file" 2>/dev/null | cut -d'=' -f2- || true)
        fi

        info "Password is stored in ${env_file} and used by ServerLens to connect."
        if ask_yn "Generate a new password? (current one will stop working)" "n"; then
            DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
            pg_exec "ALTER USER \"${DB_USER}\" WITH PASSWORD '$(escape_sql_password "$DB_PASS")';"
            ok "Password updated"
        elif [[ -n "$existing_pass" ]]; then
            DB_PASS="$existing_pass"
            ok "Password loaded from ${env_file}"
        else
            warn "Password not found in ${env_file}"
            info "ServerLens will not be able to connect to the database without it."
            info "Save the password manually after the script completes:"
            echo ""
            echo "    echo 'SL_DB_PASS=your_password' | sudo tee -a ${env_file}"
            echo ""
        fi
        pg_exec "ALTER USER \"${DB_USER}\" SET default_transaction_read_only = on;"
        pg_exec "ALTER USER \"${DB_USER}\" SET statement_timeout = '30s';"
        ok "User settings confirmed"
    else
        info "A password is needed for the new user. You can generate a random one"
        info "or set your own (the password will be saved in ${CONFIG_DIR}/env)."
        if ask_yn "Generate a random password?" "y"; then
            DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
            echo -e "  ${CYAN}Password: ${DB_PASS}${NC}"
        else
            echo -n "  Enter password: "
            read -rs DB_PASS
            echo ""
        fi

        pg_exec "CREATE USER \"${DB_USER}\" WITH PASSWORD '$(escape_sql_password "$DB_PASS")';"
        pg_exec "ALTER USER \"${DB_USER}\" SET default_transaction_read_only = on;"
        pg_exec "ALTER USER \"${DB_USER}\" SET statement_timeout = '30s';"
        ok "User '${DB_USER}' created"
    fi
}

# -- Configure tables (idempotent — GRANT is idempotent in PG) --

YAML_OUTPUT=""

configure_tables() {
    for dbname in "${SELECTED_DBS[@]}"; do
        echo -e "\n${BOLD}${CYAN}  == DB: ${dbname} ==${NC}\n"

        pg_exec "GRANT CONNECT ON DATABASE \"${dbname}\" TO \"${DB_USER}\";"
        pg_exec_db "$dbname" "GRANT USAGE ON SCHEMA public TO \"${DB_USER}\";"

        local tables
        tables=$(pg_exec_db "$dbname" "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;")

        if [[ -z "$tables" ]]; then
            warn "No tables in database ${dbname}"
            continue
        fi

        local tidx=1
        declare -a table_list=()
        while IFS= read -r tname; do
            [[ -z "$tname" ]] && continue
            local cnt; cnt=$(pg_exec_db "$dbname" "SELECT count(*) FROM \"${tname}\";" 2>/dev/null || echo "?")
            local cols; cols=$(pg_exec_db "$dbname" "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='${tname}';")
            info "[${tidx}] ${tname}  (${cnt:-?} rows, ${cols:-?} columns)"
            table_list+=("$tname")
            ((tidx++))
        done <<< "$tables"

        echo ""
        local tsel; tsel=$(ask_input "Tables to monitor? (all / comma-separated numbers / Enter = all)" "all")
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
        info "How many rows per table to show in the UI."
        info "More rows = more data, but slower loading."
        local max_rows; max_rows=$(ask_input "Max rows per table" "500")

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
                denied_info=" ${RED}hidden: ${denied[*]}${NC}"
                ((total_denied += ${#denied[@]})) || true
            fi
            echo -e "    ${GREEN}✓${NC} ${tname} (${#allowed[@]} visible, ${#denied[@]} hidden)${denied_info}"

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
        ok "DB ${dbname}: ${#selected_tables[@]} tables, ${total_denied} hidden columns"
        if (( total_denied > 0 )); then
            info "Hidden columns detected automatically (password, token, secret, etc.)"
            info "Edit denied_fields in config.yaml if needed"
        fi
    done
}

# -- Save results (idempotent) --

save_env() {
    local env_file="${CONFIG_DIR}/env"
    mkdir -p "${CONFIG_DIR}"

    if [[ -f "$env_file" ]] && grep -q "^SL_DB_PASS=" "$env_file" 2>/dev/null; then
        local escaped; escaped=$(escape_sed_replacement "$DB_PASS")
        sed -i "s|^SL_DB_PASS=.*|SL_DB_PASS=${escaped}|" "$env_file"
        ok "Password updated in ${env_file}"
    else
        printf 'SL_DB_PASS=%s\n' "$DB_PASS" >> "$env_file"
        ok "Password saved to ${env_file}"
    fi
    chmod 640 "$env_file" 2>/dev/null || true
    chown root:serverlens "$env_file" 2>/dev/null || true
}

update_config_yaml() {
    local cfg="${CONFIG_DIR}/config.yaml"

    if [[ ! -f "$cfg" ]]; then
        warn "File ${cfg} not found — insert the YAML manually"
        return
    fi

    local backup="${cfg}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$cfg" "$backup"

    local tmp; tmp=$(mktemp)

    awk '
        BEGIN { skip = 0 }
        /^databases:/ { skip = 1; next }
        skip && /^[a-zA-Z]/ { skip = 0 }
        !skip { print }
    ' "$cfg" > "$tmp"

    {
        echo "databases:"
        echo "  connections:"
        echo -e "$YAML_OUTPUT"
    } >> "$tmp"

    mv "$tmp" "$cfg"
    chmod 640 "$cfg" 2>/dev/null || true
    chown root:serverlens "$cfg" 2>/dev/null || true

    ok "config.yaml updated (backup: ${backup})"
}

print_result() {
    echo -e "\n${BOLD}══════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Result${NC}\n"

    echo -e "  ${BOLD}Generated YAML:${NC}\n"
    echo -e "${CYAN}databases:"
    echo -e "  connections:"
    echo -e "${YAML_OUTPUT}${NC}"

    echo ""
    if [[ -n "$DB_PASS" ]]; then
        save_env
    else
        warn "Password not set — save it manually in ${CONFIG_DIR}/env as SL_DB_PASS=<password>"
    fi

    echo ""
    info "Database connection settings need to be saved in the ServerLens config."
    if ask_yn "Write the databases section to ${CONFIG_DIR}/config.yaml automatically?" "y"; then
        update_config_yaml
    else
        echo ""
        info "Copy the YAML block above and paste it into the config manually:"
        echo ""
        echo "    sudo nano ${CONFIG_DIR}/config.yaml"
        echo ""
        info "Add the block to the 'databases:' section at the end of the file."
    fi

    echo ""
    ok "PostgreSQL setup complete"
    echo ""
}

# -- Main --

main() {
    echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║  ServerLens — PostgreSQL Setup             ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

    command -v psql &>/dev/null || fail "psql not found. Install: apt install postgresql-client"

    setup_connection
    select_databases
    create_readonly_user
    configure_tables
    print_result
}

main "$@"
