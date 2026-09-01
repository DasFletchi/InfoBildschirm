#!/usr/bin/env python3
"""InfoBildschirm: lightweight digital signage server for Linux devices."""

from __future__ import annotations

import base64
import html
import json
import os
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = BASE_DIR / "media"
DB_PATH = DATA_DIR / "infobildschirm.db"

DEFAULT_DURATION_SECONDS = 15


class ItemStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    item_type TEXT NOT NULL CHECK(item_type IN ('image', 'video', 'web')),
                    source TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL CHECK(duration_seconds >= 3),
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def list_items(self) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, item_type, source, duration_seconds, sort_order, active
                FROM items
                ORDER BY active DESC, sort_order ASC, id ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def list_active(self) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, item_type, source, duration_seconds
                FROM items
                WHERE active = 1
                ORDER BY sort_order ASC, id ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def add_item(
        self,
        *,
        title: str,
        item_type: str,
        source: str,
        duration_seconds: int,
        sort_order: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO items(title, item_type, source, duration_seconds, sort_order, active)
                VALUES(?, ?, ?, ?, ?, 1)
                """,
                (title, item_type, source, duration_seconds, sort_order),
            )

    def set_active(self, item_id: int, active: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE items SET active = ? WHERE id = ?", (1 if active else 0, item_id))

    def delete(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


def html_page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang=\"de\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #111; color: #eee; }}
    .container {{ max-width: 980px; margin: 0 auto; padding: 1rem; }}
    a, button {{ cursor: pointer; }}
    .card {{ background: #1d1d1d; border: 1px solid #333; border-radius: 10px; padding: 1rem; margin-bottom: 1rem; }}
    input, select {{ width: 100%; box-sizing: border-box; padding: 0.55rem; margin: 0.25rem 0 0.75rem; background: #111; color: #eee; border: 1px solid #444; border-radius: 6px; }}
    button {{ background: #2a6df4; color: white; border: 0; border-radius: 6px; padding: 0.6rem 0.9rem; }}
    .btn-danger {{ background: #d33; }}
    .btn-alt {{ background: #444; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 0.45rem; border-bottom: 1px solid #333; vertical-align: top; }}
    .mode-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
    .mode-link {{ display: block; text-decoration: none; color: inherit; }}
    .mode-link .card {{ height: 100%; }}
    code {{ background: #222; padding: 0.1rem 0.25rem; border-radius: 5px; }}
    small {{ color: #aaa; }}
  </style>
</head>
<body>
{body}
</body>
</html>""".encode("utf-8")


def normalize_source(source: str) -> str:
    src = source.strip()
    if not src:
        raise ValueError("Quelle darf nicht leer sein")
    if src.startswith("/"):
        return src
    if src.startswith("http://") or src.startswith("https://"):
        return src
    raise ValueError("Quelle muss mit / oder http(s):// beginnen")


class InfoHandler(BaseHTTPRequestHandler):
    server_version = "InfoBildschirm/1.0"

    @property
    def store(self) -> ItemStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def admin_password(self) -> str:
        return self.server.admin_password  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        if os.getenv("ENABLE_ACCESS_LOG") == "1":
            super().log_message(format, *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._respond_html(self._mode_picker())
            return
        if path == "/display":
            self._respond_html(self._display_page())
            return
        if path == "/manage":
            if not self._require_auth():
                return
            self._respond_html(self._manage_page())
            return
        if path == "/api/items":
            if not self._require_auth():
                return
            self._respond_json({"items": self.store.list_items()})
            return
        if path == "/api/playlist":
            self._respond_json({"items": self.store.list_active()})
            return
        if path.startswith("/media/"):
            self._serve_media(path)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        form = parse_qs(raw, keep_blank_values=True)

        if parsed.path == "/manage/add":
            try:
                title = form.get("title", [""])[0].strip() or "Ohne Titel"
                item_type = form.get("item_type", [""])[0].strip()
                source = normalize_source(form.get("source", [""])[0])
                duration = int(form.get("duration", [str(DEFAULT_DURATION_SECONDS)])[0])
                sort_order = int(form.get("sort_order", ["0"])[0])
                if item_type not in {"image", "video", "web"}:
                    raise ValueError("Ungültiger Typ")
                if duration < 3:
                    raise ValueError("Dauer muss mindestens 3 Sekunden sein")
            except (ValueError, TypeError) as exc:
                self._redirect(f"/manage?error={html.escape(str(exc))}")
                return

            self.store.add_item(
                title=title,
                item_type=item_type,
                source=source,
                duration_seconds=duration,
                sort_order=sort_order,
            )
            self._redirect("/manage")
            return

        if parsed.path == "/manage/toggle":
            try:
                item_id = int(form.get("id", ["0"])[0])
                active = form.get("active", ["0"])[0] == "1"
                self.store.set_active(item_id, active)
            except ValueError:
                pass
            self._redirect("/manage")
            return

        if parsed.path == "/manage/delete":
            try:
                item_id = int(form.get("id", ["0"])[0])
                self.store.delete(item_id)
            except ValueError:
                pass
            self._redirect("/manage")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _require_auth(self, optional: bool = False) -> bool:
        if not self.admin_password:
            return True

        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                raw = base64.b64decode(auth[6:].encode("ascii"), validate=True).decode("utf-8")
                _user, pwd = raw.split(":", 1)
                if pwd == self.admin_password:
                    return True
            except Exception:
                pass

        if optional:
            return True

        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="InfoBildschirm Admin"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authentifizierung erforderlich")
        return False

    def _respond_html(self, body: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, payload: dict[str, object]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _redirect(self, target: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", target)
        self.end_headers()

    def _serve_media(self, path: str) -> None:
        rel = path.removeprefix("/media/").strip("/")
        file_path = (MEDIA_DIR / rel).resolve()
        if not str(file_path).startswith(str(MEDIA_DIR.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        ext = file_path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
        }.get(ext, "application/octet-stream")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def _mode_picker(self) -> bytes:
        body = """
<div class=\"container\">
  <h1>InfoBildschirm</h1>
  <p>Wähle den Modus:</p>
  <div class=\"mode-grid\">
    <a class=\"mode-link\" href=\"/display\"><div class=\"card\"><h2>Anzeige-Modus</h2><p>Für den Bildschirm (Kiosk/Fullscreen).</p></div></a>
    <a class=\"mode-link\" href=\"/manage\"><div class=\"card\"><h2>Verwaltungs-Modus</h2><p>Inhalte verwalten und Playlist steuern.</p></div></a>
  </div>
</div>
"""
        return html_page("InfoBildschirm", body)

    def _display_page(self) -> bytes:
        body = """
<div id=\"screen\" style=\"height:100vh;display:flex;align-items:center;justify-content:center;background:#000;\"></div>
<script>
const screen = document.getElementById('screen');
let items = [];
let idx = 0;

async function loadPlaylist() {
  const response = await fetch('/api/playlist', {cache: 'no-store'});
  const data = await response.json();
  items = data.items || [];
}

function render(item) {
  screen.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.style.width = '100%';
  wrap.style.height = '100%';
  wrap.style.display = 'flex';
  wrap.style.alignItems = 'center';
  wrap.style.justifyContent = 'center';
  if (item.item_type === 'image') {
    const img = document.createElement('img');
    img.src = item.source;
    img.alt = item.title;
    img.style.maxWidth = '100%';
    img.style.maxHeight = '100%';
    wrap.appendChild(img);
  } else if (item.item_type === 'video') {
    const video = document.createElement('video');
    video.src = item.source;
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.style.maxWidth = '100%';
    video.style.maxHeight = '100%';
    wrap.appendChild(video);
  } else {
    const frame = document.createElement('iframe');
    frame.src = item.source;
    frame.style.width = '100%';
    frame.style.height = '100%';
    frame.style.border = '0';
    frame.referrerPolicy = 'no-referrer';
    frame.sandbox = 'allow-same-origin allow-scripts allow-forms';
    wrap.appendChild(frame);
  }
  screen.appendChild(wrap);
}

async function run() {
  while (true) {
    await loadPlaylist();
    if (!items.length) {
      screen.innerHTML = '<div style="color:#ccc;font-size:2rem;">Keine aktiven Inhalte konfiguriert</div>';
      await new Promise(r => setTimeout(r, 5000));
      continue;
    }
    const item = items[idx % items.length];
    render(item);
    idx += 1;
    await new Promise(r => setTimeout(r, (item.duration_seconds || 15) * 1000));
  }
}

run();
</script>
"""
        return html_page("InfoBildschirm Anzeige", body)

    def _manage_page(self) -> bytes:
        params = parse_qs(urlparse(self.path).query)
        error = html.escape(params.get("error", [""])[0])
        error_html = f"<p style='color:#ff8f8f'>{error}</p>" if error else ""

        rows = []
        for item in self.store.list_items():
            item_id = int(item["id"])
            active = bool(item["active"])
            next_active = "0" if active else "1"
            btn = "Deaktivieren" if active else "Aktivieren"
            rows.append(
                f"""
<tr>
  <td>{item_id}</td>
  <td>{html.escape(str(item['title']))}</td>
  <td>{html.escape(str(item['item_type']))}</td>
  <td><code>{html.escape(str(item['source']))}</code></td>
  <td>{int(item['duration_seconds'])}s</td>
  <td>
    <form method=\"post\" action=\"/manage/toggle\" style=\"display:inline\">
      <input type=\"hidden\" name=\"id\" value=\"{item_id}\" />
      <input type=\"hidden\" name=\"active\" value=\"{next_active}\" />
      <button class=\"btn-alt\" type=\"submit\">{btn}</button>
    </form>
    <form method=\"post\" action=\"/manage/delete\" style=\"display:inline\">
      <input type=\"hidden\" name=\"id\" value=\"{item_id}\" />
      <button class=\"btn-danger\" type=\"submit\">Löschen</button>
    </form>
  </td>
</tr>
"""
            )

        body = f"""
<div class=\"container\">
  <h1>Verwaltungs-Modus</h1>
  <p><a href=\"/\" style=\"color:#9dc1ff\">← Zur Modus-Auswahl</a></p>
  {error_html}

  <div class=\"card\">
    <h2>Inhalt hinzufügen</h2>
    <form method=\"post\" action=\"/manage/add\">
      <label>Titel</label>
      <input name=\"title\" type=\"text\" placeholder=\"z.B. Vertretungsplan\" />

      <label>Typ</label>
      <select name=\"item_type\">
        <option value=\"image\">Bild</option>
        <option value=\"video\">Video</option>
        <option value=\"web\">Webseite</option>
      </select>

      <label>Quelle</label>
      <input name=\"source\" type=\"text\" required placeholder=\"/media/datei.jpg oder https://...\" />
      <small>Lokale Dateien unter <code>/media/</code> bevorzugen (DSGVO-freundlicher).</small>

      <label>Dauer (Sekunden)</label>
      <input name=\"duration\" type=\"number\" min=\"3\" value=\"15\" />

      <label>Sortierung (kleiner = früher)</label>
      <input name=\"sort_order\" type=\"number\" value=\"0\" />

      <button type=\"submit\">Speichern</button>
    </form>
  </div>

  <div class=\"card\">
    <h2>Playlist</h2>
    <table>
      <thead><tr><th>ID</th><th>Titel</th><th>Typ</th><th>Quelle</th><th>Dauer</th><th>Aktion</th></tr></thead>
      <tbody>{''.join(rows) if rows else '<tr><td colspan="6">Keine Einträge vorhanden</td></tr>'}</tbody>
    </table>
  </div>

  <div class=\"card\">
    <h2>DSGVO-Hinweise</h2>
    <ul>
      <li>Keine Tracker, keine Cookies, keine externen Analyse-Tools.</li>
      <li>Zugriffsprotokollierung ist standardmäßig deaktiviert.</li>
      <li>Admin-Zugriff kann per <code>ADMIN_PASSWORD</code> geschützt werden.</li>
      <li>Externe Webseiten können personenbezogene Daten verarbeiten – wenn möglich lokale Inhalte nutzen.</li>
    </ul>
  </div>
</div>
"""
        return html_page("InfoBildschirm Verwaltung", body)


def run() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    store = ItemStore(DB_PATH)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    server = ThreadingHTTPServer((host, port), InfoHandler)
    server.store = store  # type: ignore[attr-defined]
    server.admin_password = admin_password  # type: ignore[attr-defined]

    print(f"InfoBildschirm läuft auf http://{host}:{port}")
    if admin_password:
        print("Admin-Modus ist mit Passwort geschützt (HTTP Basic Auth).")
    else:
        print("WARNUNG: ADMIN_PASSWORD ist nicht gesetzt. Verwaltungsmodus ist ungeschützt.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
