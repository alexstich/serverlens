#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Deploy & Migrate (запускается с ВАШЕЙ машины)
#
# Копирует проект на удалённый сервер по SSH и запускает миграцию
# PHP → Python. Работает с любым сервером из ~/.serverlens/config.yaml
# или с произвольным SSH-адресом.
#
# Использование:
#   bash scripts/deploy.sh service-book          # по имени из конфига
#   bash scripts/deploy.sh service-book rias     # несколько серверов
#   bash scripts/deploy.sh all                   # все серверы из конфига
#   bash scripts/deploy.sh user@host:port        # произвольный SSH
#
# Что делает:
#   1. Собирает архив с Python-проектом
#   2. Копирует на сервер через scp
#   3. Запускает миграцию через ssh (sudo)
#   4. Чистит за собой
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
info() { echo -e "  ${CYAN}▸${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
run()  { echo -e "  ${YELLOW}\$${NC} $*"; "$@"; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="${HOME}/.serverlens/config.yaml"
REMOTE_TMP="/tmp/serverlens-deploy"

if [[ $# -eq 0 ]] || [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    echo -e "${BOLD}ServerLens Deploy & Migrate${NC}"
    echo ""
    echo "Usage:"
    echo "  bash scripts/deploy.sh <server_name> [server2 ...]"
    echo "  bash scripts/deploy.sh all"
    echo "  bash scripts/deploy.sh user@host:port"
    echo ""
    echo "Examples:"
    echo "  bash scripts/deploy.sh service-book"
    echo "  bash scripts/deploy.sh service-book rias rias-test"
    echo "  bash scripts/deploy.sh all"
    echo "  bash scripts/deploy.sh rucode@185.41.162.124"
    echo ""
    echo "Server names are read from ${CONFIG_FILE}"
    exit 0
fi

# ═══ Parse server config ═══
resolve_ssh() {
    local name="$1"

    # Если формат user@host или user@host:port
    if [[ "$name" == *@* ]]; then
        local user_host="${name%%:*}"
        local port="${name##*:}"
        [[ "$port" == "$name" ]] && port="22"
        local user="${user_host%%@*}"
        local host="${user_host##*@}"
        echo "$user $host $port"
        return 0
    fi

    # Из конфига
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo ""
        return 1
    fi

    python3 -c "
import yaml, sys
with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)
srv = cfg.get('servers', {}).get('$name')
if not srv:
    sys.exit(1)
ssh = srv.get('ssh', {})
print(ssh.get('user','root'), ssh.get('host',''), ssh.get('port', 22))
" 2>/dev/null
}

resolve_key() {
    local name="$1"
    if [[ "$name" == *@* ]]; then
        echo ""
        return
    fi
    python3 -c "
import yaml, sys, os
with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)
srv = cfg.get('servers', {}).get('$name')
if not srv:
    sys.exit(1)
key = srv.get('ssh', {}).get('key', '')
print(key.replace('~', os.environ.get('HOME', '')))
" 2>/dev/null
}

list_servers() {
    python3 -c "
import yaml
with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)
for name in cfg.get('servers', {}):
    print(name)
" 2>/dev/null
}

# ═══ Build archive ═══
build_archive() {
    local archive="/tmp/serverlens-deploy.tar.gz"
    tar -czf "$archive" \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        -C "$PROJECT_DIR" \
        serverlens/ \
        pyproject.toml \
        requirements.txt \
        scripts/install.sh \
        scripts/migrate-php-to-python.sh \
        etc/serverlens.service \
        config.example.yaml \
        2>/dev/null || true
    echo "$archive"
}

# ═══ Deploy to one server ═══
deploy_server() {
    local name="$1"
    local archive="$2"

    echo -e "\n${BOLD}════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Deploying to: ${CYAN}${name}${NC}"
    echo -e "${BOLD}════════════════════════════════════════${NC}"

    local ssh_info
    ssh_info=$(resolve_ssh "$name")
    if [[ -z "$ssh_info" ]]; then
        fail "Сервер '$name' не найден в ${CONFIG_FILE}"
        return 1
    fi

    local user host port key
    read -r user host port <<< "$ssh_info"
    key=$(resolve_key "$name")

    local ssh_opts="-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p $port"
    local scp_opts="-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P $port"
    if [[ -n "$key" ]]; then
        ssh_opts="$ssh_opts -i $key"
        scp_opts="$scp_opts -i $key"
    fi

    local target="${user}@${host}"

    local ssh_cmd="ssh $ssh_opts"
    local scp_cmd="scp $scp_opts"

    # 1. Проверяем соединение
    info "Проверяю SSH соединение..."
    echo -e "  ${YELLOW}\$${NC} ssh ${ssh_opts} ${target} echo ok"
    if ! $ssh_cmd "$target" "echo ok" &>/dev/null; then
        fail "Не могу подключиться к ${target}:${port}"
        return 1
    fi
    ok "SSH соединение"

    # 2. Копируем архив
    info "Копирую проект на сервер..."
    echo -e "  ${YELLOW}\$${NC} scp ${scp_opts} ${archive} ${target}:/tmp/serverlens-deploy.tar.gz"
    if ! $scp_cmd "$archive" "${target}:/tmp/serverlens-deploy.tar.gz"; then
        fail "Не удалось скопировать архив на ${target}"
        return 1
    fi

    echo -e "  ${YELLOW}\$${NC} ssh ... ${target} test -f /tmp/serverlens-deploy.tar.gz"
    if ! $ssh_cmd "$target" "test -f /tmp/serverlens-deploy.tar.gz"; then
        fail "Архив не найден на сервере после копирования"
        return 1
    fi
    ok "Архив скопирован ($(du -h "$archive" | cut -f1))"

    # 3. Распаковываем и запускаем миграцию
    info "Подключаюсь к серверу для миграции..."
    echo -e "  ${YELLOW}\$${NC} ssh -t ${ssh_opts} ${target} ..."
    echo -e "  ${YELLOW}  [remote]\$${NC} tar -xzf /tmp/serverlens-deploy.tar.gz -C /tmp/serverlens-deploy"
    echo -e "  ${YELLOW}  [remote]\$${NC} # если есть /etc/serverlens/config.yaml → миграция, иначе → свежая установка"
    echo ""

    ssh -t $ssh_opts "$target" bash -c "'
        set -euo pipefail
        rm -rf /tmp/serverlens-deploy
        mkdir -p /tmp/serverlens-deploy
        tar -xzf /tmp/serverlens-deploy.tar.gz -C /tmp/serverlens-deploy

        # Чистим сломанный venv от прошлых попыток
        if [ -d /opt/serverlens/venv ] && [ ! -x /opt/serverlens/venv/bin/pip ]; then
            echo \"  ▸ Обнаружен сломанный venv — удаляю\"
            sudo rm -rf /opt/serverlens/venv
        fi

        if [ -f /etc/serverlens/config.yaml ]; then
            echo \"  ▸ Обнаружена существующая установка — запускаю миграцию\"
            SCRIPT=\"/tmp/serverlens-deploy/scripts/migrate-php-to-python.sh\"
        else
            echo \"  ▸ Конфига нет — запускаю свежую установку (--no-wizard)\"
            SCRIPT=\"/tmp/serverlens-deploy/scripts/install.sh --no-wizard\"
        fi

        if [ \$(id -u) -eq 0 ]; then
            bash \$SCRIPT
        else
            sudo bash \$SCRIPT
        fi
        rm -rf /tmp/serverlens-deploy /tmp/serverlens-deploy.tar.gz
    '"

    local exit_code=$?
    echo ""
    if [[ $exit_code -eq 0 ]]; then
        ok "${name}: миграция завершена успешно"
    else
        fail "${name}: миграция завершилась с ошибкой (код ${exit_code})"
    fi
    return $exit_code
}

# ═══ Main ═══
SERVERS=()
if [[ "$1" == "all" ]]; then
    while IFS= read -r srv; do
        [[ -n "$srv" ]] && SERVERS+=("$srv")
    done < <(list_servers)
    if [[ ${#SERVERS[@]} -eq 0 ]]; then
        fail "Нет серверов в ${CONFIG_FILE}"
        exit 1
    fi
    info "Серверы из конфига: ${SERVERS[*]}"
else
    SERVERS=("$@")
fi

info "Собираю архив проекта..."
ARCHIVE=$(build_archive)
if [[ ! -f "$ARCHIVE" ]]; then
    fail "Не удалось собрать архив"
    exit 1
fi
ok "Архив: $(du -h "$ARCHIVE" | cut -f1)"

SUCCEEDED=0
FAILED=0

for srv in "${SERVERS[@]}"; do
    if deploy_server "$srv" "$ARCHIVE"; then
        ((SUCCEEDED++))
    else
        ((FAILED++))
    fi
done

rm -f "$ARCHIVE"

echo ""
echo -e "${BOLD}════════════════════════════════════════${NC}"
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  Готово: ${SUCCEEDED} сервер(ов) мигрировано${NC}"
else
    echo -e "${YELLOW}${BOLD}  Результат: ${SUCCEEDED} ✓ / ${FAILED} ✗${NC}"
fi
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo ""

if [[ $SUCCEEDED -gt 0 ]]; then
    info "После миграции серверов обновите ~/.serverlens/config.yaml —"
    info "удалите строки 'command:' у мигрированных серверов."
    info "MCP-клиент будет использовать команду по умолчанию: serverlens serve --stdio"
    echo ""
fi

exit $FAILED
