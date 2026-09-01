# 🖥️ InfoBildschirm

Leichtgewichtige Digital-Signage-Software für Schulen – läuft auf Raspberry Pi, Mini-PC oder Linux-Server.  
**Keine externen Abhängigkeiten**, nur Python 3.10+ (Standardbibliothek).

---

## 🚀 One-Line-Installation (Empfohlen)

Führe einfach diesen Befehl im Terminal auf deinem Raspberry Pi oder Linux-Server aus:

```bash
curl -sSL https://raw.githubusercontent.com/DasFletchi/InfoBildschirm/main/install.sh | bash
```

Der interaktive Installer führt dich Schritt für Schritt durch alles:
1. **Systemprüfung** (Python 3.10+, Linux, Git)
2. **Installation** nach `/opt/infobildschirm`
3. **Onboarding**: Admin-Passwort setzen & Schulstandort wählen (*Standard: Göttingen*)
4. **Autostart**: Systemd-Service `infobildschirm.service` einrichten
5. **Netzwerk-Wahl**: 
   - 🏠 Nur lokales Schul-Intranet
   - 🚀 Cloudflare Quick-Tunnel (1-Klick, sofort https-URL fürs Internet)
   - 🌐 Cloudflare Fester Tunnel (mit eigener Schul-Domain)

Am Ende bekommt man **zwei Adressen**:
- 📍 **Intranet**: `http://<LAN-IP>:8080` – im Schul-WLAN erreichbar
- 🌐 **Internet**: `https://<tunnel>.cfargotunnel.com` – von überall (optional)

---

## ✨ Features

| Feature | Beschreibung |
|---------|-------------|
| 📺 **Slideshow** | Bilder, Videos, Webseiten als Diashow mit sanften Fade-Übergängen |
| 📤 **Upload im Browser** | Drag-and-Drop Upload von Bildern und Videos (max. 100 MB) |
| 🌤️ **Live-Wetter** | Aktuelle Temperatur und Vorhersage via Open-Meteo API |
| ⚙️ **Admin-Panel** | Inhalte verwalten, Playlist steuern, Medien-Browser |
| 🧹 **Auto-Bereinigung** | Unbenutzte Dateien werden nach 30 Tagen automatisch gelöscht |
| 🔒 **DSGVO-konform** | Keine Cookies, keine Tracker, kein Cloud-Zwang |
| 🔐 **Passwortschutz** | Admin-Bereich per HTTP Basic Auth geschützt |
| ⏰ **Uhr-Overlay** | Uhrzeit und Datum im Display-Modus |

---

## 🖥️ Modi

### Anzeige-Modus (`/display`)

Für den Info-Bildschirm – öffnen und F11 für Vollbild.

- Sanfte Fade-Übergänge zwischen Slides
- Videos spielen komplett ab
- Wetter-Widget als eigener Slide
- Uhrzeit-Overlay in der Ecke
- Mauszeiger automatisch ausgeblendet
- Playlist wird live aktualisiert

### Verwaltungs-Modus (`/manage`)

Für Lehrer / Admins – Inhalte hochladen und steuern.

- **Datei-Upload** mit Drag-and-Drop und Fortschrittsanzeige
- **Playlist-Verwaltung**: Reihenfolge, Dauer, Aktivieren/Deaktivieren
- **Medien-Browser** (`/manage/media`): Alle Dateien mit Vorschau und Speicherinfo
- Vier Content-Typen: 🖼️ Bild, 🎬 Video, 🌐 Webseite, 🌤️ Wetter

---

## 📋 Manuelle Installation

Falls der One-Liner nicht gewünscht ist:

```bash
# Repository klonen
git clone https://github.com/DasFletchi/InfoBildschirm.git
cd InfoBildschirm

# Admin-Passwort setzen
export ADMIN_PASSWORD="mein_sicheres_passwort"

# Optional: Wetter konfigurieren
export WEATHER_LAT="51.05"
export WEATHER_LON="13.74"
export WEATHER_LOCATION_NAME="Musterstadt"

# Server starten
python3 app.py
```

Standard: `http://0.0.0.0:8080`

---

