#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# InfoBildschirm – Installer & Onboarding-Wizard
#
# Einzeiler für die README:
#   curl -sSL https://raw.githubusercontent.com/DasFletchi/InfoBildschirm/main/install.sh | bash
#
# Flags:
#   --uninstall   Alles entfernen (Service, Dateien, cloudflared)
#   --path <dir>  Alternativer Installationspfad (Standard: /opt/infobildschirm)
#
# Getestet auf: Raspberry Pi OS (Bookworm), Ubuntu 22.04+, Debian 12+
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ─── Farben & Formatierung ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ─── Globale Variablen ───────────────────────────────────────────────────────
INSTALL_DIR="/opt/infobildschirm"
REPO_URL="https://github.com/DasFletchi/InfoBildschirm.git"
SERVICE_NAME="infobildschirm"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CF_SERVICE_NAME="cloudflared-infobildschirm"
CF_SERVICE_FILE="/etc/systemd/system/${CF_SERVICE_NAME}.service"
CF_CONFIG_DIR="/etc/cloudflared"
CF_CONFIG_FILE="${CF_CONFIG_DIR}/config.yml"
ENV_FILE=""  # wird nach INSTALL_DIR gesetzt
RUN_USER="${SUDO_USER:-$(whoami)}"
UNINSTALL=false
PORT=8080
TUNNEL_URL=""

# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

banner() {
    echo ""
    echo -e "${CYAN}┌──────────────────────────────────────────┐${RESET}"
    echo -e "${CYAN}│${RESET}  🖥️  ${BOLD}InfoBildschirm – Setup${RESET}             ${CYAN}│${RESET}"
    echo -e "${CYAN}└──────────────────────────────────────────┘${RESET}"
    echo ""
}

info()    { echo -e "  ${BLUE}ℹ${RESET}  $*"; }
success() { echo -e "  ${GREEN}✔${RESET}  $*"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
error()   { echo -e "  ${RED}✖${RESET}  $*"; }
step()    { echo -e "\n${BOLD}${CYAN}[$1]${RESET} ${BOLD}$2${RESET}\n"; }

# Frage mit Default-Wert
ask() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    local input
    if [[ -n "$default" ]]; then
        read -rp "  → ${prompt} [${default}]: " input
        eval "$var_name=\"${input:-$default}\""
    else
        read -rp "  → ${prompt}: " input
        eval "$var_name=\"${input}\""
    fi
}

# Ja/Nein-Frage (Standard: Nein)
confirm() {
    local prompt="$1"
    local answer
    read -rp "  → ${prompt} [j/N]: " answer
    [[ "${answer,,}" == "j" || "${answer,,}" == "ja" || "${answer,,}" == "y" || "${answer,,}" == "yes" ]]
}

# Ja/Nein-Frage (Standard: Ja)
confirm_yes() {
    local prompt="$1"
    local answer
    read -rp "  → ${prompt} [J/n]: " answer
    [[ "${answer,,}" != "n" && "${answer,,}" != "nein" && "${answer,,}" != "no" ]]
}

# ─── Deinstallation ──────────────────────────────────────────────────────────

