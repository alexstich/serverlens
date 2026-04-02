#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; ERRORS=$((ERRORS+1)); }
info() { echo -e "  ${YELLOW}▸${NC} $1"; }

ERRORS=0

echo -e "\n${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  Migration Test: PHP → Python${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"

# ═══ PHASE 1: Simulate PHP installation ═══
echo -e "\n${BOLD}[PHASE 1] Simulate existing PHP installation${NC}\n"

useradd -r -s /usr/sbin/nologin -d /opt/serverlens serverlens 2>/dev/null || true
usermod -aG adm serverlens 2>/dev/null || true
mkdir -p /opt/serverlens/{bin,src,vendor}
mkdir -p /etc/serverlens
mkdir -p /var/log/serverlens
chown serverlens:serverlens /var/log/serverlens

echo '#!/usr/bin/env php' > /opt/serverlens/bin/serverlens
echo '{}' > /opt/serverlens/composer.json
echo '{}' > /opt/serverlens/composer.lock
echo '<?php echo "hi";' > /opt/serverlens/src/Application.php
echo '<?php' > /opt/serverlens/vendor/autoload.php

cat > /etc/serverlens/config.yaml <<'EOF'
server:
  host: "127.0.0.1"
  port: 9600
  transport: "stdio"

auth:
  tokens: []
  max_failed_attempts: 5
  lockout_minutes: 15

rate_limiting:
  requests_per_minute: 60
  max_concurrent: 5

audit:
  enabled: true
  path: "/var/log/serverlens/audit.log"
  log_params: false
  retention_days: 90

logs:
  sources:
    - name: "nginx_access"
      path: "/var/log/nginx/access.log"
      format: "nginx_combined"
      max_lines: 5000
    - name: "nginx_error"
      path: "/var/log/nginx/error.log"
      format: "plain"
      max_lines: 5000
    - name: "syslog"
      path: "/var/log/syslog"
      format: "plain"
      max_lines: 5000
    - name: "auth"
      path: "/var/log/auth.log"
      format: "plain"
      max_lines: 5000

configs:
  sources:
    - name: "nginx_nginx_conf"
      path: "/etc/nginx/nginx.conf"
      redact: []

databases:
  connections: []

system:
  enabled: true
  allowed_services:
    - "nginx"
  allowed_docker_stacks: []
EOF

chown root:serverlens /etc/serverlens/config.yaml
chmod 640 /etc/serverlens/config.yaml

cat > /etc/systemd/system/serverlens.service <<'EOF'
[Unit]
Description=ServerLens MCP Server (PHP)

[Service]
ExecStart=/usr/bin/php /opt/serverlens/bin/serverlens serve --config /etc/serverlens/config.yaml
EOF

ok "PHP installation simulated"
[[ -f /opt/serverlens/composer.json ]] && ok "composer.json exists" || fail "No composer.json"
[[ -f /etc/serverlens/config.yaml ]] && ok "Config exists" || fail "No config"

# ═══ PHASE 2: Run migration ═══
echo -e "\n${BOLD}[PHASE 2] Run migration script${NC}\n"

bash /srv/serverlens/scripts/migrate-php-to-python.sh 2>&1 && \
    ok "Migration completed" || fail "Migration failed"

# ═══ PHASE 3: Verify migration ═══
echo -e "\n${BOLD}[PHASE 3] Verify migration results${NC}\n"

[[ ! -f /opt/serverlens/composer.json ]] && ok "PHP files removed" || fail "PHP files still present"
[[ ! -d /opt/serverlens/vendor ]] && ok "vendor/ removed" || fail "vendor/ still present"

[[ -f /opt/serverlens/serverlens/__main__.py ]] && ok "Python files installed" || fail "Python files missing"
[[ -d /opt/serverlens/venv ]] && ok "Virtual environment exists" || fail "No venv"
[[ -f /etc/serverlens/config.yaml ]] && ok "Config preserved" || fail "Config lost!"

ls /etc/serverlens/config.yaml.pre-migration.* &>/dev/null && ok "Backup exists" || fail "No backup"

# ═══ PHASE 4: Test Python service with preserved config ═══
echo -e "\n${BOLD}[PHASE 4] Test Python service${NC}\n"

/opt/serverlens/venv/bin/python -m serverlens validate-config --config /etc/serverlens/config.yaml 2>&1 && \
    ok "Config validation" || fail "Config validation failed"

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
NOTIF='{"jsonrpc":"2.0","method":"notifications/initialized"}'
CALL='{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"logs_list","arguments":{}}}'

RESULT=$(echo -e "${INIT}\n${NOTIF}\n${CALL}" | \
    /opt/serverlens/venv/bin/python -m serverlens serve --config /etc/serverlens/config.yaml --stdio 2>/dev/null)

echo "$RESULT" | head -1 | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['result']['serverInfo']['name'] == 'ServerLens'
print('  ✓ MCP initialize OK')
" 2>&1 || fail "MCP initialize failed"

echo "$RESULT" | sed -n '2p' | python3 -c "
import sys, json
d = json.load(sys.stdin)
sources = json.loads(d['result']['content'][0]['text'])
names = [s['name'] for s in sources]
assert 'nginx_access' in names, f'nginx_access not in {names}'
assert 'syslog' in names, f'syslog not in {names}'
print(f'  ✓ logs_list returned {len(sources)} sources (config preserved!)')
" 2>&1 || fail "logs_list with migrated config failed"

# ═══ SUMMARY ═══
echo -e "\n${BOLD}═══════════════════════════════════════════${NC}"
if (( ERRORS == 0 )); then
    echo -e "${GREEN}${BOLD}  MIGRATION TEST PASSED${NC}"
else
    echo -e "${RED}${BOLD}  FAILURES: ${ERRORS}${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════${NC}\n"

exit $ERRORS
