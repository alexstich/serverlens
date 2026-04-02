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

$local_python -c 'import venv' 2>/dev/null || fail "Python venv module not found. Install: apt install python3-venv"
ok "venv module"

if [[ -d "${INSTALL_DIR}/vendor" ]] || [[ -f "${INSTALL_DIR}/composer.json" ]]; then
    ok "PHP installation detected"
else
    warn "No PHP installation found at ${INSTALL_DIR} — proceeding anyway"
fi

# ═══ Step 2: Backup config ═══
echo -e "\n${BOLD}[2/7] Backup configuration${NC}"
BACKUP="${CONFIG_DIR}/config.yaml.pre-migration.$(date +%Y%m%d%H%M%S)"
cp "${CONFIG_DIR}/config.yaml" "$BACKUP"
ok "Backed up: $BACKUP"

if [[ -f "${CONFIG_DIR}/env" ]]; then
    cp "${CONFIG_DIR}/env" "${CONFIG_DIR}/env.pre-migration.$(date +%Y%m%d%H%M%S)"
    ok "Backed up: env"
fi

# ═══ Step 3: Stop old service ═══
echo -e "\n${BOLD}[3/7] Stop PHP service${NC}"
if [[ -f /etc/systemd/system/serverlens.service ]]; then
    systemctl stop serverlens 2>/dev/null && ok "Service stopped" || ok "Service was not running"
    systemctl disable serverlens 2>/dev/null || true
    rm -f /etc/systemd/system/serverlens.service
    systemctl daemon-reload 2>/dev/null || true
    ok "Old service file removed"
else
    ok "No systemd service (skipped)"
fi

# ═══ Step 4: Remove PHP files ═══
echo -e "\n${BOLD}[4/7] Remove PHP installation${NC}"
if [[ -d "${INSTALL_DIR}" ]]; then
    rm -rf "${INSTALL_DIR}"
    ok "Removed ${INSTALL_DIR}"
else
    ok "${INSTALL_DIR} not found (skipped)"
fi

# ═══ Step 5: Install Python version ═══
echo -e "\n${BOLD}[5/7] Install Python version${NC}"

mkdir -p "${INSTALL_DIR}" "${LOG_DIR}"
chown "${SERVICE_USER}:${SERVICE_USER}" "${LOG_DIR}" 2>/dev/null || true
chmod 750 "${LOG_DIR}" 2>/dev/null || true

cp -r "${SCRIPT_DIR}/serverlens" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/pyproject.toml" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"
ok "Files copied"

$local_python -m venv "${INSTALL_DIR}/venv"
ok "Virtual environment created"

"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
"${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
ok "Dependencies installed"

# ═══ Step 6: Install wrapper and systemd service ═══
echo -e "\n${BOLD}[6/7] CLI wrapper & systemd service${NC}"

cat > /usr/local/bin/serverlens <<WRAPPER
#!/bin/bash
exec "${INSTALL_DIR}/venv/bin/python" -m serverlens "\$@"
WRAPPER
chmod +x /usr/local/bin/serverlens
ok "CLI wrapper: /usr/local/bin/serverlens"

if [[ -f "${SCRIPT_DIR}/etc/serverlens.service" ]]; then
    cp "${SCRIPT_DIR}/etc/serverlens.service" /etc/systemd/system/serverlens.service
    systemctl daemon-reload
    ok "New service file installed"
else
    warn "serverlens.service not found in project, skipping"
fi

# ═══ Step 7: Validate and test ═══
echo -e "\n${BOLD}[7/7] Validation${NC}"

"${INSTALL_DIR}/venv/bin/python" -m serverlens validate-config --config "${CONFIG_DIR}/config.yaml" 2>&1 && \
    ok "Config validation passed" || fail "Config validation FAILED"

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"migrate-test","version":"1.0"}}}'
RESULT=$(echo "$INIT" | "${INSTALL_DIR}/venv/bin/python" -m serverlens serve --config "${CONFIG_DIR}/config.yaml" --stdio 2>/dev/null | head -1)

if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['serverInfo']['name']=='ServerLens'" 2>/dev/null; then
    ok "MCP stdio smoke test passed"
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
