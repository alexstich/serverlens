#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Migration Script (PHP → Python)
#
# Replaces the PHP version with Python on the same server,
# keeping the existing config.yaml intact.
#
# Usage:
#   sudo bash scripts/migrate-php-to-python.sh
#
# What it does:
#   1. Backs up /etc/serverlens/config.yaml
#   2. Stops and removes the old PHP systemd service
#   3. Removes /opt/serverlens (PHP files, vendor/, composer.*)
#   4. Runs the Python install (copies files, creates venv, installs deps)
#   5. Installs the new systemd service
#   6. Validates the config with the Python version
#   7. Quick smoke test via stdio MCP protocol
#
# The existing config.yaml is preserved as-is (format is compatible).
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

INSTALL_DIR="/opt/serverlens"
CONFIG_DIR="/etc/serverlens"
LOG_DIR="/var/log/serverlens"
SERVICE_USER="serverlens"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
info() { echo -e "  ${CYAN}▸${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
cmd()  { echo -e "  ${YELLOW}\$${NC} $*"; "$@"; }

[[ "$(id -u)" -ne 0 ]] && fail "Run as root: sudo bash scripts/migrate-php-to-python.sh"

echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  ServerLens Migration: PHP → Python       ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

# ═══ Step 1: Pre-flight checks ═══
echo -e "\n${BOLD}[1/7] Pre-flight checks${NC}"

[[ -f "${CONFIG_DIR}/config.yaml" ]] && ok "Config exists: ${CONFIG_DIR}/config.yaml" || fail "No config found at ${CONFIG_DIR}/config.yaml"

local_python=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        local_python="$cmd"
        break
    fi
done
[[ -z "$local_python" ]] && fail "Python not found. Install Python 3.10+"

py_version=$($local_python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
py_major=$($local_python -c 'import sys; print(sys.version_info.major)')
py_minor=$($local_python -c 'import sys; print(sys.version_info.minor)')
(( py_major < 3 || (py_major == 3 && py_minor < 10) )) && fail "Python 3.10+ required, found ${py_version}"
ok "Python ${py_version}"

if $local_python -c 'import ensurepip' 2>/dev/null; then
    ok "venv + ensurepip"
else
    warn "ensurepip отсутствует — устанавливаю..."
    venv_pkg="python${py_version}-venv"
    if command -v apt-get &>/dev/null; then
        echo -e "  ${YELLOW}\$${NC} apt-get update && apt-get install -y ${venv_pkg}"
        apt-get update -qq --allow-releaseinfo-change 2>/dev/null || true
        apt-get install -y "$venv_pkg" 2>/dev/null
        $local_python -c 'import ensurepip' 2>/dev/null || fail "Не удалось установить ${venv_pkg}. Вручную: apt install ${venv_pkg}"
        ok "venv + ensurepip (установлен: ${venv_pkg})"
    elif command -v dnf &>/dev/null; then
        echo -e "  ${YELLOW}\$${NC} dnf install -y python3-pip"
        dnf install -y "python3-pip" 2>/dev/null
        $local_python -c 'import ensurepip' 2>/dev/null || fail "Не удалось установить ensurepip"
        ok "venv + ensurepip (установлен)"
    else
        fail "ensurepip отсутствует. Установите: apt install ${venv_pkg}"
    fi
fi

if [[ -d "${INSTALL_DIR}/vendor" ]] || [[ -f "${INSTALL_DIR}/composer.json" ]]; then
    ok "PHP installation detected"
else
    warn "No PHP installation found at ${INSTALL_DIR} — proceeding anyway"
fi

# ═══ Step 2: Backup config ═══
echo -e "\n${BOLD}[2/7] Backup configuration${NC}"
BACKUP="${CONFIG_DIR}/config.yaml.pre-migration.$(date +%Y%m%d%H%M%S)"
cmd cp "${CONFIG_DIR}/config.yaml" "$BACKUP"
ok "Backed up: $BACKUP"

if [[ -f "${CONFIG_DIR}/env" ]]; then
    cmd cp "${CONFIG_DIR}/env" "${CONFIG_DIR}/env.pre-migration.$(date +%Y%m%d%H%M%S)"
    ok "Backed up: env"
fi

# ═══ Step 3: Stop old service ═══
echo -e "\n${BOLD}[3/7] Stop PHP service${NC}"
if [[ -f /etc/systemd/system/serverlens.service ]]; then
    echo -e "  ${YELLOW}\$${NC} systemctl stop serverlens"
    systemctl stop serverlens 2>/dev/null && ok "Service stopped" || ok "Service was not running"
    echo -e "  ${YELLOW}\$${NC} systemctl disable serverlens"
    systemctl disable serverlens 2>/dev/null || true
    cmd rm -f /etc/systemd/system/serverlens.service
    echo -e "  ${YELLOW}\$${NC} systemctl daemon-reload"
    systemctl daemon-reload 2>/dev/null || true
    ok "Old service file removed"
else
    ok "No systemd service (skipped)"
fi

# ═══ Step 4: Remove PHP files ═══
echo -e "\n${BOLD}[4/7] Remove PHP installation${NC}"
if [[ -d "${INSTALL_DIR}" ]]; then
    info "Содержимое ${INSTALL_DIR}:"
    ls -la "${INSTALL_DIR}/" 2>/dev/null | head -10 | while read -r line; do echo "    $line"; done
    cmd rm -rf "${INSTALL_DIR}"
    ok "Removed ${INSTALL_DIR}"
else
    ok "${INSTALL_DIR} not found (skipped)"
fi

# ═══ Step 5: Install Python version ═══
echo -e "\n${BOLD}[5/7] Install Python version${NC}"

cmd mkdir -p "${INSTALL_DIR}" "${LOG_DIR}"
chown "${SERVICE_USER}:${SERVICE_USER}" "${LOG_DIR}" 2>/dev/null || true
chmod 750 "${LOG_DIR}" 2>/dev/null || true

cmd cp -r "${SCRIPT_DIR}/serverlens" "${INSTALL_DIR}/"
cmd cp "${SCRIPT_DIR}/pyproject.toml" "${INSTALL_DIR}/"
cmd cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"
ok "Files copied"

cmd $local_python -m venv "${INSTALL_DIR}/venv"
ok "Virtual environment created"

echo -e "  ${YELLOW}\$${NC} ${INSTALL_DIR}/venv/bin/pip install --upgrade pip"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
echo -e "  ${YELLOW}\$${NC} ${INSTALL_DIR}/venv/bin/pip install ${INSTALL_DIR}"
"${INSTALL_DIR}/venv/bin/pip" install --quiet "${INSTALL_DIR}"
ok "Dependencies installed"

# ═══ Step 6: Install wrapper and systemd service ═══
echo -e "\n${BOLD}[6/7] CLI wrapper & systemd service${NC}"

echo -e "  ${YELLOW}\$${NC} cat > /usr/local/bin/serverlens << (wrapper script)"
cat > /usr/local/bin/serverlens <<WRAPPER
#!/bin/bash
exec "${INSTALL_DIR}/venv/bin/python" -m serverlens "\$@"
WRAPPER
cmd chmod +x /usr/local/bin/serverlens
ok "CLI wrapper: /usr/local/bin/serverlens"
info "Содержимое wrapper:"
cat /usr/local/bin/serverlens | while read -r line; do echo "    $line"; done

if [[ -f "${SCRIPT_DIR}/etc/serverlens.service" ]]; then
    cmd cp "${SCRIPT_DIR}/etc/serverlens.service" /etc/systemd/system/serverlens.service
    echo -e "  ${YELLOW}\$${NC} systemctl daemon-reload"
    systemctl daemon-reload
    ok "New service file installed"
else
    warn "serverlens.service not found in project, skipping"
fi

# ═══ Step 7: Validate and test ═══
echo -e "\n${BOLD}[7/7] Validation${NC}"

echo -e "  ${YELLOW}\$${NC} serverlens validate-config --config ${CONFIG_DIR}/config.yaml"
"${INSTALL_DIR}/venv/bin/python" -m serverlens validate-config --config "${CONFIG_DIR}/config.yaml" 2>&1 && \
    ok "Config validation passed" || fail "Config validation FAILED"

info "Smoke test: отправляю MCP initialize через stdio..."
echo -e "  ${YELLOW}\$${NC} echo '{...initialize...}' | serverlens serve --stdio --config ${CONFIG_DIR}/config.yaml"
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"migrate-test","version":"1.0"}}}'
RESULT=$(echo "$INIT" | "${INSTALL_DIR}/venv/bin/python" -m serverlens serve --config "${CONFIG_DIR}/config.yaml" --stdio 2>/dev/null | head -1)

