#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Смена пароля read-only пользователя PostgreSQL (change_db_password.sh)
#
# Описание:
#   Безопасно меняет пароль пользователя PostgreSQL, используемого
#   ServerLens для read-only доступа к базам данных. Выполняет:
#     1. Подключение к PostgreSQL (peer auth через sudo -u postgres,
#        или по паролю суперпользователя)
#     2. Проверку существования указанного пользователя в pg_roles
#     3. Генерацию нового пароля (--generate или при пустом вводе)
#        либо ввод пароля вручную
#     4. Изменение пароля в PostgreSQL (ALTER USER ... WITH PASSWORD)
#     5. Обновление пароля в /etc/serverlens/env (переменная SL_DB_PASS)
#
# Запуск:
#   sudo bash scripts/change_db_password.sh                — интерактивный режим
#   sudo bash scripts/change_db_password.sh --user=myuser  — указать пользователя
#   sudo bash scripts/change_db_password.sh --generate     — автогенерация пароля
#   sudo bash scripts/change_db_password.sh --help         — справка
#
# Безопасность:
#   - Требует root-прав для peer-подключения к PostgreSQL
#   - Проверяет существование пользователя перед изменением пароля
#   - Пароли экранируются перед использованием в SQL и sed
#   - НЕ изменяет права доступа, таблицы или другие настройки пользователя
#   - Env-файл получает права 640 (root:serverlens)
#   - Перезапуск ServerLens не требуется — пароль читается при каждом подключении
#
# Что НЕ делает скрипт:
#   - Не создаёт и не удаляет пользователей PostgreSQL
#   - Не изменяет GRANT-права или настройки read-only
#   - Не трогает config.yaml
#   - Не перезапускает PostgreSQL или ServerLens
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
            echo "Использование: sudo bash $0 [--user=имя] [--generate]"
            echo ""
            echo "  --user=имя    Имя пользователя PostgreSQL (по умолчанию: serverlens_readonly)"
            echo "  --generate    Сгенерировать пароль без запроса"
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

echo -e "\n${BOLD}  Смена пароля PostgreSQL: ${DB_USER}${NC}\n"

# Подключение
PG_CMD=""
if [[ "$(id -u)" -eq 0 ]] && sudo -u postgres psql -t -A -c "SELECT 1" &>/dev/null 2>&1; then
    PG_CMD="sudo -u postgres psql"
elif command -v psql &>/dev/null; then
    echo -n "  Пароль суперпользователя postgres: "
    read -rs pg_pass; echo ""
    export PGPASSWORD="$pg_pass"
    PG_CMD="psql -h localhost -U postgres"
    if ! $PG_CMD -t -A -c "SELECT 1" &>/dev/null 2>&1; then
        fail "Не удалось подключиться к PostgreSQL"
    fi
else
    fail "psql не найден"
fi

# Проверяем, что пользователь существует
user_exists=$($PG_CMD -t -A -c "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}';" 2>/dev/null || true)
if [[ "$user_exists" != "1" ]]; then
    fail "Пользователь '${DB_USER}' не найден в PostgreSQL"
fi
ok "Пользователь '${DB_USER}' найден"

# Новый пароль
NEW_PASS=""
if $GENERATE; then
    NEW_PASS=$(openssl rand -base64 24 | tr -d '/+=')
    echo -e "  ${CYAN}Новый пароль: ${NEW_PASS}${NC}"
else
    echo -n "  Новый пароль (пустое — сгенерировать): "
    read -rs NEW_PASS; echo ""
    if [[ -z "$NEW_PASS" ]]; then
        NEW_PASS=$(openssl rand -base64 24 | tr -d '/+=')
        echo -e "  ${CYAN}Сгенерирован: ${NEW_PASS}${NC}"
    fi
fi

# Меняем пароль в PostgreSQL
$PG_CMD -c "ALTER USER \"${DB_USER}\" WITH PASSWORD '$(escape_sql_password "$NEW_PASS")';" &>/dev/null
ok "Пароль изменён в PostgreSQL"

# Обновляем env-файл
ENV_FILE="${CONFIG_DIR}/env"
if [[ -f "$ENV_FILE" ]]; then
    if grep -q "^SL_DB_PASS=" "$ENV_FILE" 2>/dev/null; then
        escaped_pass=$(escape_sed_replacement "$NEW_PASS")
        sed -i "s|^SL_DB_PASS=.*|SL_DB_PASS=${escaped_pass}|" "$ENV_FILE"
    else
        printf 'SL_DB_PASS=%s\n' "$NEW_PASS" >> "$ENV_FILE"
    fi
    chmod 640 "$ENV_FILE" 2>/dev/null || true
    chown root:serverlens "$ENV_FILE" 2>/dev/null || true
    ok "Обновлён ${ENV_FILE}"
else
    warn "${ENV_FILE} не найден"
    echo -e "  Запишите вручную: SL_DB_PASS=${NEW_PASS}"
fi

echo ""
ok "Готово. Перезапуск ServerLens не требуется — пароль читается при каждом подключении."
echo ""