## ⚙️ Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind-Adresse |
| `PORT` | `8080` | Port |
| `ADMIN_PASSWORD` | *(leer)* | Passwort für Admin-Bereich |
| `WEATHER_LAT` | `51.5338` | Breitengrad (Wetter) |
| `WEATHER_LON` | `9.9355` | Längengrad (Wetter) |
| `WEATHER_LOCATION_NAME` | `Göttingen` | Anzeigename für Wetter |
| `MAX_UPLOAD_MB` | `100` | Max. Upload-Größe in MB |
| `CLEANUP_DAYS` | `30` | Tage bis unbenutzte Dateien gelöscht werden |
| `CLEANUP_MIN_FREE_MB` | `500` | Speicher-Notbremse (verkürzt auf 7 Tage) |
| `ENABLE_ACCESS_LOG` | `0` | Zugriffsprotokollierung (DSGVO: aus) |

---

## 🌤️ Wetter-Widget

Das Wetter wird über die [Open-Meteo API](https://open-meteo.com) bezogen:

- ✅ **Kostenlos** (für nicht-kommerzielle Nutzung)
- ✅ **Kein API-Key** nötig
- ✅ **DSGVO-konform** (europäischer Anbieter, kein Tracking)
- 🔄 Wird alle 15 Minuten aktualisiert (serverseitig gecacht)

Um das Wetter zu nutzen: Im Admin-Panel einen neuen Eintrag mit Typ "Wetter" erstellen.

### Koordinaten finden

1. [Google Maps](https://maps.google.com) öffnen, Schulstandort suchen
2. Rechtsklick → Koordinaten kopieren
3. Als `WEATHER_LAT` und `WEATHER_LON` setzen

Oder beim Installer eingeben – der sucht automatisch nach dem Ortsnamen.

---

## 🧹 Automatische Medien-Bereinigung

Dateien im `media/`-Ordner, die in **keinem** Playlist-Eintrag (aktiv oder inaktiv) referenziert werden, werden nach einer Schonfrist automatisch gelöscht:

- **30 Tage** Schonfrist (konfigurierbar: `CLEANUP_DAYS`)
- Falls weniger als 500 MB frei: Frist verkürzt auf **7 Tage**
- Im Medien-Browser sieht man den Status:
  - 🟢 In Playlist – wird nicht gelöscht
  - 🟡 Unbenutzt – wird in X Tagen gelöscht
  - 🔴 Bald gelöscht (Speicher knapp)
- **"Behalten"-Button** setzt den Timer zurück
- Gelöschte Dateien werden in `data/cleanup.log` protokolliert

---

## 🔒 DSGVO-Hinweise (für Schulen)

Die Software ist auf **Datensparsamkeit** ausgelegt:

- ❌ Keine Cookies
- ❌ Keine Tracker oder Analyse-Skripte
- ❌ Kein Cloud-Zwang (alles lokal)
- ❌ Keine externe Datenbank
- ✅ Zugriffsprotokolle standardmäßig **deaktiviert**

**Empfehlungen für den Schulbetrieb:**

1. `ADMIN_PASSWORD` **immer setzen**
2. Möglichst **lokale Medien** verwenden (Upload statt externer URLs)
3. Bei eingebetteten Webseiten prüfen, ob DSGVO-konform
4. Bei Internet-Zugang: Cloudflare Tunnel oder Reverse-Proxy mit TLS nutzen

> **Cloudflare als Tunnel:** Cloudflare verarbeitet als Reverse-Proxy den Netzwerkverkehr.
> Da der InfoBildschirm keine personenbezogenen Daten erhebt, ist dies in der Regel
> DSGVO-konform. Im Zweifel den Datenschutzbeauftragten der Schule konsultieren.

---

## 🔧 Raspberry Pi: Kiosk-Modus

Für den automatischen Start im Vollbild (Chromium Kiosk):

```bash
# Autostart-Datei erstellen (für LXDE/Openbox)
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/infobildschirm.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=InfoBildschirm
Exec=chromium-browser --kiosk --noerrdialogs --disable-translate --no-first-run --fast --fast-start --disable-infobars http://localhost:8080/display
X-GNOME-Autostart-enabled=true
EOF
```

---

## 🧪 Tests

```bash
python3 -m unittest -v
```

---

## 📁 Projektstruktur

```
InfoBildschirm/
├── app.py              # Server (alles in einer Datei, keine Dependencies)
├── install.sh          # One-Line-Installer mit Onboarding
├── test_app.py         # Tests
├── data/               # SQLite-DB + Cleanup-Log (gitignored)
├── media/              # Hochgeladene Dateien (gitignored)
├── .gitignore
├── LICENSE
└── README.md
```

---

## 📄 Lizenz

MIT – siehe [LICENSE](LICENSE).