if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['serverInfo']['name']=='ServerLens'" 2>/dev/null; then
    ok "MCP stdio smoke test passed"
    info "Response: ${RESULT:0:120}..."
else
    warn "Smoke test returned unexpected result (check config)"
    info "Response: ${RESULT:0:200}"
fi

# ═══ Summary ═══
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║      Migration complete!                  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Config:    ${CYAN}${CONFIG_DIR}/config.yaml${NC} (unchanged)"
echo -e "  Backup:    ${CYAN}${BACKUP}${NC}"
echo -e "  App:       ${CYAN}${INSTALL_DIR}/venv/bin/python -m serverlens${NC}"
echo ""
info "The 'serverlens' command is now available system-wide via /usr/local/bin/serverlens"
info "MCP client connects via SSH and runs: serverlens serve --stdio"
echo ""
info "In your MCP client config (~/.serverlens/config.yaml), remove the 'remote' section."
info "Only SSH credentials are needed:"
echo ""
echo "    servers:"
echo "      $(hostname -s 2>/dev/null || echo 'my-server'):"
echo "        ssh:"
echo "          host: \"$(hostname -f 2>/dev/null || hostname)\""
echo "          user: \"YOUR_USER\""
echo "          key: \"~/.ssh/id_ed25519\""
echo ""
