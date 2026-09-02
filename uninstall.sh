#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# InfoBildschirm – Vollständiger Deinstaller & Reset
#
# Einzeiler für die README:
#   curl -sSL https://raw.githubusercontent.com/DasFletchi/InfoBildschirm/main/uninstall.sh | bash
#
# Flags:
#   --force / -y  Ohne Bestätigungsabfrage sofort alles löschen
#   --path <dir>  Zusätzlicher/alternativer Pfad zum Bereinigen
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Wenn über Pipe ausgeführt (curl | bash), Terminal für Tastatureingaben (/dev/tty) nutzen
if [[ ! -t 0 ]] && [[ -e /dev/tty ]]; then
    exec < /dev/tty
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

INSTALL_DIR="/opt/infobildschirm"
CUSTOM_PATH=""
FORCE=false
SERVICE_NAME="infobildschirm"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CF_SERVICE_NAME="cloudflared-infobildschirm"
CF_SERVICE_FILE="/etc/systemd/system/${CF_SERVICE_NAME}.service"
CF_CONFIG_DIR="/etc/cloudflared"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force|-y)
            FORCE=true
            shift
            ;;
        --path)
            CUSTOM_PATH="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo ""
echo -e "${RED}┌──────────────────────────────────────────┐${RESET}"
echo -e "${RED}│${RESET}  🗑️  ${BOLD}InfoBildschirm – Kompletter Reset${RESET}   ${RED}│${RESET}"
echo -e "${RED}└──────────────────────────────────────────┘${RESET}"
echo ""

echo -e "  ${YELLOW}Folgendes wird restlos beendet und gelöscht:${RESET}"
echo -e "   - Alle laufenden InfoBildschirm-Prozesse (Port 8080)"
echo -e "   - Systemd-Dienste: ${BOLD}${SERVICE_NAME}${RESET}, ${BOLD}${CF_SERVICE_NAME}${RESET}"
echo -e "   - Installationsverzeichnis: ${BOLD}${INSTALL_DIR}${RESET}"
echo -e "   - Alle Konfigurationen: ${BOLD}.env${RESET}, Datenbanken (${BOLD}data/*.db${RESET})"
echo -e "   - Cloudflare-Tunnel-Konfigurationen (${BOLD}${CF_CONFIG_DIR}${RESET})"
if [[ -n "$CUSTOM_PATH" ]]; then
    echo -e "   - Benutzerdefinierter Pfad: ${BOLD}${CUSTOM_PATH}${RESET}"
fi
echo ""

if ! $FORCE; then
    read -rp "  → Wirklich alles restlos löschen & zurücksetzen? [j/N]: " CONFIRM
    if [[ "${CONFIRM,,}" != "j" && "${CONFIRM,,}" != "ja" && "${CONFIRM,,}" != "y" && "${CONFIRM,,}" != "yes" ]]; then
        echo -e "  ${BLUE}ℹ${RESET}  Deinstallation abgebrochen."
        exit 0
    fi
fi

echo ""
echo -e "  ${BLUE}ℹ${RESET}  1/4: Stoppe laufende Prozesse & Server..."

# Prozesse auf Port 8080 beenden
if command -v fuser &>/dev/null; then
    sudo fuser -k 8080/tcp 2>/dev/null || true
fi
sudo pkill -f "python3.*app.py" 2>/dev/null || true
echo -e "  ${GREEN}✔${RESET}  Laufende Server-Prozesse beendet."

echo -e "  ${BLUE}ℹ${RESET}  2/4: Entferne Systemd-Dienste..."

# InfoBildschirm Service
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
fi
if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
fi
if [[ -f "${SERVICE_FILE}" ]]; then
    sudo rm -f "${SERVICE_FILE}"
    echo -e "  ${GREEN}✔${RESET}  ${SERVICE_FILE} entfernt."
fi

# Cloudflare Tunnel Service
if systemctl is-active --quiet "${CF_SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl stop "${CF_SERVICE_NAME}" 2>/dev/null || true
fi
if systemctl is-enabled --quiet "${CF_SERVICE_NAME}" 2>/dev/null; then
    sudo systemctl disable "${CF_SERVICE_NAME}" 2>/dev/null || true
fi
if [[ -f "${CF_SERVICE_FILE}" ]]; then
    sudo rm -f "${CF_SERVICE_FILE}"
    echo -e "  ${GREEN}✔${RESET}  ${CF_SERVICE_FILE} entfernt."
fi

# Cloudflare Configs
if [[ -d "${CF_CONFIG_DIR}" ]]; then
    sudo rm -rf "${CF_CONFIG_DIR}"
    echo -e "  ${GREEN}✔${RESET}  ${CF_CONFIG_DIR} entfernt."
fi
if [[ -d "$HOME/.cloudflared" ]]; then
    rm -rf "$HOME/.cloudflared"
    echo -e "  ${GREEN}✔${RESET}  ~/.cloudflared entfernt."
fi

sudo systemctl daemon-reload 2>/dev/null || true
echo -e "  ${GREEN}✔${RESET}  Systemd neu geladen."

echo -e "  ${BLUE}ℹ${RESET}  3/4: Lösche Installationsordner & Daten..."

# Standard-Installationsordner löschen
if [[ -d "${INSTALL_DIR}" ]]; then
    sudo rm -rf "${INSTALL_DIR}"
    echo -e "  ${GREEN}✔${RESET}  ${INSTALL_DIR} gelöscht."
fi

# Falls ein Custom-Pfad angegeben wurde
if [[ -n "$CUSTOM_PATH" && -d "$CUSTOM_PATH" && "$CUSTOM_PATH" != "/" ]]; then
    sudo rm -rf "$CUSTOM_PATH"
    echo -e "  ${GREEN}✔${RESET}  ${CUSTOM_PATH} gelöscht."
fi

# Falls das Skript innerhalb eines lokalen Git-Klons ausgeführt wird:
# Lokale .env, data/ und media-Dateien bereinigen, damit Git-Repo sauber ist
CURRENT_DIR="$(pwd)"
if [[ -f "${CURRENT_DIR}/app.py" ]]; then
    echo -e "  ${BLUE}ℹ${RESET}  4/4: Bereinige lokales Verzeichnis (${CURRENT_DIR})..."
    rm -f "${CURRENT_DIR}/.env"
    if [[ -d "${CURRENT_DIR}/data" ]]; then
        rm -rf "${CURRENT_DIR}/data"
    fi
    if [[ -d "${CURRENT_DIR}/media" ]]; then
        find "${CURRENT_DIR}/media" -type f ! -name ".gitkeep" -delete 2>/dev/null || true
    fi
    echo -e "  ${GREEN}✔${RESET}  Lokale Konfigurationen, Datenbank & Medien zurückgesetzt."
fi

echo ""
echo -e "${GREEN}┌──────────────────────────────────────────────────────────┐${RESET}"
echo -e "${GREEN}│  ✅ InfoBildschirm wurde komplett zurückgesetzt!        │${RESET}"
echo -e "${GREEN}│                                                          │${RESET}"
echo -e "${GREEN}│  Du kannst das Onboarding jetzt frisch testen mit:      │${RESET}"
echo -e "${GREEN}│  curl -sSL https://raw.githubusercontent.com/...        │${RESET}"
echo -e "${GREEN}└──────────────────────────────────────────────────────────┘${RESET}"
echo ""
