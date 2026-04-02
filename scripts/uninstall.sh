#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Uninstall Script
#
# Removes ServerLens (both PHP and Python versions) from the server.
# Safe to run multiple times.
#
# Usage:
#   sudo bash scripts/uninstall.sh              — interactive (asks before each step)
#   sudo bash scripts/uninstall.sh --yes        — remove everything without asking
#   sudo bash scripts/uninstall.sh --keep-config — remove all but keep /etc/serverlens
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/opt/serverlens"
CONFIG_DIR="/etc/serverlens"
LOG_DIR="/var/log/serverlens"
SERVICE_USER="serverlens"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
info() { echo -e "  ${YELLOW}▸${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

AUTO_YES=false
KEEP_CONFIG=false

for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=true ;;
        --keep-config) KEEP_CONFIG=true ;;
    esac
done

ask_yn() {
    $AUTO_YES && return 0
    local prompt="$1" answer
    read -rp "  $prompt [y/N]: " answer
    [[ "${answer,,}" == "y" ]]
}

[[ "$(id -u)" -ne 0 ]] && fail "Run as root: sudo bash scripts/uninstall.sh"

echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     ServerLens Uninstaller                ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

echo ""
info "This will remove ServerLens from this server."
info "Detected components:"
echo ""

[[ -f /etc/systemd/system/serverlens.service ]] && info "  systemd service" || true
[[ -d "$INSTALL_DIR" ]] && info "  $INSTALL_DIR ($(du -sh "$INSTALL_DIR" 2>/dev/null | cut -f1))" || true
[[ -d "$CONFIG_DIR" ]] && info "  $CONFIG_DIR" || true
[[ -d "$LOG_DIR" ]] && info "  $LOG_DIR ($(du -sh "$LOG_DIR" 2>/dev/null | cut -f1))" || true
id "$SERVICE_USER" &>/dev/null && info "  system user '$SERVICE_USER'" || true

echo ""

if ! $AUTO_YES; then
    if ! ask_yn "Proceed with uninstall?"; then
        echo "  Cancelled."
        exit 0
    fi
fi

# 1. Stop and disable systemd service
echo -e "\n${BOLD}[1/5] Systemd service${NC}"
if [[ -f /etc/systemd/system/serverlens.service ]]; then
    systemctl stop serverlens 2>/dev/null || true
    systemctl disable serverlens 2>/dev/null || true
    rm -f /etc/systemd/system/serverlens.service
    systemctl daemon-reload 2>/dev/null || true
    ok "Service stopped and removed"
else
    ok "No service file found (skipped)"
fi

# 2. Remove application directory
echo -e "\n${BOLD}[2/5] Application directory${NC}"
if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    ok "Removed $INSTALL_DIR"
else
    ok "$INSTALL_DIR not found (skipped)"
fi

# 3. Remove config directory
echo -e "\n${BOLD}[3/5] Configuration${NC}"
if $KEEP_CONFIG; then
    warn "Keeping $CONFIG_DIR (--keep-config)"
elif [[ -d "$CONFIG_DIR" ]]; then
    if $AUTO_YES || ask_yn "Remove configuration ($CONFIG_DIR)?"; then
        local_backup="/tmp/serverlens-config-backup-$(date +%Y%m%d%H%M%S).tar.gz"
        tar -czf "$local_backup" -C / "etc/serverlens" 2>/dev/null || true
        info "Config backup saved: $local_backup"
        rm -rf "$CONFIG_DIR"
        ok "Removed $CONFIG_DIR"
    else
        warn "Keeping $CONFIG_DIR"
    fi
else
    ok "$CONFIG_DIR not found (skipped)"
fi

# 4. Remove log directory
echo -e "\n${BOLD}[4/5] Logs${NC}"
if [[ -d "$LOG_DIR" ]]; then
    if $AUTO_YES || ask_yn "Remove logs ($LOG_DIR)?"; then
        rm -rf "$LOG_DIR"
        ok "Removed $LOG_DIR"
    else
        warn "Keeping $LOG_DIR"
    fi
else
    ok "$LOG_DIR not found (skipped)"
fi

# 5. Remove CLI wrapper
echo -e "\n${BOLD}[5/6] CLI wrapper${NC}"
if [[ -f /usr/local/bin/serverlens ]]; then
    rm -f /usr/local/bin/serverlens
    ok "Removed /usr/local/bin/serverlens"
else
    ok "No wrapper found (skipped)"
fi

# 6. Remove system user
echo -e "\n${BOLD}[6/6] System user${NC}"
if id "$SERVICE_USER" &>/dev/null; then
    if $AUTO_YES || ask_yn "Remove system user '$SERVICE_USER'?"; then
        userdel "$SERVICE_USER" 2>/dev/null || true
        ok "User '$SERVICE_USER' removed"
    else
        warn "Keeping user '$SERVICE_USER'"
    fi
else
    ok "User '$SERVICE_USER' not found (skipped)"
fi

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       Uninstall complete!                 ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

if $KEEP_CONFIG; then
    info "Config preserved at $CONFIG_DIR"
    info "To reinstall: sudo bash scripts/install.sh --no-wizard"
fi
echo ""