do_uninstall() {
    banner
    step "🗑️" "Deinstallation"

    warn "Folgendes wird entfernt:"
    echo "     - Systemd-Service: ${SERVICE_NAME}"
    echo "     - Systemd-Service: ${CF_SERVICE_NAME} (falls vorhanden)"
    echo "     - Installationsverzeichnis: ${INSTALL_DIR}"
    echo "     - Cloudflared-Konfiguration: ${CF_CONFIG_DIR} (falls vorhanden)"
    echo ""

    if ! confirm "Wirklich alles deinstallieren?"; then
        info "Abgebrochen."
        exit 0
    fi

    # Systemd-Services stoppen & entfernen
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        info "Stoppe ${SERVICE_NAME}..."
        sudo systemctl stop "${SERVICE_NAME}" || true
    fi
    if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        sudo systemctl disable "${SERVICE_NAME}" || true
    fi
    if [[ -f "${SERVICE_FILE}" ]]; then
        sudo rm -f "${SERVICE_FILE}"
        success "Service-Datei entfernt: ${SERVICE_FILE}"
    fi

    # Cloudflared Service
    if systemctl is-active --quiet "${CF_SERVICE_NAME}" 2>/dev/null; then
        info "Stoppe ${CF_SERVICE_NAME}..."
        sudo systemctl stop "${CF_SERVICE_NAME}" || true
    fi
    if systemctl is-enabled --quiet "${CF_SERVICE_NAME}" 2>/dev/null; then
        sudo systemctl disable "${CF_SERVICE_NAME}" || true
    fi
    if [[ -f "${CF_SERVICE_FILE}" ]]; then
        sudo rm -f "${CF_SERVICE_FILE}"
        success "Service-Datei entfernt: ${CF_SERVICE_FILE}"
    fi

    # Cloudflared Konfiguration
    if [[ -d "${CF_CONFIG_DIR}" ]]; then
        sudo rm -rf "${CF_CONFIG_DIR}"
        success "Cloudflared-Konfiguration entfernt: ${CF_CONFIG_DIR}"
    fi

    sudo systemctl daemon-reload 2>/dev/null || true

    # Installationsverzeichnis
    if [[ -d "${INSTALL_DIR}" ]]; then
        sudo rm -rf "${INSTALL_DIR}"
        success "Installationsverzeichnis entfernt: ${INSTALL_DIR}"
    fi

    echo ""
    success "${BOLD}Deinstallation abgeschlossen.${RESET}"
    echo ""
    exit 0
}

# ─── Argumente parsen ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --path)
            INSTALL_DIR="$2"
            shift 2
            ;;
        *)
            error "Unbekanntes Argument: $1"
            echo "  Verwendung: $0 [--uninstall] [--path <verzeichnis>]"
            exit 1
            ;;
    esac
done

ENV_FILE="${INSTALL_DIR}/.env"

# Deinstallation zuerst prüfen
if $UNINSTALL; then
    do_uninstall
fi

# ═════════════════════════════════════════════════════════════════════════════
#  Hauptinstallation
# ═════════════════════════════════════════════════════════════════════════════

banner

# ─── Schritt 1/5: Systemprüfung ──────────────────────────────────────────────

step "1/5" "Systemprüfung"

# Betriebssystem
if [[ "$(uname -s)" != "Linux" ]]; then
    error "Dieses Skript läuft nur unter Linux."
    error "Erkannt: $(uname -s)"
    exit 1
fi
success "Betriebssystem: Linux ($(uname -m))"

# Python >= 3.10
if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 10 ]]; }; then
        error "Python >= 3.10 wird benötigt (gefunden: ${PYTHON_VERSION})"
        error "Installation: sudo apt update && sudo apt install python3"
        exit 1
    fi
    success "Python: ${PYTHON_VERSION}"
else
    error "Python3 ist nicht installiert."
    error "Installation: sudo apt update && sudo apt install python3"
    exit 1
fi

# Git
if command -v git &>/dev/null; then
    success "Git: $(git --version | awk '{print $3}')"
else
    error "Git ist nicht installiert."
    error "Installation: sudo apt update && sudo apt install git"
    exit 1
fi

# curl (benötigt für Geocoding API und ggf. cloudflared-Download)
if command -v curl &>/dev/null; then
    success "curl: verfügbar"
else
    error "curl ist nicht installiert."
    error "Installation: sudo apt update && sudo apt install curl"
    exit 1
fi

# Netzwerk
if curl -sfm 5 "https://github.com" -o /dev/null 2>/dev/null; then
    success "Netzwerk: Verbindung zu github.com OK"
else
    error "Keine Verbindung zu github.com möglich."
    error "Bitte Netzwerkverbindung und DNS prüfen."
    exit 1
fi

# ─── Schritt 2/5: Installation ───────────────────────────────────────────────

step "2/5" "Installation"

# Installationspfad bestätigen/ändern
echo -e "  ${DIM}Standard-Installationspfad: ${INSTALL_DIR}${RESET}"
if ! confirm_yes "Installationspfad beibehalten?"; then
    ask "Neuer Installationspfad" "" INSTALL_DIR
    ENV_FILE="${INSTALL_DIR}/.env"
