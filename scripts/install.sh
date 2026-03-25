#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/serverlens"
CONFIG_DIR="/etc/serverlens"
LOG_DIR="/var/log/serverlens"
SERVICE_USER="serverlens"

echo "=== ServerLens Installation ==="
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: run as root (sudo)"
    exit 1
fi

echo "[1/6] Creating system user: ${SERVICE_USER}"
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "${INSTALL_DIR}" "${SERVICE_USER}"
    echo "  User created"
else
    echo "  User already exists"
fi

echo "[2/6] Creating directories"
mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}" "${LOG_DIR}"
chown "${SERVICE_USER}:${SERVICE_USER}" "${LOG_DIR}"
chmod 750 "${LOG_DIR}"

echo "[3/6] Copying files"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp -r "${SCRIPT_DIR}/src" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/bin" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/composer.json" "${INSTALL_DIR}/"
chmod +x "${INSTALL_DIR}/bin/serverlens"

echo "[4/6] Installing PHP dependencies"
if ! command -v php &>/dev/null; then
    echo "  Error: PHP not found. Install PHP 8.1+"
    exit 1
fi

PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION . "." . PHP_MINOR_VERSION;')
echo "  PHP version: ${PHP_VERSION}"

if ! command -v composer &>/dev/null; then
    echo "  Installing Composer..."
    curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
fi

cd "${INSTALL_DIR}"
composer install --no-dev --optimize-autoloader --no-interaction

echo "[5/6] Configuring"
if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    cp "${SCRIPT_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
    echo "  Example config copied to ${CONFIG_DIR}/config.yaml"
    echo "  EDIT THIS FILE before starting the server!"
else
    echo "  Config already exists, skipping"
fi

chown root:${SERVICE_USER} "${CONFIG_DIR}/config.yaml"
chmod 640 "${CONFIG_DIR}/config.yaml"

echo "[6/6] Installing systemd service"
cp "${SCRIPT_DIR}/etc/serverlens.service" /etc/systemd/system/serverlens.service
systemctl daemon-reload
echo "  Service installed (not started)"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit config: sudo nano ${CONFIG_DIR}/config.yaml"
echo "  2. Generate token: ${INSTALL_DIR}/bin/serverlens token generate"
echo "  3. Add token hash to config"
echo "  4. Set up read-only DB user (see scripts/setup_db_users.sql)"
echo "  5. Start: sudo systemctl start serverlens"
echo "  6. Enable on boot: sudo systemctl enable serverlens"
echo ""
echo "SSH tunnel (from developer machine):"
echo "  ssh -L 9600:127.0.0.1:9600 user@this-server"
echo ""
