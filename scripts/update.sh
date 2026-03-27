#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Скрипт обновления (update.sh)
#
# Описание:
#   Обновляет установленный ServerLens до актуальной версии. Выполняет:
#     1. Проверку предусловий (root, наличие /opt/serverlens, PHP)
#     2. Получение обновлений из git-репозитория (git pull --ff-only)
#        — pull выполняется от имени владельца .git, а не root
#        — при недоступности git предлагает альтернативы (rsync, ручной pull)
#     3. Создание резервной копии текущих файлов в /opt/serverlens/.backup.TIMESTAMP
#     4. Замену src/, bin/, composer.json, composer.lock в /opt/serverlens
#     5. Обновление PHP-зависимостей (composer install --no-dev)
#     6. Валидацию конфигурации (validate-config)
#     7. Опциональный перезапуск systemd-сервиса (--restart)
#
# Запуск:
#   sudo bash scripts/update.sh              — стандартное обновление
#   sudo bash scripts/update.sh --no-pull    — без git pull (файлы обновлены вручную)
#   sudo bash scripts/update.sh --restart    — перезапустить сервис после обновления
#   sudo bash scripts/update.sh --help       — справка
#
# Безопасность:
#   - Требует root-прав (проверяет id -u)
#   - НЕ ИЗМЕНЯЕТ config.yaml и env-файлы — обновляется только код приложения
#   - Создаёт резервную копию перед обновлением файлов (.backup.TIMESTAMP)
#   - git pull использует --ff-only (отклоняет конфликтные слияния)
#   - Systemd-сервис обновляется только при наличии различий (diff-проверка)
#   - При недоступности git не падает, а предлагает варианты
#   - Перезапуск сервиса только по явному флагу --restart
#
# Что НЕ делает скрипт:
#   - Не трогает конфигурацию (/etc/serverlens/config.yaml, env)
#   - Не изменяет системного пользователя или права директорий
#   - Не удаляет резервные копии (накапливаются — очищайте вручную)
#   - Не делает force push/pull и не меняет ветку git
# ═══════════════════════════════════════════════════════════════════════
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
        warn "Директория ${SCRIPT_DIR} не является git-репозиторием, пропуск git pull"
        return
    fi

    cd "$SCRIPT_DIR"

    local repo_owner
    repo_owner=$(stat -c '%U' "${SCRIPT_DIR}/.git" 2>/dev/null || stat -f '%Su' "${SCRIPT_DIR}/.git" 2>/dev/null || echo "")

    local before
    before=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

    local branch
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    info "Ветка: ${branch}"

    local pull_ok=false

    if [[ -n "$repo_owner" && "$repo_owner" != "root" ]]; then
        info "Репозиторий принадлежит пользователю '${repo_owner}', запускаю git pull от его имени"
        if sudo -u "$repo_owner" git -C "$SCRIPT_DIR" fetch origin 2>/dev/null \
           && sudo -u "$repo_owner" git -C "$SCRIPT_DIR" pull origin "$branch" --ff-only 2>/dev/null; then
            pull_ok=true
        fi
    else
        if git fetch origin 2>/dev/null && git pull origin "$branch" --ff-only 2>/dev/null; then
            pull_ok=true
        fi
    fi

    if ! $pull_ok; then
        warn "git pull не удался (нет SSH-доступа к репозиторию с этого сервера)"
        echo ""
        info "Варианты обновления:"
        echo -e "    ${CYAN}1)${NC} На своей машине: ${BOLD}git push${NC}, затем на сервере от обычного пользователя:"
        echo -e "       ${CYAN}cd ~/serverlens-src && git pull${NC}"
        echo -e "       ${CYAN}sudo bash scripts/update.sh --no-pull${NC}"
        echo ""
        echo -e "    ${CYAN}2)${NC} Со своей машины через rsync:"
        echo -e "       ${CYAN}rsync -avz --exclude .git --exclude vendor ./ user@server:~/serverlens-src/${NC}"
        echo -e "       Затем на сервере: ${CYAN}sudo bash scripts/update.sh --no-pull${NC}"
        echo ""

        local answer
        read -rp "  Продолжить обновление из текущих файлов? [Y/n]: " answer
        if [[ -n "$answer" && "${answer,,}" != "y" ]]; then
            fail "Обновление отменено"
        fi
        ok "Продолжаю с текущими файлами (${before:0:8})"
        return
    fi

    local after
    after=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

    if [[ "$before" == "$after" ]]; then
        ok "Уже актуальная версия (${before:0:8})"
    else
        local count
        count=$(git rev-list "${before}..${after}" --count 2>/dev/null || echo "?")
        ok "Обновлено: ${before:0:8} → ${after:0:8} (${count} коммитов)"
        echo ""
        info "Изменения:"
        git --no-pager log --oneline "${before}..${after}" 2>/dev/null | while IFS= read -r line; do
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