fi

# Repository klonen oder aktualisieren
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    warn "Verzeichnis existiert bereits: ${INSTALL_DIR}"
    echo ""
    echo "  Was möchtest du tun?"
    echo "    1) Aktualisieren (git pull)"
    echo "    2) Neu installieren (Verzeichnis löschen & neu klonen)"
    echo "    3) Abbrechen"
    echo ""
    read -rp "  → Auswahl [1]: " CHOICE
    CHOICE="${CHOICE:-1}"

    case "$CHOICE" in
        1)
            info "Aktualisiere Repository..."
            sudo -u "${RUN_USER}" git -C "${INSTALL_DIR}" pull --ff-only
            success "Repository aktualisiert."
            ;;
        2)
            warn "Lösche ${INSTALL_DIR}..."
            sudo rm -rf "${INSTALL_DIR}"
            info "Klone Repository..."
            sudo git clone "${REPO_URL}" "${INSTALL_DIR}"
            sudo chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
            success "Repository neu geklont."
            ;;
        *)
            info "Abgebrochen."
            exit 0
            ;;
    esac
elif [[ -d "${INSTALL_DIR}" ]]; then
    # Verzeichnis existiert, aber kein Git-Repo
    warn "Verzeichnis ${INSTALL_DIR} existiert, ist aber kein Git-Repository."
    if confirm "Verzeichnis löschen und neu installieren?"; then
        sudo rm -rf "${INSTALL_DIR}"
        info "Klone Repository..."
        sudo git clone "${REPO_URL}" "${INSTALL_DIR}"
        sudo chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
        success "Repository geklont."
    else
        info "Abgebrochen."
        exit 0
    fi
else
    info "Klone Repository nach ${INSTALL_DIR}..."
    sudo mkdir -p "$(dirname "${INSTALL_DIR}")"
    sudo git clone "${REPO_URL}" "${INSTALL_DIR}"
    sudo chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
    success "Repository geklont."
fi

# Verzeichnisse erstellen
for dir in data media; do
    if [[ ! -d "${INSTALL_DIR}/${dir}" ]]; then
        mkdir -p "${INSTALL_DIR}/${dir}"
        success "Verzeichnis erstellt: ${dir}/"
    else
        success "Verzeichnis vorhanden: ${dir}/"
    fi
done

# Eigentümer setzen
sudo chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
success "Eigentümer gesetzt: ${RUN_USER}"

# .env zu .gitignore hinzufügen (idempotent)
GITIGNORE="${INSTALL_DIR}/.gitignore"
if [[ -f "${GITIGNORE}" ]]; then
    if ! grep -qxF '.env' "${GITIGNORE}"; then
        echo '.env' >> "${GITIGNORE}"
        success ".env zu .gitignore hinzugefügt"
    fi
else
    echo '.env' > "${GITIGNORE}"
    success ".gitignore mit .env erstellt"
fi

# ─── Schritt 3/5: Konfiguration ──────────────────────────────────────────────

step "3/5" "Konfiguration (interaktiv)"

# Admin-Passwort
echo ""
info "Admin-Passwort für die Verwaltungsoberfläche festlegen."
while true; do
    echo -n "  → Admin-Passwort: "
    read -rs ADMIN_PASSWORD
    echo ""
    if [[ -z "$ADMIN_PASSWORD" ]]; then
        warn "Passwort darf nicht leer sein."
        continue
    fi
    echo -n "  → Passwort bestätigen: "
    read -rs ADMIN_PASSWORD_CONFIRM
    echo ""
    if [[ "$ADMIN_PASSWORD" == "$ADMIN_PASSWORD_CONFIRM" ]]; then
        success "Passwort gesetzt."
        break
    else
        warn "Passwörter stimmen nicht überein. Bitte erneut versuchen."
    fi
done

# Wetter-Standort
echo ""
info "Standort für das Wetter-Widget."
echo -e "  ${DIM}Standard: Berlin (52.52°N, 13.41°E)${RESET}"
echo ""

