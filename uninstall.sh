#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# InfoBildschirm – Deinstaller (One-Liner)
#
# Einzeiler für die README:
#   curl -sSL https://raw.githubusercontent.com/DasFletchi/InfoBildschirm/main/uninstall.sh | bash
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

INSTALL_DIR="/opt/infobildschirm"
SERVICE_NAME="infobildschirm"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CF_SERVICE_NAME="cloudflared-infobildschirm"
CF_SERVICE_FILE="/etc/systemd/system/${CF_SERVICE_NAME}.service"
CF_CONFIG_DIR="/etc/cloudflared"

echo ""
echo -e "${RED}┌──────────────────────────────────────────┐${RESET}"
echo -e "${RED}│${RESET}  🗑️  ${BOLD}InfoBildschirm – Deinstallation${RESET}     ${RED}│${RESET}"
echo -e "${RED}└──────────────────────────────────────────┘${RESET}"
echo ""

echo -e "  ${YELLOW}Folgende Komponenten werden vollständig entfernt:${RESET}"
echo -e "   - Systemd-Service: ${BOLD}${SERVICE_NAME}${RESET}"
echo -e "   - Systemd-Service: ${BOLD}${CF_SERVICE_NAME}${RESET} (falls vorhanden)"
echo -e "   - Installationsordner & Daten: ${BOLD}${INSTALL_DIR}${RESET}"
echo -e "   - Cloudflare-Konfiguration: ${BOLD}${CF_CONFIG_DIR}${RESET} (falls vorhanden)"
echo ""

read -rp "  → Wirklich alles restlos deinstallieren? [j/N]: " CONFIRM
if [[ "${CONFIRM,,}" != "j" && "${CONFIRM,,}" != "ja" && "${CONFIRM,,}" != "y" && "${CONFIRM,,}" != "yes" ]]; then
    echo -e "  ${BLUE}ℹ${RESET}  Deinstallation abgebrochen."
    exit 0
fi

echo ""
echo -e "  ${BLUE}ℹ${RESET}  Stoppe und entferne Systemd-Services..."

# InfoBildschirm Service
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl stop "${SERVICE_NAME}" || true
fi
if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl disable "${SERVICE_NAME}" || true
fi
if [[ -f "${SERVICE_FILE}" ]]; then
    sudo rm -f "${SERVICE_FILE}"
    echo -e "  ${GREEN}✔${RESET}  ${SERVICE_FILE} entfernt."
fi

# Cloudflare Tunnel Service
if systemctl is-active --quiet "${CF_SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl stop "${CF_SERVICE_NAME}" || true
fi
if systemctl is-enabled --quiet "${CF_SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl disable "${CF_SERVICE_NAME}" || true
fi
if [[ -f "${CF_SERVICE_FILE}" ]]; then
    sudo rm -f "${CF_SERVICE_FILE}"
    echo -e "  ${GREEN}✔${RESET}  ${CF_SERVICE_FILE} entfernt."
fi

# Cloudflare Config
if [[ -d "${CF_CONFIG_DIR}" ]]; then
    sudo rm -rf "${CF_CONFIG_DIR}"
    echo -e "  ${GREEN}✔${RESET}  ${CF_CONFIG_DIR} entfernt."
fi

sudo systemctl daemon-reload 2>/dev/null || true
echo -e "  ${GREEN}✔${RESET}  Systemd neu geladen."

# Programmordner löschen
if [[ -d "${INSTALL_DIR}" ]]; then
    sudo rm -rf "${INSTALL_DIR}"
    echo -e "  ${GREEN}✔${RESET}  ${INSTALL_DIR} gelöscht."
fi

echo ""
echo -e "${GREEN}┌──────────────────────────────────────────┐${RESET}"
echo -e "${GREEN}│  ✅ InfoBildschirm wurde deinstalliert.  │${RESET}"
echo -e "${GREEN}└──────────────────────────────────────────┘${RESET}"
echo ""
