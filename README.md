# InfoBildschirm

Leichtgewichtige Info-Screen-Software für Linux (Low-Spec VPS, Raspberry Pi, Mini-PC) mit **so wenig Dependencies wie möglich**.

## Features

- Zwei Modi über Startseite auswählbar:
  - **Anzeige-Modus** (`/display`) für den Info-Bildschirm
  - **Verwaltungs-Modus** (`/manage`) zum Steuern der Inhalte
- Inhalte als Slideshow:
  - Bilder
  - Videos
  - Webseiten (Iframe)
- Lokale SQLite-Datenbank (kein externer Dienst)
- Keine externen Tracking-Tools, keine Cookies
- Optionaler Passwortschutz für Verwaltung (`ADMIN_PASSWORD`)

## Voraussetzungen

- Linux
- Python 3.10+ (nur Standardbibliothek, keine zusätzlichen Python-Pakete nötig)

## Start

```bash
cd /home/runner/work/InfoBildschirm/InfoBildschirm
python3 app.py
```

Standard: `http://0.0.0.0:8080`

### Optionale Umgebungsvariablen

- `HOST` (Default `0.0.0.0`)
- `PORT` (Default `8080`)
- `ADMIN_PASSWORD` (wenn gesetzt, schützt `/manage` mit HTTP Basic Auth)
- `ENABLE_ACCESS_LOG=1` (standardmäßig aus, DSGVO-freundliche Datensparsamkeit)

## Inhalte nutzen

- Lokale Medien unter `/media/` ablegen (z. B. `/media/schulnews.jpg`)
- Im Verwaltungs-Modus Einträge anlegen:
  - Typ: `image`, `video`, `web`
  - Quelle: `/media/...` oder `https://...`
  - Dauer in Sekunden
  - Sortierung

## DSGVO-Hinweise (für Schulen)

Die Software ist auf datensparsame Defaults ausgelegt:

- keine Cookies
- keine Tracker/Analyse-Skripte
- keine externe Datenbank/Cloud nötig
- Access-Logs standardmäßig deaktiviert

Wichtig für den Betrieb:

1. **ADMIN_PASSWORD setzen**, damit nur berechtigte Personen Inhalte ändern.
2. Möglichst **lokale Medien** verwenden.
3. Bei extern eingebetteten Webseiten prüfen, ob deren Verarbeitung personenbezogener Daten schul- und DSGVO-konform ist.
4. Falls die Instanz aus dem Internet erreichbar ist: TLS/Reverse-Proxy (z. B. Nginx) und starke Passwörter nutzen.

## Test

```bash
cd /home/runner/work/InfoBildschirm/InfoBildschirm
python3 -m unittest -v
```