WEATHER_LAT="52.52"
WEATHER_LON="13.41"
WEATHER_CITY="Berlin"

if confirm "Anderen Standort wählen?"; then
    while true; do
        ask "Stadtname eingeben" "" CITY_SEARCH
        if [[ -z "$CITY_SEARCH" ]]; then
            warn "Bitte einen Stadtnamen eingeben."
            continue
        fi

        info "Suche nach '${CITY_SEARCH}'..."

        # URL-encode des Stadtnamens (einfache Variante via Python)
        ENCODED_CITY=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${CITY_SEARCH}'))")

        # Open-Meteo Geocoding API abfragen
        GEO_RESPONSE=$(curl -sfm 10 "https://geocoding-api.open-meteo.com/v1/search?name=${ENCODED_CITY}&count=1&language=de" 2>/dev/null || echo "")

        if [[ -z "$GEO_RESPONSE" ]]; then
            warn "Konnte Geocoding-API nicht erreichen. Verwende Standard (Berlin)."
            break
        fi

        # JSON mit Python parsen
        GEO_RESULT=$(python3 -c "
import json, sys
try:
    data = json.loads('''${GEO_RESPONSE}''')
    results = data.get('results', [])
    if results:
        r = results[0]
        name = r.get('name', 'Unbekannt')
        admin = r.get('admin1', '')
        country = r.get('country', '')
        lat = r.get('latitude', 0)
        lon = r.get('longitude', 0)
        label = name
        if admin:
            label += f', {admin}'
        if country:
            label += f' ({country})'
        print(f'{lat}|{lon}|{name}|{label}')
    else:
        print('NOT_FOUND')
except Exception:
    print('ERROR')
" 2>/dev/null || echo "ERROR")

        if [[ "$GEO_RESULT" == "NOT_FOUND" ]]; then
            warn "Kein Ergebnis für '${CITY_SEARCH}'. Bitte erneut versuchen."
            continue
        elif [[ "$GEO_RESULT" == "ERROR" ]]; then
            warn "Fehler beim Parsen der Antwort. Verwende Standard (Berlin)."
            break
        fi

        IFS='|' read -r GEO_LAT GEO_LON GEO_NAME GEO_LABEL <<< "$GEO_RESULT"

        echo ""
        info "Gefunden: ${BOLD}${GEO_LABEL}${RESET}"
        info "Koordinaten: ${GEO_LAT}°N, ${GEO_LON}°E"
        echo ""

        if confirm_yes "Diesen Standort verwenden?"; then
            WEATHER_LAT="$GEO_LAT"
            WEATHER_LON="$GEO_LON"
            WEATHER_CITY="$GEO_NAME"
            success "Standort gesetzt: ${WEATHER_CITY}"
            break
        fi
        # Sonst: Schleife wiederholen
    done
else
    success "Standort: Berlin (Standard)"
fi

# Port
echo ""
ask "Port für den Webserver" "8080" PORT
success "Port: ${PORT}"

# .env-Datei schreiben
info "Schreibe Konfiguration nach ${ENV_FILE}..."
cat > "${ENV_FILE}" <<ENVEOF
# ──────────────────────────────────────────
# InfoBildschirm – Konfiguration
# Erstellt am: $(date '+%Y-%m-%d %H:%M:%S')
# ──────────────────────────────────────────

# Admin-Zugang
ADMIN_PASSWORD=${ADMIN_PASSWORD}

# Wetter-Widget
WEATHER_CITY=${WEATHER_CITY}
WEATHER_LAT=${WEATHER_LAT}
WEATHER_LON=${WEATHER_LON}

# Webserver
PORT=${PORT}

# Installationspfad
INSTALL_DIR=${INSTALL_DIR}
ENVEOF

# .env nur für den Benutzer lesbar
chmod 600 "${ENV_FILE}"
chown "${RUN_USER}:${RUN_USER}" "${ENV_FILE}"
success "Konfiguration gespeichert."

# ─── Schritt 4/5: Systemd-Service ────────────────────────────────────────────

step "4/5" "Systemd-Service einrichten"

info "Erstelle ${SERVICE_FILE}..."

sudo tee "${SERVICE_FILE}" > /dev/null <<SERVICEEOF
[Unit]
Description=InfoBildschirm Digital Signage
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
SERVICEEOF

success "Service-Datei erstellt."

info "Lade systemd-Konfiguration neu..."
sudo systemctl daemon-reload
success "systemd neu geladen."

sudo systemctl enable "${SERVICE_NAME}" --quiet 2>/dev/null
success "Service aktiviert (startet beim Boot)."

sudo systemctl restart "${SERVICE_NAME}"
success "Service gestartet."

# ─── Schritt 5/5: Cloudflare Tunnel (optional) ───────────────────────────────

step "5/5" "Cloudflare Tunnel (optional)"

echo -e "  ${DIM}Ein Cloudflare-Tunnel macht den Bildschirm auch außerhalb${RESET}"
echo -e "  ${DIM}des Schulnetzwerks erreichbar (z.B. von zu Hause).${RESET}"
echo ""

if confirm "Soll der Bildschirm auch über das Internet erreichbar sein? (z.B. von zu Hause)"; then
    echo ""
    echo "  Tunnel-Variante wählen:"
    echo ""
    echo "    1) ${BOLD}Quick Tunnel${RESET} ${DIM}(kein Account nötig, URL ändert sich bei Neustart)${RESET}"
    echo "    2) ${BOLD}Persistenter Tunnel${RESET} ${DIM}(Cloudflare-Account nötig, feste URL)${RESET}"
    echo ""
    read -rp "  → Auswahl [1]: " TUNNEL_CHOICE
    TUNNEL_CHOICE="${TUNNEL_CHOICE:-1}"

    # ── cloudflared installieren ──
    install_cloudflared() {
        if command -v cloudflared &>/dev/null; then
            success "cloudflared ist bereits installiert: $(cloudflared --version 2>&1 | head -1)"
            return 0
        fi

        info "Installiere cloudflared..."

        # Architektur erkennen
        ARCH=$(uname -m)
        case "$ARCH" in
            aarch64)       CF_ARCH="arm64" ;;
            armv7l|armhf)  CF_ARCH="arm"   ;;
            x86_64)        CF_ARCH="amd64" ;;
            *)
                error "Nicht unterstützte Architektur: ${ARCH}"
                error "Bitte cloudflared manuell installieren:"
                error "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
                return 1
                ;;
        esac

        CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
        info "Lade cloudflared herunter (${CF_ARCH})..."

        if sudo curl -sfL "${CF_URL}" -o /usr/local/bin/cloudflared; then
            sudo chmod +x /usr/local/bin/cloudflared
            success "cloudflared installiert: $(cloudflared --version 2>&1 | head -1)"
        else
            error "Download fehlgeschlagen."
            error "URL: ${CF_URL}"
            return 1
        fi
    }

    if ! install_cloudflared; then
        warn "cloudflared konnte nicht installiert werden. Tunnel wird übersprungen."
    else
        case "$TUNNEL_CHOICE" in
            # ── Quick Tunnel ──
            1)
                echo ""
                info "Quick Tunnel wird eingerichtet..."
                warn "Hinweis: Die URL ändert sich bei jedem Neustart des Tunnels."
                warn "Empfohlen nur zum Testen – nicht für den Dauerbetrieb."
                echo ""

                # Quick-Tunnel als systemd-Service (damit er beim Boot startet)
                sudo tee "${CF_SERVICE_FILE}" > /dev/null <<CFSEOF
