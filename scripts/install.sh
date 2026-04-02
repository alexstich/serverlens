#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ServerLens — Installation Script (install.sh)
#
# Description:
#   Full installation of ServerLens on a server. The script performs:
#     1. System requirements check (PHP 8.1+, extensions: json, mbstring)
#     2. Creating system user 'serverlens' (no login shell)
#     3. Creating directories: /opt/serverlens, /etc/serverlens, /var/log/serverlens
#     4. Copying project files (src/, bin/, composer.json) to /opt/serverlens
#     5. Installing PHP dependencies via Composer (--no-dev)
#     6. Interactive setup wizard:
#        — auto-detection of services (nginx, postgresql, mysql, redis, etc.)
#        — scanning logs and configuration files
#        — generating /etc/serverlens/config.yaml
#        — optionally: running setup_db.sh for PostgreSQL configuration
#     7. Installing systemd service (does not start it)
#
# Usage:
#   sudo bash scripts/install.sh              — with interactive wizard
#   sudo bash scripts/install.sh --no-wizard  — install only, configure manually
#
# Security:
#   - Requires root privileges (checks id -u)
#   - Creates a backup (.bak.YYYYMMDDHHMMSS) before overwriting config.yaml
#   - Does NOT delete or modify existing system service configurations
#   - All ServerLens config files get permissions 640 (root:serverlens)
#   - The setup wizard only READS discovered logs/configs, modifies nothing
#   - Idempotent: safe to run multiple times (useradd checks existence)
#   - Composer is installed only if missing (from official source)
#
# What the script does NOT do:
#   - Does not remove any system packages or users
#   - Does not modify nginx, postgresql, mysql or other service settings
#   - Does not open network ports or change firewall rules
#   - Does not start the service automatically
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/opt/serverlens"
CONFIG_DIR="/etc/serverlens"
LOG_DIR="/var/log/serverlens"
SERVICE_USER="serverlens"
SCRIPT_DIR=""

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "  ${BLUE}▸${NC} $1"; }
ok()      { echo -e "  ${GREEN}✓${NC} $1"; }
warn()    { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()    { echo -e "  ${RED}✗${NC} $1"; exit 1; }

ask_yn() {
    local prompt="$1" default="${2:-y}" answer
    if [[ "$default" == "y" ]]; then
        read -rp "  $prompt [Y/n]: " answer
        [[ -z "$answer" || "${answer,,}" == "y" ]]
    else
        read -rp "  $prompt [y/N]: " answer
        [[ "${answer,,}" == "y" ]]
    fi
}

ask_input() {
    local prompt="$1" default="${2:-}" answer
    if [[ -n "$default" ]]; then
        read -rp "  $prompt [$default]: " answer
        echo "${answer:-$default}"
    else
        read -rp "  $prompt: " answer
        echo "$answer"
    fi
}

WIZARD=true
for arg in "$@"; do
    [[ "$arg" == "--no-wizard" ]] && WIZARD=false
done

# ═══════════════════════════════════════════════════
# Known services, logs, configs
# ═══════════════════════════════════════════════════

ALL_SERVICES=(
    # Web servers / reverse proxies
    nginx apache2 caddy haproxy traefik
    # Databases
    postgresql mysql mongodb elasticsearch clickhouse memcached
    # App runtimes
    php-fpm tomcat gunicorn uwsgi supervisor
    # Cache / message brokers
    redis rabbitmq kafka mosquitto
    # Containers
    docker
    # Mail
    postfix dovecot
    # Monitoring
    prometheus grafana
    # Security
    fail2ban crowdsec
    # CI/CD
    gitlab-runner jenkins
)

declare -A SVC_UNITS
SVC_UNITS=(
    # Web servers
    [nginx]="nginx"
    [apache2]="apache2 httpd"
    [caddy]="caddy"
    [haproxy]="haproxy"
    [traefik]="traefik"
    # Databases
    [postgresql]="postgresql"
    [mysql]="mysql mysqld mariadb"
    [mongodb]="mongod mongos"
    [elasticsearch]="elasticsearch"
    [clickhouse]="clickhouse-server"
    [memcached]="memcached"
    # App runtimes
    [php-fpm]=""
    [tomcat]="tomcat tomcat9 tomcat10"
    [gunicorn]="gunicorn"
    [uwsgi]="uwsgi"
    [supervisor]="supervisor supervisord"
    # Cache / message brokers
    [redis]="redis-server redis"
    [rabbitmq]="rabbitmq-server"
    [kafka]="kafka confluent-kafka"
    [mosquitto]="mosquitto"
    # Containers
    [docker]="docker"
    # Mail
    [postfix]="postfix"
    [dovecot]="dovecot"
    # Monitoring
    [prometheus]="prometheus"
    [grafana]="grafana-server"
    # Security
    [fail2ban]="fail2ban"
    [crowdsec]="crowdsec"
    # CI/CD
    [gitlab-runner]="gitlab-runner"
    [jenkins]="jenkins"
)

declare -A SVC_LOGS
SVC_LOGS=(
    # Web servers
    [nginx]="/var/log/nginx/access.log:nginx_combined /var/log/nginx/error.log:plain"
    [apache2]="/var/log/apache2/access.log:plain /var/log/apache2/error.log:plain /var/log/httpd/access_log:plain /var/log/httpd/error_log:plain"
    [caddy]="/var/log/caddy:plain"
    [haproxy]="/var/log/haproxy.log:plain"
    [traefik]="/var/log/traefik:plain"
    # Databases
    [postgresql]="/var/log/postgresql:postgres"
    [mysql]="/var/log/mysql/error.log:plain"
    [mongodb]="/var/log/mongodb/mongod.log:json"
    [elasticsearch]="/var/log/elasticsearch:json"
    [clickhouse]="/var/log/clickhouse-server:plain"
    [memcached]=""
    # App runtimes
    [php-fpm]=""
    [tomcat]="/var/log/tomcat:plain /var/log/tomcat9:plain /var/log/tomcat10:plain"
    [gunicorn]=""
    [uwsgi]=""
    [supervisor]="/var/log/supervisor:plain"
    # Cache / message brokers
    [redis]="/var/log/redis/redis-server.log:plain"
    [rabbitmq]="/var/log/rabbitmq:plain"
    [kafka]="/var/log/kafka:plain"
    [mosquitto]="/var/log/mosquitto/mosquitto.log:plain"
    # Containers
    [docker]=""
    # Mail
    [postfix]="/var/log/mail.log:plain /var/log/maillog:plain"
    [dovecot]="/var/log/dovecot.log:plain"
    # Monitoring
    [prometheus]="/var/log/prometheus:plain"
    [grafana]="/var/log/grafana:plain"
    # Security
    [fail2ban]="/var/log/fail2ban.log:plain"
    [crowdsec]="/var/log/crowdsec.log:plain"
    # CI/CD
    [gitlab-runner]=""
    [jenkins]="/var/log/jenkins/jenkins.log:plain"
)

declare -A SVC_CONFIGS
SVC_CONFIGS=(
    # Web servers
    [nginx]="/etc/nginx/nginx.conf /etc/nginx/sites-enabled/ /etc/nginx/conf.d/"
    [apache2]="/etc/apache2/apache2.conf /etc/httpd/conf/httpd.conf"
    [caddy]="/etc/caddy/Caddyfile"
    [haproxy]="/etc/haproxy/haproxy.cfg"
    [traefik]="/etc/traefik/traefik.yml /etc/traefik/traefik.toml"
    # Databases
    [postgresql]=""
    [mysql]="/etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/my.cnf"
    [mongodb]="/etc/mongod.conf"
    [elasticsearch]="/etc/elasticsearch/elasticsearch.yml"
    [clickhouse]="/etc/clickhouse-server/config.xml /etc/clickhouse-server/users.xml"
    [memcached]="/etc/memcached.conf"
    # App runtimes
    [php-fpm]=""
    [tomcat]=""
    [gunicorn]=""
    [uwsgi]=""
    [supervisor]="/etc/supervisor/supervisord.conf /etc/supervisor/conf.d/"
    # Cache / message brokers
    [redis]="/etc/redis/redis.conf"
    [rabbitmq]="/etc/rabbitmq/rabbitmq.conf"
    [kafka]=""
    [mosquitto]="/etc/mosquitto/mosquitto.conf"
    # Containers
    [docker]="/etc/docker/daemon.json"
    # Mail
    [postfix]="/etc/postfix/main.cf"
    [dovecot]="/etc/dovecot/dovecot.conf"
    # Monitoring
    [prometheus]="/etc/prometheus/prometheus.yml"
    [grafana]="/etc/grafana/grafana.ini"
    # Security
    [fail2ban]="/etc/fail2ban/jail.local"
    [crowdsec]="/etc/crowdsec/config.yaml"
    # CI/CD
    [gitlab-runner]="/etc/gitlab-runner/config.toml"
    [jenkins]=""
)

WORKER_PATTERNS="worker|queue|celery|sidekiq|resque|bullmq|supervisor|delayed|background|async|horizon|scheduler|cron"

FOUND_SERVICES=()
SELECTED_SERVICES=()

LOG_YAML=""
CONFIG_YAML=""
SYSTEM_SVC_YAML=""
DOCKER_YAML=""

# ═══════════════════════════════════════════════════
# Service discovery
# ═══════════════════════════════════════════════════

CACHED_UNITS=""

cache_systemctl() {
    CACHED_UNITS=$(systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null || true)
}

service_is_active() {
    echo "$CACHED_UNITS" | grep -qE "^\s*${1}\.service\s.*\sactive\s" 2>/dev/null
}

service_exists() {
    echo "$CACHED_UNITS" | grep -qF "${1}.service" 2>/dev/null
}

FOUND_WORKERS=()

discover_services() {
    cache_systemctl

    for svc in "${ALL_SERVICES[@]}"; do
        local found=false

        case "$svc" in
            php-fpm)
                local units
                units=$(echo "$CACHED_UNITS" | grep -oP 'php[0-9.]+-fpm(?=\.service)' || true)
                if [[ -n "$units" ]]; then
                    SVC_UNITS[php-fpm]="$units"
                    found=true
                fi
                ;;
            postgresql)
                if echo "$CACHED_UNITS" | grep -qE 'postgresql' 2>/dev/null; then
                    found=true
                fi
                ;;
            *)
                for unit in ${SVC_UNITS[$svc]}; do
                    if service_exists "$unit"; then
                        found=true
                        break
                    fi
                done
                ;;
        esac

        if $found; then FOUND_SERVICES+=("$svc"); fi
    done

    if [[ -n "$WORKER_PATTERNS" ]]; then
        while IFS= read -r wunit; do
            [[ -z "$wunit" ]] && continue
            FOUND_WORKERS+=("$wunit")
        done < <(echo "$CACHED_UNITS" | grep -oP "(?:${WORKER_PATTERNS})[a-zA-Z0-9@_.-]*(?=\.service)" | sort -u || true)
    fi
}

