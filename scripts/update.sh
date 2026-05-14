#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Update script (update.sh)
#
# Description:
#   Updates the installed ServerLens to the current version. It:
#     1. Checks prerequisites (root, /opt/serverlens exists, Python)
#     2. Fetches updates from the git repository (git pull --ff-only)
#        — pull runs as the owner of .git, not root
#        — if git is unavailable, suggests alternatives (rsync, manual pull)
#     3. Backs up current files under /opt/serverlens/.backup.TIMESTAMP
#     4. Replaces serverlens/, pyproject.toml, requirements.txt in /opt/serverlens
#     5. Updates Python dependencies (pip install .)
#     6. Validates configuration (validate-config)
#     7. Optionally restarts the systemd service (--restart)
#
# Usage:
#   sudo bash scripts/update.sh              — standard update
#   sudo bash scripts/update.sh --no-pull    — skip git pull (files updated manually)
#   sudo bash scripts/update.sh --restart    — restart service after update
#   sudo bash scripts/update.sh --help       — show help
#
# Security:
#   - Requires root (checks id -u)
#   - Does NOT change config.yaml or env files — only application code is updated
#   - Creates a backup before updating files (.backup.TIMESTAMP)
#   - git pull uses --ff-only (rejects non-fast-forward merges)
#   - Systemd unit is updated only when it differs (diff check)
#   - If git is unavailable, the script does not abort; it offers options
#   - Service restart only with explicit --restart
#
# What this script does NOT do:
#   - Does not touch configuration (/etc/serverlens/config.yaml, env)
#   - Does not change system user or directory permissions
#   - Does not remove backups (they accumulate — clean up manually)
#   - Does not force push/pull or change the git branch
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
    echo "Usage: sudo bash scripts/update.sh [options]"
    echo ""
    echo "Options:"
    echo "  --no-pull      Skip git pull (if you already updated manually)"
    echo "  --restart      Restart systemd service after update"
    echo "  --help         Show this help"
    exit 0
}

DO_PULL=true
DO_RESTART=false

for arg in "$@"; do
    case "$arg" in
        --no-pull)  DO_PULL=false ;;
        --restart)  DO_RESTART=true ;;
        --help|-h)  usage ;;
        *)          warn "Unknown argument: $arg" ;;
    esac
done

# ═══════════════════════════════════════════════════


check_prerequisites() {
    echo -e "\n${BOLD}[1/5] Checks${NC}"

    [[ "$(id -u)" -ne 0 ]] && fail "Run as root: sudo bash scripts/update.sh"

    [[ -d "$INSTALL_DIR" ]] || fail "ServerLens is not installed (${INSTALL_DIR} not found). Run install.sh first"

    [[ -f "${CONFIG_DIR}/config.yaml" ]] || warn "Config ${CONFIG_DIR}/config.yaml not found"

    local python_cmd=""
    for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            python_cmd="$cmd"
            break
        fi
    done
    [[ -z "$python_cmd" ]] && fail "Python not found"

    [[ -d "${INSTALL_DIR}/venv" ]] || fail "Virtual environment not found at ${INSTALL_DIR}/venv. Run install.sh first"

    ok "Prerequisites OK"
}