[Unit]
Description=Cloudflare Quick Tunnel for InfoBildschirm
After=network-online.target ${SERVICE_NAME}.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:${PORT}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${CF_SERVICE_NAME}

[Install]
WantedBy=multi-user.target
CFSEOF

                sudo systemctl daemon-reload
                sudo systemctl enable "${CF_SERVICE_NAME}" --quiet 2>/dev/null
                sudo systemctl restart "${CF_SERVICE_NAME}"
                success "Quick-Tunnel-Service gestartet."

                # Kurz warten und URL aus dem Journal auslesen
                info "Warte auf Tunnel-URL..."
                sleep 5

                TUNNEL_URL=$(sudo journalctl -u "${CF_SERVICE_NAME}" --no-pager -n 30 2>/dev/null \
                    | grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || echo "")

                if [[ -z "$TUNNEL_URL" ]]; then
                    # Zweiter Versuch nach etwas mehr Wartezeit
                    sleep 5
                    TUNNEL_URL=$(sudo journalctl -u "${CF_SERVICE_NAME}" --no-pager -n 50 2>/dev/null \
                        | grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || echo "")
                fi

                if [[ -n "$TUNNEL_URL" ]]; then
                    success "Tunnel-URL: ${BOLD}${TUNNEL_URL}${RESET}"
                else
                    warn "Tunnel-URL konnte noch nicht ermittelt werden."
                    info "Prüfe mit: sudo journalctl -u ${CF_SERVICE_NAME} -f"
                    TUNNEL_URL="(wird beim Start angezeigt – siehe journalctl)"
                fi

                echo ""
                echo -e "  ${YELLOW}Hinweis: Cloudflare verarbeitet als Reverse-Proxy den Netzwerkverkehr.${RESET}"
                echo -e "  ${YELLOW}Da der Infobildschirm keine personenbezogenen Daten erhebt,${RESET}"
                echo -e "  ${YELLOW}ist dies in der Regel DSGVO-konform.${RESET}"
                ;;

            # ── Persistenter Tunnel ──
            2)
                echo ""
                info "Persistenter Tunnel wird eingerichtet..."
                info "Du benötigst einen Cloudflare-Account mit einer konfigurierten Domain."
                echo ""

                # Login
                info "Öffne die angezeigte URL im Browser und melde dich an..."
                echo ""
                cloudflared login
                echo ""
                success "Anmeldung erfolgreich."

                # Tunnel erstellen
                TUNNEL_NAME="infobildschirm"
                info "Erstelle Tunnel '${TUNNEL_NAME}'..."

                # Prüfen, ob Tunnel bereits existiert
                if cloudflared tunnel list 2>/dev/null | grep -q "${TUNNEL_NAME}"; then
                    warn "Tunnel '${TUNNEL_NAME}' existiert bereits."
                    TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null \
                        | grep "${TUNNEL_NAME}" | awk '{print $1}')
                else
                    TUNNEL_CREATE_OUTPUT=$(cloudflared tunnel create "${TUNNEL_NAME}" 2>&1)
                    echo "$TUNNEL_CREATE_OUTPUT"
                    TUNNEL_ID=$(echo "$TUNNEL_CREATE_OUTPUT" \
                        | grep -oP '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
                        | head -1)
                fi

                if [[ -z "$TUNNEL_ID" ]]; then
                    error "Konnte Tunnel-ID nicht ermitteln."
                    warn "Bitte manuell einrichten: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
                else
                    success "Tunnel-ID: ${TUNNEL_ID}"

                    # Domain abfragen
                    echo ""
                    ask "Unter welcher Domain soll der Bildschirm erreichbar sein? (z.B. info.meine-schule.de)" "" TUNNEL_DOMAIN

                    # DNS-Route erstellen
                    info "Erstelle DNS-Eintrag für ${TUNNEL_DOMAIN}..."
                    cloudflared tunnel route dns "${TUNNEL_NAME}" "${TUNNEL_DOMAIN}" 2>/dev/null || \
                        warn "DNS-Route konnte nicht automatisch erstellt werden. Bitte manuell im Cloudflare-Dashboard anlegen."

                    # Config-Datei
                    sudo mkdir -p "${CF_CONFIG_DIR}"
                    sudo tee "${CF_CONFIG_FILE}" > /dev/null <<CFCEOF
