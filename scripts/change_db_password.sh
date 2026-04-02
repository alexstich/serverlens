#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Change PostgreSQL read-only user password (change_db_password.sh)
#
# Description:
#   Safely changes the PostgreSQL password used by ServerLens for read-only
#   database access. It:
#     1. Connects to PostgreSQL (peer auth via sudo -u postgres,
#        or superuser password)
#     2. Verifies the given user exists in pg_roles
#     3. Generates a new password (--generate or empty input)
#        or reads a password interactively
#     4. Changes the password in PostgreSQL (ALTER USER ... WITH PASSWORD)
#     5. Updates the password in /etc/serverlens/env (SL_DB_PASS)
#
# Usage:
#   sudo bash scripts/change_db_password.sh                — interactive
#   sudo bash scripts/change_db_password.sh --user=myuser  — specify user
#   sudo bash scripts/change_db_password.sh --generate     — auto-generate password
#   sudo bash scripts/change_db_password.sh --help         — help
#
# Security:
#   - Requires root for peer connection to PostgreSQL
#   - Verifies the user exists before changing the password
#   - Escapes passwords for use in SQL and sed
#   - Does NOT change privileges, tables, or other user settings
#   - Env file gets mode 640 (root:serverlens)
#   - ServerLens restart is not required — password is read on each connection
#
# What this script does NOT do:
#   - Does not create or drop PostgreSQL users
#   - Does not change GRANTs or read-only settings
#   - Does not touch config.yaml
#   - Does not restart PostgreSQL or ServerLens
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

CONFIG_DIR="/etc/serverlens"
DB_USER="serverlens_readonly"
GENERATE=false

for arg in "$@"; do
    case "$arg" in
        --user=*)    DB_USER="${arg#*=}" ;;
        --generate)  GENERATE=true ;;
        --help|-h)
            echo "Usage: sudo bash $0 [--user=name] [--generate]"
            echo ""
            echo "  --user=name   PostgreSQL username (default: serverlens_readonly)"
            echo "  --generate    Generate a password without prompting"
            exit 0
            ;;
    esac
done

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

escape_sql_password() {
    printf '%s' "${1//\'/\'\'}"
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[&\\/|]/\\&/g'
}

echo -e "\n${BOLD}  Change PostgreSQL password: ${DB_USER}${NC}\n"

# Connection
PG_CMD=""
if [[ "$(id -u)" -eq 0 ]] && sudo -u postgres psql -t -A -c "SELECT 1" &>/dev/null 2>&1; then
    PG_CMD="sudo -u postgres psql"
elif command -v psql &>/dev/null; then
    echo -n "  postgres superuser password: "
    read -rs pg_pass; echo ""
    export PGPASSWORD="$pg_pass"
    echo -n "  PostgreSQL port [5432]: "
    read -r pg_port
    pg_port="${pg_port:-5432}"
    PG_CMD="psql -h localhost -p ${pg_port} -U postgres"
    if ! $PG_CMD -t -A -c "SELECT 1" &>/dev/null 2>&1; then
        fail "Could not connect to PostgreSQL"
    fi
else
    fail "psql not found"
fi

# Ensure user exists
user_exists=$($PG_CMD -t -A -c "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}';" 2>/dev/null || true)
if [[ "$user_exists" != "1" ]]; then
    fail "User '${DB_USER}' not found in PostgreSQL"
fi
ok "User '${DB_USER}' found"

# New password
NEW_PASS=""
if $GENERATE; then
    NEW_PASS=$(openssl rand -base64 24 | tr -d '/+=')
    echo -e "  ${CYAN}New password: ${NEW_PASS}${NC}"
else
    echo -n "  New password (empty — generate): "
    read -rs NEW_PASS; echo ""
    if [[ -z "$NEW_PASS" ]]; then
        NEW_PASS=$(openssl rand -base64 24 | tr -d '/+=')
        echo -e "  ${CYAN}Generated: ${NEW_PASS}${NC}"
    fi
fi

# Change password in PostgreSQL
$PG_CMD -c "ALTER USER \"${DB_USER}\" WITH PASSWORD '$(escape_sql_password "$NEW_PASS")';" &>/dev/null
ok "Password changed in PostgreSQL"

# Update env file
ENV_FILE="${CONFIG_DIR}/env"
mkdir -p "${CONFIG_DIR}"
if [[ -f "$ENV_FILE" ]] && grep -q "^SL_DB_PASS=" "$ENV_FILE" 2>/dev/null; then
    escaped_pass=$(escape_sed_replacement "$NEW_PASS")
    sed -i "s|^SL_DB_PASS=.*|SL_DB_PASS=${escaped_pass}|" "$ENV_FILE"
else
    printf 'SL_DB_PASS=%s\n' "$NEW_PASS" >> "$ENV_FILE"
fi
chmod 640 "$ENV_FILE" 2>/dev/null || true
chown root:serverlens "$ENV_FILE" 2>/dev/null || true
ok "Password written to ${ENV_FILE}"

echo ""
ok "Done. ServerLens restart is not required — the password is read on each connection."
echo ""