pull_updates() {
    echo -e "\n${BOLD}[2/5] Fetching updates${NC}"

    if ! $DO_PULL; then
        ok "Skipping git pull (--no-pull)"
        return
    fi

    if [[ ! -d "${SCRIPT_DIR}/.git" ]]; then
        warn "${SCRIPT_DIR} is not a git repository, skipping git pull"
        return
    fi

    cd "$SCRIPT_DIR"

    local repo_owner
    repo_owner=$(stat -c '%U' "${SCRIPT_DIR}/.git" 2>/dev/null || stat -f '%Su' "${SCRIPT_DIR}/.git" 2>/dev/null || echo "")

    local before
    before=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

    local branch
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    info "Branch: ${branch}"

    local pull_ok=false

    if [[ -n "$repo_owner" && "$repo_owner" != "root" ]]; then
        info "Repository owned by user '${repo_owner}', running git pull as that user"
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
        warn "git pull failed (no SSH access to the repository from this server)"
        echo ""
        info "Ways to update:"
        echo -e "    ${CYAN}1)${NC} On your machine: ${BOLD}git push${NC}, then on the server as a normal user:"
        echo -e "       ${CYAN}cd ~/serverlens-src && git pull${NC}"
        echo -e "       ${CYAN}sudo bash scripts/update.sh --no-pull${NC}"
        echo ""
        echo -e "    ${CYAN}2)${NC} From your machine via rsync:"
        echo -e "       ${CYAN}rsync -avz --exclude .git --exclude vendor ./ user@server:~/serverlens-src/${NC}"
        echo -e "       Then on the server: ${CYAN}sudo bash scripts/update.sh --no-pull${NC}"
        echo ""

        local answer
        read -rp "  Continue update from current files? [Y/n]: " answer
        if [[ -n "$answer" && "${answer,,}" != "y" ]]; then
            fail "Update cancelled"
        fi
        ok "Continuing with current files (${before:0:8})"
        return
    fi

    local after
    after=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

    if [[ "$before" == "$after" ]]; then
        ok "Already up to date (${before:0:8})"
    else
        local count
        count=$(git rev-list "${before}..${after}" --count 2>/dev/null || echo "?")
        ok "Updated: ${before:0:8} → ${after:0:8} (${count} commits)"
        echo ""
        info "Changes:"
        git --no-pager log --oneline "${before}..${after}" 2>/dev/null | while IFS= read -r line; do
            echo -e "    ${CYAN}${line}${NC}"
        done
    fi
}

copy_files() {
    echo -e "\n${BOLD}[3/5] Updating files${NC}"

    local backup_dir="${INSTALL_DIR}/.backup.$(date +%Y%m%d%H%M%S)"
    mkdir -p "$backup_dir"

    for item in serverlens pyproject.toml requirements.txt; do
        if [[ -e "${INSTALL_DIR}/${item}" ]]; then
            cp -r "${INSTALL_DIR}/${item}" "${backup_dir}/" 2>/dev/null || true
        fi
    done
    ok "Backup: ${backup_dir}"

    rm -rf "${INSTALL_DIR}/serverlens"
    cp -r "${SCRIPT_DIR}/serverlens" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/pyproject.toml" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/config.example.yaml" "${INSTALL_DIR}/" 2>/dev/null || true
    ok "Files updated in ${INSTALL_DIR}"

    if [[ -f "${SCRIPT_DIR}/etc/serverlens.service" ]]; then
        local current="/etc/systemd/system/serverlens.service"
        if [[ -f "$current" ]]; then
            if ! diff -q "${SCRIPT_DIR}/etc/serverlens.service" "$current" &>/dev/null; then
                cp "${SCRIPT_DIR}/etc/serverlens.service" "$current"
                systemctl daemon-reload
                ok "Systemd unit updated"
            fi
        fi
    fi
}

update_dependencies() {
    echo -e "\n${BOLD}[4/5] Python dependencies${NC}"

    if [[ ! -x "${INSTALL_DIR}/venv/bin/pip" ]]; then
        fail "pip not found in ${INSTALL_DIR}/venv"
    fi

    "${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
    "${INSTALL_DIR}/venv/bin/pip" install --quiet "${INSTALL_DIR}"
    ok "Dependencies updated"
}

verify() {
    echo -e "\n${BOLD}[5/5] Verification${NC}"

    if [[ -f "${CONFIG_DIR}/config.yaml" ]]; then
        if "${INSTALL_DIR}/venv/bin/python" -m serverlens validate-config --config "${CONFIG_DIR}/config.yaml" &>/dev/null; then
            ok "Configuration is valid"
        else
            warn "Configuration validation failed — new settings may have been added"
            warn "Check: serverlens validate-config --config ${CONFIG_DIR}/config.yaml"
        fi
    fi

    if $DO_RESTART; then
        if systemctl is-active serverlens &>/dev/null; then
            systemctl restart serverlens
            ok "Service restarted"
        else
            info "Service was not running; no restart needed"
        fi
    else
        if systemctl is-active serverlens &>/dev/null; then
            warn "Service is running but was not restarted. To restart: sudo systemctl restart serverlens"
            warn "Or run with --restart"
        fi
    fi

    local commit
    commit=$(cd "$SCRIPT_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "n/a")

    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║        Update complete!                  ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Commit:       ${CYAN}${commit}${NC}"
    echo -e "  Config:       ${CYAN}${CONFIG_DIR}/config.yaml${NC} (unchanged)"
    echo -e "  Application:  ${CYAN}${INSTALL_DIR}/venv/bin/python -m serverlens${NC}"
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