tunnel: ${TUNNEL_ID}
credentials-file: /home/${RUN_USER}/.cloudflared/${TUNNEL_ID}.json

ingress:
  - hostname: ${TUNNEL_DOMAIN}
    service: http://localhost:${PORT}
  - service: http_status:404
CFCEOF
                    success "Konfiguration geschrieben: ${CF_CONFIG_FILE}"

                    TUNNEL_URL="https://${TUNNEL_DOMAIN}"

                    # Systemd-Service für persistenten Tunnel
                    sudo tee "${CF_SERVICE_FILE}" > /dev/null <<CFPSEOF
[Unit]
Description=Cloudflare Persistent Tunnel for InfoBildschirm
After=network-online.target ${SERVICE_NAME}.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
ExecStart=/usr/local/bin/cloudflared tunnel --config ${CF_CONFIG_FILE} run ${TUNNEL_NAME}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${CF_SERVICE_NAME}

[Install]
WantedBy=multi-user.target
CFPSEOF

                    sudo systemctl daemon-reload
                    sudo systemctl enable "${CF_SERVICE_NAME}" --quiet 2>/dev/null
                    sudo systemctl restart "${CF_SERVICE_NAME}"
                    success "Persistenter Tunnel gestartet."
                fi

                echo ""
                echo -e "  ${YELLOW}Hinweis: Cloudflare verarbeitet als Reverse-Proxy den Netzwerkverkehr.${RESET}"
                echo -e "  ${YELLOW}Da der Infobildschirm keine personenbezogenen Daten erhebt,${RESET}"
                echo -e "  ${YELLOW}ist dies in der Regel DSGVO-konform.${RESET}"
                ;;

            *)
                warn "Ungültige Auswahl. Tunnel wird übersprungen."
                ;;
        esac
    fi
