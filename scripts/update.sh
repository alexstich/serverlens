#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/serverlens"
CONFIG_DIR="/etc/serverlens"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "  ${BLUE}▸${NC} $1"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
    echo "Использование: sudo bash scripts/update.sh [опции]"
    echo ""
    echo "Опции:"
    echo "  --no-pull      Не делать git pull (если уже обновили вручную)"
    echo "  --restart      Перезапустить systemd-сервис после обновления"
    echo "  --help         Показать эту справку"
    exit 0
}

DO_PULL=true
DO_RESTART=false

for arg in "$@"; do
    case "$arg" in
        --no-pull)  DO_PULL=false ;;
        --restart)  DO_RESTART=true ;;
        --help|-h)  usage ;;
        *)          warn "Неизвестный аргумент: $arg" ;;
    esac
done

# ═══════════════════════════════════════════════════


check_prerequisites() {
    echo -e "\n${BOLD}[1/5] Проверка${NC}"

    [[ "$(id -u)" -ne 0 ]] && fail "Запустите от root: sudo bash scripts/update.sh"

    [[ -d "$INSTALL_DIR" ]] || fail "ServerLens не установлен (${INSTALL_DIR} не найден). Сначала выполните install.sh"

    [[ -f "${CONFIG_DIR}/config.yaml" ]] || warn "Конфиг ${CONFIG_DIR}/config.yaml не найден"

    command -v php &>/dev/null || fail "PHP не найден"
    ok "Предусловия в порядке"
}

pull_updates() {
    echo -e "\n${BOLD}[2/5] Получение обновлений${NC}"

    if ! $DO_PULL; then
        ok "Пропуск git pull (--no-pull)"
        return
    fi

    if [[ ! -d "${SCRIPT_DIR}/.git" ]]; then
        fail "Директория ${SCRIPT_DIR} не является git-репозиторием"
    fi

    cd "$SCRIPT_DIR"
    local before after
    before=$(git rev-parse HEAD)

    local branch
    branch=$(git rev-parse --abbrev-ref HEAD)
    info "Ветка: ${branch}"

    git fetch origin
    git pull origin "$branch" --ff-only || fail "Не удалось выполнить git pull (возможно, есть локальные изменения). Сделайте git stash или git reset вручную."

    after=$(git rev-parse HEAD)

    if [[ "$before" == "$after" ]]; then
        ok "Уже актуальная версия (${before:0:8})"
    else
        local count
        count=$(git rev-list "${before}..${after}" --count)
        ok "Обновлено: ${before:0:8} → ${after:0:8} (${count} коммитов)"
        echo ""
        info "Изменения:"
        git --no-pager log --oneline "${before}..${after}" | while IFS= read -r line; do
            echo -e "    ${CYAN}${line}${NC}"
        done
    fi
}

copy_files() {
    echo -e "\n${BOLD}[3/5] Обновление файлов${NC}"

    local backup_dir="${INSTALL_DIR}/.backup.$(date +%Y%m%d%H%M%S)"
    mkdir -p "$backup_dir"

    for item in src bin composer.json composer.lock; do
        if [[ -e "${INSTALL_DIR}/${item}" ]]; then
            cp -r "${INSTALL_DIR}/${item}" "${backup_dir}/" 2>/dev/null || true
        fi
    done
    ok "Резервная копия: ${backup_dir}"

    rm -rf "${INSTALL_DIR}/src" "${INSTALL_DIR}/bin"
    cp -r "${SCRIPT_DIR}/src" "${INSTALL_DIR}/"
    cp -r "${SCRIPT_DIR}/bin" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/composer.json" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/composer.lock" "${INSTALL_DIR}/" 2>/dev/null || true
    chmod +x "${INSTALL_DIR}/bin/serverlens"
    ok "Файлы обновлены в ${INSTALL_DIR}"

    if [[ -f "${SCRIPT_DIR}/etc/serverlens.service" ]]; then
        local current="/etc/systemd/system/serverlens.service"
        if [[ -f "$current" ]]; then
            if ! diff -q "${SCRIPT_DIR}/etc/serverlens.service" "$current" &>/dev/null; then
                cp "${SCRIPT_DIR}/etc/serverlens.service" "$current"
                systemctl daemon-reload
                ok "Systemd-сервис обновлён"
            fi
        fi
    fi
}

update_dependencies() {
    echo -e "\n${BOLD}[4/5] PHP-зависимости${NC}"

    cd "${INSTALL_DIR}"

    if ! command -v composer &>/dev/null; then
        fail "Composer не найден"
    fi

    composer install --no-dev --optimize-autoloader --no-interaction --quiet
    ok "Зависимости обновлены"
}

verify() {
    echo -e "\n${BOLD}[5/5] Проверка${NC}"

    if [[ -f "${CONFIG_DIR}/config.yaml" ]]; then
        if php "${INSTALL_DIR}/bin/serverlens" validate-config --config "${CONFIG_DIR}/config.yaml" &>/dev/null; then
            ok "Конфигурация валидна"
        else
            warn "Конфигурация невалидна — возможно, добавились новые параметры"
            warn "Проверьте: php ${INSTALL_DIR}/bin/serverlens validate-config --config ${CONFIG_DIR}/config.yaml"
        fi
    fi

    if $DO_RESTART; then
        if systemctl is-active serverlens &>/dev/null; then
            systemctl restart serverlens
            ok "Сервис перезапущен"
        else
            info "Сервис не был запущен, перезапуск не требуется"
        fi
    else
        if systemctl is-active serverlens &>/dev/null; then
            warn "Сервис запущен, но не перезапущен. Для перезапуска: sudo systemctl restart serverlens"
            warn "Или запустите с флагом --restart"
        fi
    fi

    local version
    version=$(php "${INSTALL_DIR}/bin/serverlens" --version 2>/dev/null || echo "н/д")
    local commit
    commit=$(cd "$SCRIPT_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "н/д")

    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║        Обновление завершено!             ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Коммит:       ${CYAN}${commit}${NC}"
    echo -e "  Конфигурация: ${CYAN}${CONFIG_DIR}/config.yaml${NC} (не изменена)"
    echo -e "  Программа:    ${CYAN}${INSTALL_DIR}/bin/serverlens${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════

main() {
    echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║     ServerLens Updater                   ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

    check_prerequisites
    pull_updates
    copy_files
    update_dependencies
    verify
}

main "$@"
