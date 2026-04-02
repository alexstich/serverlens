#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; ERRORS=$((ERRORS+1)); }
info() { echo -e "  ${YELLOW}▸${NC} $1"; }

ERRORS=0

echo -e "\n${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  ServerLens Docker Test Suite${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"

# ═══ TEST 1: Install (no wizard) ═══
echo -e "\n${BOLD}[TEST 1] Install script (--no-wizard)${NC}\n"

bash /srv/serverlens/scripts/install.sh --no-wizard 2>&1 && \
    ok "Install completed" || fail "Install failed"

# ═══ TEST 2: Check files exist ═══
echo -e "\n${BOLD}[TEST 2] Verify installed files${NC}\n"

for f in \
    /opt/serverlens/serverlens/__init__.py \
    /opt/serverlens/serverlens/__main__.py \
    /opt/serverlens/serverlens/application.py \
    /opt/serverlens/serverlens/config.py \
    /opt/serverlens/pyproject.toml \
    /opt/serverlens/requirements.txt \
    /opt/serverlens/venv/bin/python \
    /opt/serverlens/venv/bin/pip \
    /etc/serverlens/config.yaml \
    /etc/systemd/system/serverlens.service \
    /usr/local/bin/serverlens; do
    [[ -e "$f" ]] && ok "$f" || fail "Missing: $f"
done

[[ -d /var/log/serverlens ]] && ok "/var/log/serverlens/" || fail "Missing: /var/log/serverlens/"

# ═══ TEST 3: User and permissions ═══
echo -e "\n${BOLD}[TEST 3] User and permissions${NC}\n"

id serverlens &>/dev/null && ok "User 'serverlens' exists" || fail "User 'serverlens' missing"
[[ "$(stat -c '%a' /etc/serverlens/config.yaml)" == "640" ]] && ok "config.yaml permissions 640" || fail "config.yaml wrong permissions"

# ═══ TEST 4: Python venv works ═══
echo -e "\n${BOLD}[TEST 4] Python venv${NC}\n"

/opt/serverlens/venv/bin/python --version 2>&1 && ok "Python in venv" || fail "Python in venv broken"
/opt/serverlens/venv/bin/python -c "import yaml; print(f'pyyaml {yaml.__version__}')" && ok "pyyaml" || fail "pyyaml missing"
/opt/serverlens/venv/bin/python -c "import aiohttp; print(f'aiohttp {aiohttp.__version__}')" && ok "aiohttp" || fail "aiohttp missing"
/opt/serverlens/venv/bin/python -c "import argon2; print('argon2')" && ok "argon2" || fail "argon2 missing"

# ═══ TEST 5: Module imports ═══
echo -e "\n${BOLD}[TEST 5] Module imports${NC}\n"

cd /opt/serverlens
IMPORTS=(
    "serverlens"
    "serverlens.application"
    "serverlens.config"
    "serverlens.mcp.server"
    "serverlens.module.log_reader"
    "serverlens.module.config_reader"
    "serverlens.module.db_query"
    "serverlens.module.system_info"
    "serverlens.auth.token_auth"
    "serverlens.auth.rate_limiter"
    "serverlens.audit.audit_logger"
    "serverlens.security.redactor"
    "serverlens.security.path_guard"
    "serverlens.transport.stdio"
    "serverlens.transport.sse"
)
for mod in "${IMPORTS[@]}"; do
    /opt/serverlens/venv/bin/python -c "import $mod" 2>&1 && ok "import $mod" || fail "import $mod FAILED"
done

# ═══ TEST 6: CLI commands ═══
echo -e "\n${BOLD}[TEST 6] CLI commands${NC}\n"

/opt/serverlens/venv/bin/python -m serverlens --help 2>&1 | grep -q "serve" && \
    ok "CLI --help (via python)" || fail "CLI --help broken"

serverlens --help 2>&1 | grep -q "serve" && \
    ok "CLI --help (via /usr/local/bin/serverlens wrapper)" || fail "Wrapper --help broken"

/opt/serverlens/venv/bin/python -m serverlens validate-config --config /etc/serverlens/config.yaml 2>&1 && \
    ok "validate-config" || fail "validate-config failed"

TOKEN_OUT=$(/opt/serverlens/venv/bin/python -m serverlens token generate 2>&1)
echo "$TOKEN_OUT" | grep -q "Token:" && ok "token generate" || fail "token generate failed"

# ═══ TEST 7: MCP stdio test ═══
echo -e "\n${BOLD}[TEST 7] MCP stdio protocol${NC}\n"

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
NOTIF='{"jsonrpc":"2.0","method":"notifications/initialized"}'
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

RESULT=$(echo -e "${INIT}\n${NOTIF}\n${LIST}" | \
    /opt/serverlens/venv/bin/python -m serverlens serve \
    --config /etc/serverlens/config.yaml --stdio 2>/dev/null)

echo "$RESULT" | head -1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert data['result']['serverInfo']['name'] == 'ServerLens', 'Wrong server name'
print('  ✓ Initialize response OK')
" 2>&1 || fail "Initialize response broken"

echo "$RESULT" | tail -1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
tools = data['result']['tools']
names = [t['name'] for t in tools]
print(f'  ✓ tools/list returned {len(tools)} tools: {names[:5]}...')
" 2>&1 || fail "tools/list response broken"

# ═══ TEST 7b: Wrapper stdio test ═══
echo -e "\n${BOLD}[TEST 7b] Wrapper: serverlens serve --stdio${NC}\n"

WRAPPER_RESULT=$(echo -e "${INIT}\n${NOTIF}\n${LIST}" | \
    serverlens serve --config /etc/serverlens/config.yaml --stdio 2>/dev/null)

echo "$WRAPPER_RESULT" | head -1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert data['result']['serverInfo']['name'] == 'ServerLens'
print('  ✓ Wrapper initialize OK')
" 2>&1 || fail "Wrapper initialize broken"

# ═══ TEST 8: Tool calls ═══
echo -e "\n${BOLD}[TEST 8] Tool execution${NC}\n"

LOGS_LIST='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"logs_list","arguments":{}}}'
SYS_OVERVIEW='{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"system_overview","arguments":{}}}'

TOOL_RESULT=$(echo -e "${INIT}\n${NOTIF}\n${LOGS_LIST}\n${SYS_OVERVIEW}" | \
    /opt/serverlens/venv/bin/python -m serverlens serve \
    --config /etc/serverlens/config.yaml --stdio 2>/dev/null)

echo "$TOOL_RESULT" | sed -n '2p' | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data['result']['content'][0]['text']
parsed = json.loads(content)
if isinstance(parsed, list):
    print(f'  ✓ logs_list returned {len(parsed)} sources')
elif isinstance(parsed, dict):
    print(f'  ✓ logs_list returned: {list(parsed.keys())[:3]}...')
" 2>&1 || fail "logs_list call failed"

echo "$TOOL_RESULT" | sed -n '3p' | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data['result']['content'][0]['text']
parsed = json.loads(content)
print(f'  ✓ system_overview: uptime present = {\"uptime\" in parsed}')
" 2>&1 || fail "system_overview call failed"

# ═══ SUMMARY ═══
echo -e "\n${BOLD}═══════════════════════════════════════════${NC}"
if (( ERRORS == 0 )); then
    echo -e "${GREEN}${BOLD}  ALL TESTS PASSED${NC}"
else
    echo -e "${RED}${BOLD}  FAILURES: ${ERRORS}${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════${NC}\n"

exit $ERRORS