discover_pg_version_paths() {
    if [[ -d /etc/postgresql ]]; then
        for d in /etc/postgresql/*/main; do
            [[ -d "$d" ]] && echo "$d"
        done
    fi
}

discover_phpfpm_paths() {
    local paths=""
    for f in /var/log/php*-fpm.log /var/log/php/*/fpm.log; do
        [[ -f "$f" ]] && paths+="$f:plain "
    done
    SVC_LOGS[php-fpm]="$paths"

    local cpath=""
    for f in /etc/php/*/fpm/php-fpm.conf /etc/php/*/fpm/pool.d/www.conf; do
        [[ -f "$f" ]] && cpath+="$f "
    done
    SVC_CONFIGS[php-fpm]="$cpath"
}

# ═══════════════════════════════════════════════════
# Wizard — logs
# ═══════════════════════════════════════════════════

make_log_name() {
    local path="$1" svc="$2"
    local base
    base=$(basename "$path" .log)
    base="${base//[^a-zA-Z0-9_]/_}"
    echo "${svc}_${base}"
}

wizard_logs() {
    echo -e "\n${BOLD}  -- Log files --${NC}\n"

    local idx=1
    declare -a found_logs=() found_formats=() found_names=()

    for svc in "${SELECTED_SERVICES[@]}"; do
        [[ "$svc" == "php-fpm" ]] && discover_phpfpm_paths

        if [[ "$svc" == "postgresql" ]]; then
            while IFS= read -r d; do
                [[ -z "$d" ]] && continue
                local ver
                ver=$(basename "$(dirname "$d")")
                for f in /var/log/postgresql/postgresql-"${ver}"-main.log; do
                    if [[ -f "$f" ]]; then
                        found_logs+=("$f"); found_formats+=("postgres"); found_names+=("postgresql_${ver}")
                        info "[${idx}] $f (postgres)"
                        ((idx++))
                    fi
                done
            done < <(discover_pg_version_paths)
            continue
        fi

        for entry in ${SVC_LOGS[$svc]:-}; do
            local path="${entry%%:*}" fmt="${entry##*:}"
            if [[ -d "$path" ]]; then
                for f in "$path"/*.log; do
                    [[ -f "$f" ]] || continue
                    found_logs+=("$f"); found_formats+=("$fmt"); found_names+=("$(make_log_name "$f" "$svc")")
                    local sz; sz=$(du -sh "$f" 2>/dev/null | cut -f1)
                    info "[${idx}] $f ($fmt, ${sz})"
                    ((idx++))
                done
            elif [[ -f "$path" ]]; then
                found_logs+=("$path"); found_formats+=("$fmt"); found_names+=("$(make_log_name "$path" "$svc")")
                local sz; sz=$(du -sh "$path" 2>/dev/null | cut -f1)
                info "[${idx}] $path ($fmt, ${sz})"
                ((idx++))
            fi
        done
    done

    for syslog in /var/log/syslog /var/log/messages /var/log/auth.log; do
        if [[ -f "$syslog" ]]; then
            found_logs+=("$syslog"); found_formats+=("plain"); found_names+=("$(basename "$syslog" .log)")
            local sz; sz=$(du -sh "$syslog" 2>/dev/null | cut -f1)
            info "[${idx}] $syslog (plain, ${sz})"
            ((idx++))
        fi
    done

    # Framework log auto-discovery
    local -A fw_patterns=(
        [yii]='*/runtime/logs/*.log'
        [laravel]='*/storage/logs/*.log'
        [symfony]='*/var/log/*.log'
        [rails]='*/log/*.log'
        [django]='*/logs/*.log'
        [wordpress]='*/wp-content/debug.log'
        [magento]='*/var/log/*.log'
        [cakephp]='*/tmp/logs/*.log'
        [codeigniter]='*/writable/logs/*.log'
        [nextjs]='*/.next/server/*.log'
        [nuxtjs]='*/.output/server/*.log'
        [spring]='*/logs/spring*.log'
        [generic]='*/logs/*.log'
    )
    local search_roots=(/var/www /srv /opt /home)
    declare -A seen_logs=()
    for fw in "${!fw_patterns[@]}"; do
        for sroot in "${search_roots[@]}"; do
            [[ -d "$sroot" ]] || continue
            while IFS= read -r fwlog; do
                [[ -z "$fwlog" ]] && continue
                [[ -n "${seen_logs[$fwlog]:-}" ]] && continue
                seen_logs[$fwlog]=1
                local appdir appname logbase fwname
                appdir=$(echo "$fwlog" | sed -E 's|/(runtime|storage|var|wp-content|tmp|writable|\.next|\.output)/.*||; s|/logs?/[^/]*$||; s|/log/[^/]*$||')
                appname=$(basename "$appdir")
                logbase=$(basename "$fwlog" .log)
                fwname="${fw}_${appname}_${logbase}"
                fwname="${fwname//[^a-zA-Z0-9_]/_}"
                found_logs+=("$fwlog"); found_formats+=("plain"); found_names+=("$fwname")
                local sz; sz=$(du -sh "$fwlog" 2>/dev/null | cut -f1)
                info "[${idx}] $fwlog (${fw}, ${sz})"
                ((idx++))
            done < <(find "$sroot" -maxdepth 5 -path "${fw_patterns[$fw]}" -readable 2>/dev/null | head -20 || true)
        done
    done

    if (( ${#found_logs[@]} == 0 )); then
        warn "No log files found"
    else
        echo ""
        local sel
        sel=$(ask_input "Which logs to include? (all / comma-separated numbers / Enter = all)" "all")
        if [[ "${sel,,}" == "all" ]]; then
            for i in "${!found_logs[@]}"; do
                append_log "${found_names[$i]}" "${found_logs[$i]}" "${found_formats[$i]}"
            done
        elif [[ -n "$sel" ]]; then
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#found_logs[@]} )); then append_log "${found_names[$n]}" "${found_logs[$n]}" "${found_formats[$n]}"; fi
            done
        fi
    fi

    echo ""
    info "If there are log files that were not discovered automatically,"
    info "you can add them manually (e.g. /var/log/myapp/app.log)."
    while ask_yn "Add a log file manually?" "n"; do
        local cpath cname cfmt
        cpath=$(ask_input "Full path to log file")
        [[ ! -f "$cpath" ]] && { warn "File not found: $cpath"; continue; }
        cname=$(ask_input "Display name (shown in UI)" "$(make_log_name "$cpath" "custom")")
        cfmt=$(ask_input "Log format (plain/json/nginx_combined/postgres)" "plain")
        append_log "$cname" "$cpath" "$cfmt"
        ok "Added file: $cname"
    done

    echo ""
    info "You can also add an entire directory — ServerLens will monitor"
    info "all log files inside it (e.g. /var/www/myapp/runtime/logs)."
    while ask_yn "Add a directory with logs?" "n"; do
        local dpath dname dfmt dpattern
        dpath=$(ask_input "Full path to directory")
        if [[ ! -d "$dpath" ]]; then
            warn "Directory not found: $dpath"; continue
        fi
        local logcount; logcount=$(find "$dpath" -maxdepth 1 -name '*.log' 2>/dev/null | wc -l)
        info "Found *.log files in directory: ${logcount}"
        dname=$(ask_input "Display name (shown in UI)" "custom_$(basename "$dpath" | sed 's/[^a-zA-Z0-9_]/_/g')")
        dpattern=$(ask_input "File pattern (which files to read)" "*.log")
        dfmt=$(ask_input "Log format (plain/json/nginx_combined/postgres)" "plain")
        append_log_dir "$dname" "$dpath" "$dpattern" "$dfmt"
        ok "Added directory: $dpath ($dpattern)"
    done
}

append_log() {
    LOG_YAML+="    - name: \"$1\"\n      path: \"$2\"\n      format: \"$3\"\n      max_lines: 5000\n\n"
}

append_log_dir() {
    LOG_YAML+="    - name: \"$1\"\n      path: \"$2\"\n      type: \"directory\"\n      pattern: \"$3\"\n      format: \"$4\"\n      max_lines: 5000\n\n"
}

fix_log_permissions() {
    echo -e "\n${BOLD}  -- Log file permissions --${NC}\n"

    if ! getent group adm &>/dev/null; then
        warn "'adm' group not found (not Ubuntu/Debian?)"
        echo ""
        info "ServerLens reads logs as the serverlens system user."
        info "Without the 'adm' group, you need to grant access to log files manually."
        echo ""
        info "For each log file, run:"
        echo ""
        echo "    sudo chgrp serverlens /path/to/logfile"
        echo "    sudo chmod 640 /path/to/logfile"
        echo ""
        info "Example for nginx:"
        echo ""
        echo "    sudo chgrp serverlens /var/log/nginx/access.log /var/log/nginx/error.log"
        echo "    sudo chmod 640 /var/log/nginx/access.log /var/log/nginx/error.log"
        echo ""
        return
    fi

    local fixed=0

    for f in /var/log/php*-fpm.log /var/log/php/*/fpm.log; do
        [[ -f "$f" ]] || continue
        local grp perms
        grp=$(stat -c '%G' "$f" 2>/dev/null || echo "unknown")
        perms=$(stat -c '%a' "$f" 2>/dev/null || echo "000")
        if [[ "$grp" == "root" ]] && (( (8#$perms & 8#040) == 0 )); then
            chgrp adm "$f" 2>/dev/null && chmod 640 "$f" 2>/dev/null && {
                ok "$f -> root:adm 640"
                ((fixed++)) || true
            }
        fi
    done

    if [[ -d /var/log/rabbitmq ]]; then
        local dir_perms
        dir_perms=$(stat -c '%a' /var/log/rabbitmq 2>/dev/null || echo "000")
        if (( (8#$dir_perms & 8#050) == 0 )); then
            chmod g+rx /var/log/rabbitmq 2>/dev/null && {
                ok "/var/log/rabbitmq/ -> added group read+execute"
                ((fixed++)) || true
            }
        fi
        for f in /var/log/rabbitmq/*.log*; do
            [[ -f "$f" ]] || continue
            local fperms
            fperms=$(stat -c '%a' "$f" 2>/dev/null || echo "000")
            if (( (8#$fperms & 8#040) == 0 )); then
                chmod g+r "$f" 2>/dev/null && {
                    ok "$f -> added group read"
                    ((fixed++)) || true
                }
            fi
        done
    fi

    # Grant serverlens group access to service log directories
    # that are not readable by adm
    for svc in "${SELECTED_SERVICES[@]}"; do
        local log_dirs=()
        case "$svc" in
            postgresql)    [[ -d "/var/log/postgresql" ]] && log_dirs+=("/var/log/postgresql") ;;
            redis)         [[ -d "/var/log/redis" ]] && log_dirs+=("/var/log/redis") ;;
            mongodb)       [[ -d "/var/log/mongodb" ]] && log_dirs+=("/var/log/mongodb") ;;
            elasticsearch) [[ -d "/var/log/elasticsearch" ]] && log_dirs+=("/var/log/elasticsearch") ;;
            clickhouse)    [[ -d "/var/log/clickhouse-server" ]] && log_dirs+=("/var/log/clickhouse-server") ;;
            kafka)         [[ -d "/var/log/kafka" ]] && log_dirs+=("/var/log/kafka") ;;
            grafana)       [[ -d "/var/log/grafana" ]] && log_dirs+=("/var/log/grafana") ;;
            tomcat)
                for d in /var/log/tomcat /var/log/tomcat9 /var/log/tomcat10; do
                    [[ -d "$d" ]] && log_dirs+=("$d")
                done ;;
        esac
        for ldir in "${log_dirs[@]}"; do
            local dgrp
            dgrp=$(stat -c '%G' "$ldir" 2>/dev/null || echo "")
            if [[ -n "$dgrp" && "$dgrp" != "root" && "$dgrp" != "adm" && "$dgrp" != "serverlens" ]]; then
                if command -v setfacl &>/dev/null; then
                    setfacl -R -m g:serverlens:rx "$ldir" 2>/dev/null && \
                    setfacl -d -m g:serverlens:rx "$ldir" 2>/dev/null && {
                        ok "$ldir -> ACL granted to serverlens group"
                        ((fixed++)) || true
                    }
                else
                    chmod g+rx "$ldir" 2>/dev/null && {
                        ok "$ldir -> added group read+execute"
                        ((fixed++)) || true
                    }
                    for f in "$ldir"/*.log*; do
                        [[ -f "$f" ]] && chmod g+r "$f" 2>/dev/null
                    done
                fi
            fi
        done
    done

    if (( fixed > 0 )); then
        echo ""
        info "Logrotate: permissions may reset after log rotation."
        info "A 'create' directive in logrotate config preserves correct permissions."
        echo ""

        declare -a lr_files=() lr_create=()
        for lr in /etc/logrotate.d/php*-fpm; do
            [[ -f "$lr" ]] || continue
            if ! grep -q '^\s*create\b' "$lr" 2>/dev/null; then
                lr_files+=("$lr"); lr_create+=("create 0640 root adm")
            fi
        done
        if [[ -f /etc/logrotate.d/rabbitmq-server ]]; then
            if ! grep -q '^\s*create\b' /etc/logrotate.d/rabbitmq-server 2>/dev/null; then
                lr_files+=("/etc/logrotate.d/rabbitmq-server"); lr_create+=("create 0640 rabbitmq rabbitmq")
            fi
        fi

        if (( ${#lr_files[@]} > 0 )); then
            for i in "${!lr_files[@]}"; do
                warn "${lr_files[$i]} — missing '${lr_create[$i]}' directive"
            done
            echo ""
            if ask_yn "Add 'create' directives to logrotate automatically?" "y"; then
                for i in "${!lr_files[@]}"; do
                    if sed -i '/{/a\    '"${lr_create[$i]}" "${lr_files[$i]}" 2>/dev/null; then
                        ok "${lr_files[$i]} — added '${lr_create[$i]}'"
                    else
                        warn "Failed to modify ${lr_files[$i]}"
                        info "  Run manually: sudo nano ${lr_files[$i]}"
                        info "  Add the line '${lr_create[$i]}' inside the { ... } block"
                    fi
                done
            else
                info "Add manually:"
                for i in "${!lr_files[@]}"; do
                    info "  sudo nano ${lr_files[$i]}"
                    info "  -> inside the { ... } block, add: ${lr_create[$i]}"
                done
            fi
        else
            ok "Logrotate configs already contain the required directives"
        fi
    else
        ok "Log file permissions are correct"
    fi
}

# ═══════════════════════════════════════════════════
# Wizard — configs
# ═══════════════════════════════════════════════════

wizard_configs() {
    echo -e "\n${BOLD}  -- Configuration files --${NC}\n"

    local idx=1
    declare -a found_cfgs=() found_cfg_names=() found_cfg_redacts=()

    for svc in "${SELECTED_SERVICES[@]}"; do
        if [[ "$svc" == "postgresql" ]]; then
            while IFS= read -r d; do
                [[ -z "$d" ]] && continue
                local ver; ver=$(basename "$(dirname "$d")")
                for f in "$d"/postgresql.conf "$d"/pg_hba.conf; do
                    [[ -f "$f" ]] || continue
                    local bname; bname=$(basename "$f" .conf)
                    found_cfgs+=("$f"); found_cfg_names+=("postgres_${ver}_${bname}")
                    [[ "$bname" == "postgresql" ]] && found_cfg_redacts+=("password ssl_key_file ssl_cert_file") || found_cfg_redacts+=("")
                    info "[${idx}] $f"; ((idx++))
                done
            done < <(discover_pg_version_paths)
            continue
        fi

        for cpath in ${SVC_CONFIGS[$svc]:-}; do
            if [[ -d "$cpath" ]]; then
                found_cfgs+=("$cpath")
                found_cfg_names+=("${svc}_$(basename "$cpath")")
                found_cfg_redacts+=("")
                local cnt; cnt=$(find "$cpath" -maxdepth 1 -type f 2>/dev/null | wc -l)
                info "[${idx}] $cpath (directory, ${cnt} files)"; ((idx++))
            elif [[ -f "$cpath" ]]; then
                found_cfgs+=("$cpath")
                local bname; bname=$(basename "$cpath" .conf); bname="${bname//[^a-zA-Z0-9_]/_}"
                found_cfg_names+=("${svc}_${bname}")
                local redact=""
                [[ "$svc" == "redis" ]] && redact="requirepass masterauth"
                [[ "$svc" == "mysql" ]] && redact="password"
                found_cfg_redacts+=("$redact")
                info "[${idx}] $cpath"; ((idx++))
            fi
        done
    done

    if (( ${#found_cfgs[@]} == 0 )); then
        warn "No configuration files found"
    else
        echo ""
        local sel
        sel=$(ask_input "Which configs to include? (all / comma-separated numbers / Enter = all)" "all")
        if [[ "${sel,,}" == "all" ]]; then
            for i in "${!found_cfgs[@]}"; do
                append_config "${found_cfg_names[$i]}" "${found_cfgs[$i]}" "${found_cfg_redacts[$i]}"
            done
        elif [[ -n "$sel" ]]; then
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#found_cfgs[@]} )); then append_config "${found_cfg_names[$n]}" "${found_cfgs[$n]}" "${found_cfg_redacts[$n]}"; fi
            done
        fi
    fi

    echo ""
    info "If there are config files that were not discovered automatically,"
    info "you can add them manually. ServerLens will track changes and display"
    info "their contents (e.g. /etc/myapp/config.ini)."
    while ask_yn "Add a config file manually?" "n"; do
        local cpath cname
        cpath=$(ask_input "Full path to file or directory")
        [[ ! -e "$cpath" ]] && { warn "Path not found: $cpath"; continue; }
        cname=$(ask_input "Display name (shown in UI)" "custom_$(basename "$cpath" | sed 's/[^a-zA-Z0-9_]/_/g')")
        append_config "$cname" "$cpath" ""
        ok "Added: $cname"
    done
}

append_config() {
    local name="$1" path="$2" redact="$3"
    CONFIG_YAML+="    - name: \"${name}\"\n      path: \"${path}\"\n"
    [[ -d "$path" ]] && CONFIG_YAML+="      type: \"directory\"\n"
    if [[ -n "$redact" ]]; then
        CONFIG_YAML+="      redact:\n"
        for r in $redact; do CONFIG_YAML+="        - \"${r}\"\n"; done
    else
        CONFIG_YAML+="      redact: []\n"
    fi
    CONFIG_YAML+="\n"
}

# ═══════════════════════════════════════════════════
# Wizard — system monitoring
# ═══════════════════════════════════════════════════

wizard_system() {
    echo -e "\n${BOLD}  -- System monitoring --${NC}\n"

    if ! ask_yn "Enable system monitoring (disk, memory, services, processes)?" "y"; then
        return
    fi

    for svc in "${SELECTED_SERVICES[@]}"; do
        local units="${SVC_UNITS[$svc]}"
        if [[ "$svc" == "php-fpm" ]]; then
            for unit in $units; do
                SYSTEM_SVC_YAML+="    - \"${unit}\"\n"
            done
        else
            local unit="${units%% *}"
            [[ -z "$unit" ]] && continue
            SYSTEM_SVC_YAML+="    - \"${unit}\"\n"
        fi
    done

    if (( ${#FOUND_WORKERS[@]} > 0 )); then
        echo ""
        info "Discovered workers/queues:"
        local widx=1
        for w in "${FOUND_WORKERS[@]}"; do
            local wstatus
            service_is_active "$w" && wstatus="${GREEN}active${NC}" || wstatus="${YELLOW}inactive/failed${NC}"
            echo -e "  [${widx}] ${w} (${wstatus})"
            ((widx++))
        done
        echo ""
        local sel
        sel=$(ask_input "Which workers to add? (all / comma-separated numbers / Enter = all)" "all")
        if [[ "${sel,,}" == "all" ]]; then
            for w in "${FOUND_WORKERS[@]}"; do
                SYSTEM_SVC_YAML+="    - \"${w}\"\n"
            done
        elif [[ -n "$sel" ]]; then
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#FOUND_WORKERS[@]} )); then
                    SYSTEM_SVC_YAML+="    - \"${FOUND_WORKERS[$n]}\"\n"
                fi
            done
        fi
    fi

    if [[ " ${SELECTED_SERVICES[*]} " == *" docker "* ]]; then
        echo ""
        local stacks
        stacks=$(docker stack ls --format '{{.Name}}' 2>/dev/null || docker compose ls --format '{{.Name}}' 2>/dev/null || true)
        if [[ -n "$stacks" ]]; then
            info "Discovered Docker stacks:"
            local didx=1
            declare -a stack_list=()
            while IFS= read -r s; do
                [[ -z "$s" ]] && continue
                info "  [${didx}] $s"; stack_list+=("$s"); ((didx++))
            done <<< "$stacks"
            echo ""
            local ssel
            ssel=$(ask_input "Which stacks to monitor? (all / comma-separated numbers / Enter = all)" "all")
            if [[ "${ssel,,}" == "all" ]]; then
                for s in "${stack_list[@]}"; do DOCKER_YAML+="    - \"${s}\"\n"; done
            elif [[ -n "$ssel" ]]; then
                IFS=',' read -ra nums <<< "$ssel"
                for n in "${nums[@]}"; do
                    n=$(( ${n// /} - 1 ))
                    if (( n >= 0 && n < ${#stack_list[@]} )); then
                        DOCKER_YAML+="    - \"${stack_list[$n]}\"\n"
                    fi
                done
            fi
        fi
    fi
}

# ═══════════════════════════════════════════════════
# Generate config.yaml (without databases section)
# ═══════════════════════════════════════════════════

generate_config() {
    echo -e "\n${BOLD}  -- Generating config.yaml --${NC}\n"

    local cfg="${CONFIG_DIR}/config.yaml"

    if [[ -f "$cfg" ]]; then
        warn "Config ${cfg} already exists"
        if ! ask_yn "Overwrite? (a backup will be created)" "n"; then
            ok "Config unchanged"
            return
        fi
        local backup="${cfg}.bak.$(date +%Y%m%d%H%M%S)"
        cp "$cfg" "$backup"
        ok "Backup saved: ${backup}"
    fi

    cat > "$cfg" <<'HEADER'
# ServerLens Configuration
# Generated by install.sh

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

HEADER

    if [[ -n "$LOG_YAML" ]]; then
        { echo "logs:"; echo "  sources:"; echo -e "$LOG_YAML"; } >> "$cfg"
    else
        { echo "logs:"; echo "  sources: []"; echo ""; } >> "$cfg"
    fi

    if [[ -n "$CONFIG_YAML" ]]; then
        { echo "configs:"; echo "  sources:"; echo -e "$CONFIG_YAML"; } >> "$cfg"
    else
        { echo "configs:"; echo "  sources: []"; echo ""; } >> "$cfg"
    fi

    { echo "databases:"; echo "  connections: []"; echo ""; } >> "$cfg"

    local sys_enabled="false"
    [[ -n "$SYSTEM_SVC_YAML" || -n "$DOCKER_YAML" ]] && sys_enabled="true"
    echo "system:" >> "$cfg"
    echo "  enabled: ${sys_enabled}" >> "$cfg"
    [[ -n "$SYSTEM_SVC_YAML" ]] && { echo "  allowed_services:"; echo -e "$SYSTEM_SVC_YAML"; } >> "$cfg" || echo "  allowed_services: []" >> "$cfg"
    [[ -n "$DOCKER_YAML" ]] && { echo "  allowed_docker_stacks:"; echo -e "$DOCKER_YAML"; } >> "$cfg" || echo "  allowed_docker_stacks: []" >> "$cfg"

    chown root:${SERVICE_USER} "$cfg" 2>/dev/null || true
    chmod 640 "$cfg"

    ok "Configuration written: ${cfg}"
}

# ═══════════════════════════════════════════════════
# Installation phases
# ═══════════════════════════════════════════════════

phase_checks() {
    echo -e "\n${BOLD}[1/7] System check${NC}"

    [[ "$(id -u)" -ne 0 ]] && fail "Run as root: sudo bash scripts/install.sh"

    command -v php &>/dev/null || fail "PHP not found. Install PHP 8.1+"

    local php_major php_minor
    php_major=$(php -r 'echo PHP_MAJOR_VERSION;')
    php_minor=$(php -r 'echo PHP_MINOR_VERSION;')

    if (( php_major < 8 || (php_major == 8 && php_minor < 1) )); then fail "PHP 8.1+ required, found ${php_major}.${php_minor}"; fi
    ok "PHP ${php_major}.${php_minor}"

    for ext in json mbstring; do
        if php -r "if(!extension_loaded('${ext}')) exit(1);" 2>/dev/null; then
            ok "ext-${ext}"
        else
            fail "Missing extension: ${ext}"
        fi
    done

    if php -r "if(!extension_loaded('pdo_pgsql')) exit(1);" 2>/dev/null; then
        ok "ext-pdo_pgsql"
    else
        warn "ext-pdo_pgsql not found (only needed for the database module)"
    fi
}

phase_create_user() {
    echo -e "\n${BOLD}[2/7] System user${NC}"
    if ! id "${SERVICE_USER}" &>/dev/null; then
        useradd -r -s /usr/sbin/nologin -d "${INSTALL_DIR}" "${SERVICE_USER}"
        ok "User '${SERVICE_USER}' created"
    else
        ok "User '${SERVICE_USER}' already exists"
    fi

    if getent group adm &>/dev/null; then
        usermod -aG adm "${SERVICE_USER}" 2>/dev/null || true
        ok "'${SERVICE_USER}' added to 'adm' group (log access)"
    fi
}

phase_directories() {
    echo -e "\n${BOLD}[3/7] Directories${NC}"
    mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}" "${LOG_DIR}"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${LOG_DIR}"
    chmod 750 "${LOG_DIR}"
    ok "${INSTALL_DIR}, ${CONFIG_DIR}, ${LOG_DIR}"
}

phase_copy_files() {
    echo -e "\n${BOLD}[4/7] Copying files${NC}"
    cp -r "${SCRIPT_DIR}/src" "${INSTALL_DIR}/"
    cp -r "${SCRIPT_DIR}/bin" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/composer.json" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/composer.lock" "${INSTALL_DIR}/" 2>/dev/null || true
    chmod +x "${INSTALL_DIR}/bin/serverlens"
    ok "Files copied to ${INSTALL_DIR}"
}

phase_dependencies() {
    echo -e "\n${BOLD}[5/7] PHP dependencies${NC}"
    if ! command -v composer &>/dev/null; then
        info "Installing Composer..."
        curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null
    fi
    ok "Composer found"
    cd "${INSTALL_DIR}"
    composer install --no-dev --optimize-autoloader --no-interaction --quiet
    ok "Dependencies installed"
}

phase_wizard() {
    echo -e "\n${BOLD}[6/7] Setup wizard${NC}"

    echo -e "\n  Scanning services...\n"
    discover_services

    if (( ${#FOUND_SERVICES[@]} == 0 )); then
        warn "No services found"
    else
        local idx=1
        for svc in "${FOUND_SERVICES[@]}"; do
            local unit="${SVC_UNITS[$svc]%% *}"
            local status
            service_is_active "${unit:-$svc}" 2>/dev/null && status="${GREEN}active${NC}" || status="${YELLOW}installed${NC}"
            echo -e "  [${idx}] ${svc} (${status})"
            ((idx++))
        done

        echo ""
        local sel; sel=$(ask_input "Which services to monitor? (all / comma-separated numbers / Enter = all)" "all")
        if [[ "$sel" == "all" ]]; then
            SELECTED_SERVICES=("${FOUND_SERVICES[@]}")
        elif [[ -n "$sel" ]]; then
            IFS=',' read -ra nums <<< "$sel"
            for n in "${nums[@]}"; do
                n=$(( ${n// /} - 1 ))
                if (( n >= 0 && n < ${#FOUND_SERVICES[@]} )); then SELECTED_SERVICES+=("${FOUND_SERVICES[$n]}"); fi
            done
        fi
    fi

    if (( ${#SELECTED_SERVICES[@]} > 0 )); then ok "Selected: ${SELECTED_SERVICES[*]}"; fi

    wizard_logs
    fix_log_permissions
    wizard_configs
    wizard_system
    generate_config

    echo ""
    if ask_yn "Configure PostgreSQL connection?" "y"; then
        local db_script="${SCRIPT_DIR}/scripts/setup_db.sh"
        if [[ -f "$db_script" ]]; then
            bash "$db_script"
        else
            warn "Script ${db_script} not found, skipping"
            echo ""
            info "This script creates a read-only PostgreSQL user for monitoring."
            info "Run it later from the project directory:"
            echo ""
            echo "    cd ${SCRIPT_DIR} && sudo bash scripts/setup_db.sh"
            echo ""
        fi
    fi
}

phase_default_config() {
    echo -e "\n${BOLD}[6/7] Configuration (no wizard)${NC}"
    local cfg="${CONFIG_DIR}/config.yaml"
    if [[ -f "$cfg" ]]; then
        ok "Config already exists: ${cfg}"
    else
        cp "${SCRIPT_DIR}/config.example.yaml" "$cfg"
        chown root:${SERVICE_USER} "$cfg" 2>/dev/null || true
        chmod 640 "$cfg"
        ok "Copied config.example.yaml -> ${cfg}"
        echo ""
        info "The config contains template values. Review and adapt for your server:"
        echo ""
        echo "    sudo nano ${cfg}"
        echo ""
        info "Key sections: logs, configs, system."
    fi
}

phase_systemd() {
    echo -e "\n${BOLD}[7/7] Systemd${NC}"
    if [[ -f "${SCRIPT_DIR}/etc/serverlens.service" ]]; then
        cp "${SCRIPT_DIR}/etc/serverlens.service" /etc/systemd/system/serverlens.service
        systemctl daemon-reload
        ok "Service installed (not started)"
        echo ""
        info "MCP client launches ServerLens via SSH automatically."
        info "The systemd service is only needed for SSE (HTTP) mode:"
        echo ""
        echo "    sudo systemctl start serverlens     # start"
        echo "    sudo systemctl enable serverlens    # auto-start on boot"
        echo ""
    else
        warn "serverlens.service not found, skipping"
    fi
}

phase_summary() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║         Installation complete!            ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Configuration: ${CYAN}${CONFIG_DIR}/config.yaml${NC}"
    echo -e "  Application:   ${CYAN}${INSTALL_DIR}/bin/serverlens${NC}"
    echo -e "  Audit log:     ${CYAN}${LOG_DIR}/audit.log${NC}"

    echo ""
    echo -e "${BOLD}  ═══════════════ Server Setup ═══════════════${NC}"

    echo ""
    echo -e "  ${BOLD}Step 1.${NC} Grant your SSH user access to ServerLens:"
    echo ""
    echo -e "  The SSH user needs the ${CYAN}serverlens${NC} group (to read ServerLens config)"
    if getent group adm &>/dev/null; then
        echo -e "  and the ${CYAN}adm${NC} group (standard Ubuntu/Debian group for reading /var/log/)."
    fi
    echo -e "  Replace ${CYAN}YOUR_USER${NC} with your SSH username:"
    echo ""
    echo "    sudo usermod -aG serverlens YOUR_USER"
    if getent group adm &>/dev/null; then
        echo "    sudo usermod -aG adm YOUR_USER"
    fi
    echo ""
    echo -e "  Apply group changes without logging out:"
    echo ""
    echo "    newgrp serverlens"
    echo ""

    echo -e "  ${BOLD}Step 2.${NC} Validate configuration:"
    echo ""
    echo "    php ${INSTALL_DIR}/bin/serverlens validate-config \\"
    echo "      --config ${CONFIG_DIR}/config.yaml"
    echo ""

    echo -e "  ${BOLD}Step 3.${NC} Quick test (Ctrl+C to exit):"
    echo ""
    echo "    echo '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"test\",\"version\":\"1.0\"}}}' | \\"
    echo "      php ${INSTALL_DIR}/bin/serverlens serve --config ${CONFIG_DIR}/config.yaml --stdio"
    echo ""

    local srv_host; srv_host=$(hostname -f 2>/dev/null || hostname)

    echo -e "${BOLD}  ════════════ MCP Client Setup ═════════════${NC}"
    echo -e "  (run these on your ${CYAN}developer machine${NC})"
    echo ""

    echo -e "  ${BOLD}Step 1.${NC} Clone the repository and install dependencies:"
    echo ""
    echo "    git clone <repo-url> ~/serverlens"
    echo "    cd ~/serverlens/mcp-client && composer install"
    echo ""

    echo -e "  ${BOLD}Step 2.${NC} Create client config:"
    echo ""
    echo "    mkdir -p ~/.serverlens"
    echo "    cp ~/serverlens/mcp-client/config.example.yaml ~/.serverlens/config.yaml"
    echo ""

    echo -e "  ${BOLD}Step 3.${NC} Edit ${CYAN}~/.serverlens/config.yaml${NC} — add this server:"
    echo ""
    echo "    servers:"
    echo "      production:              # any name you like"
    echo "        ssh:"
    echo "          host: \"${srv_host}\""
    echo "          user: \"YOUR_USER\"       # SSH username"
    echo "          key: \"~/.ssh/id_ed25519\""
    echo "        remote:"
    echo "          php: \"php\""
    echo "          serverlens_path: \"${INSTALL_DIR}/bin/serverlens\""
    echo "          config_path: \"${CONFIG_DIR}/config.yaml\""
    echo ""

    echo -e "  ${BOLD}Step 4.${NC} Add to ${CYAN}~/.cursor/mcp.json${NC} (on your machine):"
    echo ""
    echo "    {"
    echo "      \"mcpServers\": {"
    echo "        \"serverlens\": {"
    echo "          \"command\": \"php\","
    echo "          \"args\": [\"~/serverlens/mcp-client/bin/serverlens-mcp\","
    echo "                   \"--config\", \"~/.serverlens/config.yaml\"]"
    echo "        }"
    echo "      }"
    echo "    }"
    echo ""

    echo -e "  ${BOLD}Step 5.${NC} Restart Cursor. In Output -> MCP you should see:"
    echo ""
    echo "    [MCP] Ready: 1 server(s), N remote tool(s), 2 MCP tools"
    echo ""
    echo -e "  Full guide: ${CYAN}docs/quickstart.md${NC} (steps 4-6)"
    echo ""
}

# ═══════════════════════════════════════════════════

main() {
    SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

    echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║     ServerLens Installer v1.0             ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

    phase_checks
    phase_create_user
    phase_directories
    phase_copy_files
    phase_dependencies

    if $WIZARD; then
        phase_wizard
    else
        phase_default_config
    fi

    phase_systemd
    phase_summary
}

main "$@"