else
    info "Kein Tunnel eingerichtet. Nur im lokalen Netzwerk erreichbar."
fi

# ─── Fertig! ──────────────────────────────────────────────────────────────────

echo ""
echo ""

# LAN-IP ermitteln
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -z "$LAN_IP" ]]; then
    LAN_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[\d.]+' || echo "???")
fi

LOCAL_URL="http://${LAN_IP}:${PORT}"

# Breite der Box berechnen
BOX_WIDTH=50

print_padded() {
    local content="$1"
    # Sichtbare Zeichenlänge (ANSI-Codes entfernen)
    local visible
    visible=$(echo -e "$content" | sed 's/\x1b\[[0-9;]*m//g')
    local visible_len=${#visible}
    local padding=$((BOX_WIDTH - 2 - visible_len))
    if (( padding < 0 )); then padding=0; fi
    printf "║ %s%*s ║\n" "$content" "$padding" ""
}

# Box oben
printf "${GREEN}╔"
printf '═%.0s' $(seq 1 $BOX_WIDTH)
printf "╗${RESET}\n"

print_padded ""
print_padded "✅ InfoBildschirm ist bereit!"
print_padded ""
print_padded "📍 Intranet (Schul-WLAN):"
print_padded "   ${LOCAL_URL}"
print_padded ""

if [[ -n "$TUNNEL_URL" ]]; then
    print_padded "🌐 Internet (von überall):"
    print_padded "   ${TUNNEL_URL}"
    print_padded ""
fi

print_padded "🔧 Verwaltung: .../manage"
print_padded "📺 Anzeige:    .../display"
print_padded ""

# Box unten
printf "${GREEN}╚"
printf '═%.0s' $(seq 1 $BOX_WIDTH)
printf "╝${RESET}\n"

echo ""
echo -e "  ${DIM}Nützliche Befehle:${RESET}"
echo -e "  ${DIM}  Status:     sudo systemctl status ${SERVICE_NAME}${RESET}"
echo -e "  ${DIM}  Logs:       sudo journalctl -u ${SERVICE_NAME} -f${RESET}"
echo -e "  ${DIM}  Neustart:   sudo systemctl restart ${SERVICE_NAME}${RESET}"
echo -e "  ${DIM}  Stoppen:    sudo systemctl stop ${SERVICE_NAME}${RESET}"
echo -e "  ${DIM}  Entfernen:  bash ${INSTALL_DIR}/install.sh --uninstall${RESET}"
echo ""
