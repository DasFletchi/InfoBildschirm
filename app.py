#!/usr/bin/env python3
"""InfoBildschirm: lightweight digital signage server for Linux devices.

Features:
- Slideshow display with fullscreen auto-scaled images, videos, web pages, and live weather
- Standby screen with large digital clock, date, and live weather card
- Admin panel with file upload, media browser, and 1-click weather toggle
- Automatic media cleanup with configurable retention period
- Weather widget via Open-Meteo API (no API key required)
- Zero external Python dependencies (stdlib only)
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote
from urllib.request import urlopen, Request
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = BASE_DIR / "media"
DB_PATH = DATA_DIR / "infobildschirm.db"

DEFAULT_DURATION_SECONDS = 15
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
CLEANUP_DAYS = int(os.getenv("CLEANUP_DAYS", "30"))
CLEANUP_MIN_FREE_MB = int(os.getenv("CLEANUP_MIN_FREE_MB", "500"))
CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour

WEATHER_LAT = os.getenv("WEATHER_LAT", "51.5338")
WEATHER_LON = os.getenv("WEATHER_LON", "9.9355")
WEATHER_LOCATION = os.getenv("WEATHER_LOCATION_NAME") or os.getenv("WEATHER_CITY") or "Göttingen"
WEATHER_CACHE_SECONDS = 900  # 15 minutes

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm"}

MIME_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".svg": "image/svg+xml", ".css": "text/css", ".js": "application/javascript",
}

# WMO Weather interpretation codes → (German description, emoji)
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Klar", "☀️"), 1: ("Überwiegend klar", "🌤️"),
    2: ("Teilweise bewölkt", "⛅"), 3: ("Bewölkt", "☁️"),
    45: ("Nebel", "🌫️"), 48: ("Nebel mit Reif", "🌫️"),
    51: ("Leichter Nieselregen", "🌦️"), 53: ("Nieselregen", "🌦️"),
    55: ("Starker Nieselregen", "🌦️"),
    61: ("Leichter Regen", "🌧️"), 63: ("Regen", "🌧️"),
    65: ("Starker Regen", "🌧️"),
    66: ("Gefrierender Regen", "🌧️"), 67: ("Starker gefr. Regen", "🌧️"),
    71: ("Leichter Schneefall", "🌨️"), 73: ("Schneefall", "🌨️"),
    75: ("Starker Schneefall", "❄️"), 77: ("Schneegriesel", "❄️"),
    80: ("Leichte Regenschauer", "🌦️"), 81: ("Regenschauer", "🌧️"),
    82: ("Starke Regenschauer", "🌧️"),
    85: ("Leichte Schneeschauer", "🌨️"), 86: ("Starke Schneeschauer", "❄️"),
    95: ("Gewitter", "⛈️"), 96: ("Gewitter mit Hagel", "⛈️"),
    99: ("Starkes Gewitter", "⛈️"),
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class ItemStore:
    """Manages playlist items and media tracking in SQLite."""

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
            # Main items table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL DEFAULT 15,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Media file tracking for auto-cleanup
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media_tracking (
                    filename TEXT PRIMARY KEY,
                    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    unreferenced_since TEXT
                )
            """)
        self._migrate_if_needed()

    def _migrate_if_needed(self) -> None:
        """Migrate from old schema (with CHECK constraint) if necessary."""
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO items(title, item_type, source, duration_seconds, sort_order, active) "
                    "VALUES('__migrate_test__', 'weather', 'weather://test', 10, 0, 0)"
                )
                conn.execute("DELETE FROM items WHERE title = '__migrate_test__'")
            except sqlite3.IntegrityError:
                conn.execute("ALTER TABLE items RENAME TO _items_old")
                conn.execute("""
                    CREATE TABLE items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        item_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        duration_seconds INTEGER NOT NULL DEFAULT 15,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("INSERT INTO items SELECT * FROM _items_old")
                conn.execute("DROP TABLE _items_old")

    # -- Item CRUD --

    def list_items(self) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, title, item_type, source, duration_seconds, sort_order, active
                FROM items ORDER BY active DESC, sort_order ASC, id ASC
            """).fetchall()
        return [dict(r) for r in rows]

    def list_active(self) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, title, item_type, source, duration_seconds
                FROM items WHERE active = 1
                ORDER BY sort_order ASC, id ASC
            """).fetchall()
        return [dict(r) for r in rows]

    def add_item(self, *, title: str, item_type: str, source: str,
                 duration_seconds: int, sort_order: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO items(title, item_type, source, duration_seconds, sort_order, active) "
                "VALUES(?, ?, ?, ?, ?, 1)",
                (title, item_type, source, duration_seconds, sort_order),
            )

    def edit_item(self, item_id: int, *, title: str, duration_seconds: int,
                  sort_order: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE items SET title=?, duration_seconds=?, sort_order=? WHERE id=?",
                (title, duration_seconds, sort_order, item_id),
            )

    def set_active(self, item_id: int, active: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE items SET active=? WHERE id=?",
                         (1 if active else 0, item_id))

    def delete(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM items WHERE id=?", (item_id,))

    def has_active_weather(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM items WHERE item_type = 'weather' AND active = 1 LIMIT 1"
            ).fetchone()
        return row is not None

    def toggle_weather_item(self) -> bool:
        """Toggles the weather widget slide in the playlist. Returns new active state."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, active FROM items WHERE item_type = 'weather' LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO items(title, item_type, source, duration_seconds, sort_order, active) "
                    "VALUES('🌤️ Live-Wetter', 'weather', 'weather://current', 15, 999, 1)"
                )
                return True
            else:
                new_active = 0 if row["active"] == 1 else 1
                conn.execute("UPDATE items SET active = ? WHERE id = ?", (new_active, row["id"]))
                return new_active == 1

    # -- Media tracking --

    def track_upload(self, filename: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO media_tracking(filename, uploaded_at, unreferenced_since) "
                "VALUES(?, CURRENT_TIMESTAMP, NULL)", (filename,))

    def get_referenced_media(self) -> set[str]:
        """Return set of filenames referenced by any playlist item."""
        with self._connect() as conn:
            rows = conn.execute("SELECT source FROM items").fetchall()
        refs: set[str] = set()
        for r in rows:
            src = str(r["source"])
            if src.startswith("/media/"):
                refs.add(src.removeprefix("/media/"))
        return refs

    def mark_unreferenced(self, filename: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE media_tracking SET unreferenced_since = CURRENT_TIMESTAMP "
                "WHERE filename = ? AND unreferenced_since IS NULL", (filename,))

    def clear_unreferenced(self, filename: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE media_tracking SET unreferenced_since = NULL WHERE filename = ?",
                (filename,))

    def reset_retention(self, filename: str) -> None:
        """Reset the cleanup timer (user clicked 'keep')."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO media_tracking(filename, uploaded_at, unreferenced_since) "
                "VALUES(?, COALESCE((SELECT uploaded_at FROM media_tracking WHERE filename=?), "
                "CURRENT_TIMESTAMP), NULL)", (filename, filename))

    def get_media_tracking(self) -> dict[str, dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT filename, uploaded_at, unreferenced_since FROM media_tracking"
            ).fetchall()
        return {r["filename"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# Weather Cache
# ---------------------------------------------------------------------------

class WeatherCache:
    """Fetches and caches weather data from Open-Meteo API."""

    def __init__(self) -> None:
        self._cache: dict[str, object] | None = None
        self._cache_time: float = 0
        self._lock = threading.Lock()

    def get(self) -> dict[str, object]:
        with self._lock:
            if self._cache and (time.time() - self._cache_time) < WEATHER_CACHE_SECONDS:
                return self._cache
        data = self._fetch()
        with self._lock:
            self._cache = data
            self._cache_time = time.time()
        return data

    def _fetch(self) -> dict[str, object]:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min"
            f"&timezone=auto&forecast_days=7"
        )
        try:
            req = Request(url, headers={"User-Agent": "InfoBildschirm/2.0"})
            with urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode("utf-8"))

            current = raw.get("current", {})
            daily = raw.get("daily", {})
            code = int(current.get("weather_code", 0))
            desc, emoji = WMO_CODES.get(code, ("Unbekannt", "❓"))

            dates = daily.get("time", [])
            codes = daily.get("weather_code", [])
            max_temps = daily.get("temperature_2m_max", [])
            min_temps = daily.get("temperature_2m_min", [])

            forecast_days = []
            for i in range(len(dates)):
                d_code = int(codes[i]) if i < len(codes) else 0
                d_desc, d_emoji = WMO_CODES.get(d_code, ("Unbekannt", "❓"))
                forecast_days.append({
                    "date": dates[i],
                    "code": d_code,
                    "desc": d_desc,
                    "emoji": d_emoji,
                    "max": max_temps[i] if i < len(max_temps) else None,
                    "min": min_temps[i] if i < len(min_temps) else None,
                })

            return {
                "location": WEATHER_LOCATION,
                "temperature": current.get("temperature_2m"),
                "humidity": current.get("relative_humidity_2m"),
                "wind_speed": current.get("wind_speed_10m"),
                "weather_code": code,
                "description": desc,
                "emoji": emoji,
                "forecast": forecast_days,
                "today_min": min_temps[0] if min_temps else None,
                "today_max": max_temps[0] if max_temps else None,
                "ok": True,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "location": WEATHER_LOCATION}


# ---------------------------------------------------------------------------
# Media Cleanup
# ---------------------------------------------------------------------------

class MediaCleaner:
    """Background thread that cleans up unreferenced media files."""

    def __init__(self, store: ItemStore) -> None:
        self.store = store
        self._stop = threading.Event()

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(CLEANUP_INTERVAL_SECONDS):
            try:
                self._cleanup()
            except Exception as exc:
                _log_cleanup(f"Fehler bei Bereinigung: {exc}")

    def _cleanup(self) -> None:
        if not MEDIA_DIR.exists():
            return

        referenced = self.store.get_referenced_media()
        disk = shutil.disk_usage(MEDIA_DIR)
        free_mb = disk.free // (1024 * 1024)
        retention_days = 7 if free_mb < CLEANUP_MIN_FREE_MB else CLEANUP_DAYS
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        for f in MEDIA_DIR.iterdir():
            if f.name == ".gitkeep" or not f.is_file():
                continue
            if f.name in referenced:
                self.store.clear_unreferenced(f.name)
                continue

            self.store.mark_unreferenced(f.name)

        tracking = self.store.get_media_tracking()
        for fname, info in tracking.items():
            fpath = MEDIA_DIR / fname
            if not fpath.exists():
                continue
            if fname in referenced:
                continue
            unref = info.get("unreferenced_since")
            if unref:
                try:
                    unref_dt = datetime.fromisoformat(unref).replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                if unref_dt < cutoff:
                    fpath.unlink(missing_ok=True)
                    _log_cleanup(f"Gelöscht: {fname} (unbenutzt seit {unref})")


def _log_cleanup(msg: str) -> None:
    log_path = DATA_DIR / "cleanup.log"
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Multipart Parser
# ---------------------------------------------------------------------------

def parse_multipart(content_type: str, body: bytes
                    ) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """Parse multipart/form-data. Returns (fields, files)."""
    match = re.search(r"boundary=([^\s;]+)", content_type)
    if not match:
        raise ValueError("Kein Boundary im Content-Type gefunden")
    boundary = match.group(1).strip('"').encode("utf-8")
    parts = body.split(b"--" + boundary)

    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}

    for part in parts[1:]:
        if part.strip() in (b"", b"--", b"--\r\n"):
            continue
        sep = part.find(b"\r\n\r\n")
        if sep == -1:
            continue
        header_block = part[:sep].decode("utf-8", errors="replace")
        data = part[sep + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]

        nm = re.search(r'name="([^"]*)"', header_block)
        fn = re.search(r'filename="([^"]*)"', header_block)
        if not nm:
            continue
        if fn and fn.group(1):
            files[nm.group(1)] = (fn.group(1), data)
        else:
            fields[nm.group(1)] = data.decode("utf-8", errors="replace")
    return fields, files


def sanitize_filename(name: str) -> str:
    """Make a filename filesystem-safe."""
    name = name.strip().replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^\w.\-]", "_", name)
    name = name.lstrip(".")
    if len(name) > 200:
        name = name[:200]
    return name or "upload"


def deduplicate_filename(directory: Path, name: str) -> str:
    """Add a numeric suffix if file already exists."""
    stem = Path(name).stem
    ext = Path(name).suffix
    if not (directory / name).exists():
        return name
    for i in range(1, 10000):
        candidate = f"{stem}_{i}{ext}"
        if not (directory / candidate).exists():
            return candidate
    return f"{stem}_{int(time.time())}{ext}"


# ---------------------------------------------------------------------------
# HTML Helpers
# ---------------------------------------------------------------------------

def normalize_source(source: str) -> str:
    src = source.strip()
    if not src:
        raise ValueError("Quelle darf nicht leer sein")
    if src.startswith("/"):
        return src
    if src.startswith("http://") or src.startswith("https://"):
        return src
    if src == "weather://current":
        return src
    raise ValueError("Quelle muss mit / oder http(s):// beginnen")


VALID_ITEM_TYPES = {"image", "video", "web", "weather"}


def html_page(title: str, body: str, extra_head: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
           margin: 0; background: #0b0f19; color: #f1f5f9; }}
    .container {{ max-width: 1050px; margin: 0 auto; padding: 1.5rem; }}
    a {{ color: #60a5fa; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .card {{ background: #131c31; border: 1px solid #1e293b; border-radius: 14px;
             padding: 1.3rem; margin-bottom: 1.3rem; box-shadow: 0 4px 20px rgba(0,0,0,0.25); }}
    input, select, textarea {{ width: 100%; padding: 0.65rem 0.8rem; margin: 0.25rem 0 0.8rem;
           background: #090d16; color: #f1f5f9; border: 1px solid #27354f; border-radius: 8px;
           font-size: 0.95rem; }}
    input:focus, select:focus {{ border-color: #3b82f6; outline: none;
           box-shadow: 0 0 0 3px rgba(59,130,246,0.25); }}
    label {{ font-size: 0.85rem; color: #94a3b8; font-weight: 500; }}
    button {{ cursor: pointer; background: #2563eb; color: white; border: 0;
              border-radius: 8px; padding: 0.65rem 1.2rem; font-size: 0.9rem;
              font-weight: 600; transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem; }}
    button:hover {{ background: #1d4ed8; transform: translateY(-1px); }}
    button:active {{ transform: translateY(0); }}
    .btn-success {{ background: #10b981; }}
    .btn-success:hover {{ background: #059669; }}
    .btn-danger {{ background: #ef4444; }}
    .btn-danger:hover {{ background: #dc2626; }}
    .btn-alt {{ background: #334155; }}
    .btn-alt:hover {{ background: #1e293b; }}
    .btn-sm {{ padding: 0.35rem 0.65rem; font-size: 0.8rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 0.65rem 0.5rem; border-bottom: 1px solid #1e293b;
              vertical-align: middle; font-size: 0.9rem; }}
    th {{ color: #64748b; font-weight: 600; font-size: 0.75rem; text-transform: uppercase;
         letter-spacing: 0.6px; }}
    code {{ background: #090d16; padding: 0.15rem 0.4rem; border-radius: 5px;
            font-size: 0.85rem; border: 1px solid #1e293b; color: #93c5fd; }}
    small {{ color: #64748b; }}
    .mode-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                  gap: 1.2rem; }}
    .mode-link {{ display: block; text-decoration: none; color: inherit; }}
    .mode-link:hover {{ text-decoration: none; }}
    .mode-link .card {{ transition: border-color 0.2s, transform 0.2s; }}
    .mode-link:hover .card {{ border-color: #3b82f6; transform: translateY(-3px); }}
    .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 9999px;
              font-size: 0.75rem; font-weight: 600; }}
    .badge-green {{ background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }}
    .badge-yellow {{ background: rgba(245,158,11,0.15); color: #fbbf24; border: 1px solid rgba(245,158,11,0.3); }}
    .badge-red {{ background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(239,68,68,0.3); }}
    .badge-blue {{ background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3); }}
    .thumb {{ width: 64px; height: 42px; object-fit: cover; border-radius: 6px;
              background: #090d16; border: 1px solid #1e293b; }}
    .upload-zone {{ border: 2px dashed #27354f; border-radius: 12px; padding: 2.2rem;
                    text-align: center; color: #94a3b8; transition: all 0.25s; cursor: pointer; }}
    .upload-zone:hover, .upload-zone.drag-over {{ border-color: #3b82f6; color: #60a5fa;
                    background: rgba(59,130,246,0.06); }}
    .upload-zone input[type=file] {{ display: none; }}
    .progress-bar {{ width: 100%; height: 7px; background: #090d16; border-radius: 4px;
                     overflow: hidden; margin-top: 0.6rem; display: none; }}
    .progress-fill {{ height: 100%; background: #3b82f6; width: 0%; transition: width 0.2s; }}
    .media-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
                   gap: 1.2rem; }}
    .media-card {{ background: #090d16; border-radius: 12px; overflow: hidden;
                   border: 1px solid #1e293b; transition: border-color 0.2s; }}
    .media-card:hover {{ border-color: #3b82f6; }}
    .media-card img, .media-card video {{ width: 100%; height: 120px; object-fit: cover;
                    display: block; background: #000; }}
    .media-card .info {{ padding: 0.75rem; }}
    .media-card .fname {{ font-size: 0.82rem; word-break: break-all; margin-bottom: 0.3rem; font-weight: 500; }}
    .media-card .meta {{ font-size: 0.72rem; color: #64748b; }}
    .flex-row {{ display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }}
    .toggle-card {{ background: linear-gradient(135deg, #131c31 0%, #172544 100%);
                    border: 1px solid #253961; padding: 1.2rem; border-radius: 14px;
                    display: flex; align-items: center; justify-content: space-between; gap: 1rem; }}
    @media (max-width: 640px) {{
      th:nth-child(4), td:nth-child(4) {{ display: none; }}
      .container {{ padding: 1rem; }}
      .toggle-card {{ flex-direction: column; align-items: flex-start; }}
    }}
  </style>
  {extra_head}
</head>
<body>
{body}
</body>
</html>""".encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class InfoHandler(BaseHTTPRequestHandler):
    server_version = "InfoBildschirm/2.0"

    @property
    def store(self) -> ItemStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def admin_password(self) -> str:
        return self.server.admin_password  # type: ignore[attr-defined]

    @property
    def weather(self) -> WeatherCache:
        return self.server.weather  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        if os.getenv("ENABLE_ACCESS_LOG") == "1":
            super().log_message(format, *args)

    # ---- GET routes ----

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        routes: dict[str, tuple[bool, object]] = {
            "/": (False, self._page_mode_picker),
            "/display": (False, self._page_display),
            "/manage": (True, self._page_manage),
            "/manage/media": (True, self._page_media_browser),
            "/api/items": (True, lambda: self._respond_json({"items": self.store.list_items()})),
            "/api/playlist": (False, lambda: self._respond_json({"items": self.store.list_active()})),
            "/api/weather": (False, lambda: self._respond_json(self.weather.get())),
        }

        if path in routes:
            needs_auth, handler = routes[path]
            if needs_auth and not self._require_auth():
                return
            handler()
            return

        if path.startswith("/media/"):
            self._serve_media(path)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    # ---- POST routes ----

    def do_POST(self) -> None:
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))

        if path == "/manage/upload":
            self._handle_upload(content_type, length)
            return

        if length > 10_000_000:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        raw = self.rfile.read(length).decode("utf-8")
        form = parse_qs(raw, keep_blank_values=True)

        if path == "/manage/add":
            self._handle_add(form)
        elif path == "/manage/edit":
            self._handle_edit(form)
        elif path == "/manage/toggle":
            self._handle_toggle(form)
        elif path == "/manage/toggle-weather":
            self._handle_toggle_weather()
        elif path == "/manage/delete":
            self._handle_delete(form)
        elif path == "/manage/media/delete":
            self._handle_media_delete(form)
        elif path == "/manage/media/keep":
            self._handle_media_keep(form)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # ---- POST handlers ----

    def _handle_add(self, form: dict[str, list[str]]) -> None:
        try:
            title = form.get("title", [""])[0].strip() or "Ohne Titel"
            item_type = form.get("item_type", [""])[0].strip()
            duration = int(form.get("duration", [str(DEFAULT_DURATION_SECONDS)])[0])
            sort_order = int(form.get("sort_order", ["0"])[0])
            if item_type not in VALID_ITEM_TYPES:
                raise ValueError("Ungültiger Typ")
            if item_type == "weather":
                source = "weather://current"
            else:
                source = normalize_source(form.get("source", [""])[0])
            if duration < 3:
                raise ValueError("Dauer muss mindestens 3 Sekunden sein")
        except (ValueError, TypeError) as exc:
            self._redirect(f"/manage?error={quote(str(exc))}")
            return

        self.store.add_item(title=title, item_type=item_type, source=source,
                            duration_seconds=duration, sort_order=sort_order)
        self._redirect("/manage")

    def _handle_edit(self, form: dict[str, list[str]]) -> None:
        try:
            item_id = int(form.get("id", ["0"])[0])
            title = form.get("title", [""])[0].strip() or "Ohne Titel"
            duration = int(form.get("duration", [str(DEFAULT_DURATION_SECONDS)])[0])
            sort_order = int(form.get("sort_order", ["0"])[0])
            if duration < 3:
                raise ValueError("Dauer muss mindestens 3 Sekunden sein")
        except (ValueError, TypeError):
            pass
        else:
            self.store.edit_item(item_id, title=title, duration_seconds=duration,
                                 sort_order=sort_order)
        self._redirect("/manage")

    def _handle_toggle(self, form: dict[str, list[str]]) -> None:
        try:
            item_id = int(form.get("id", ["0"])[0])
            active = form.get("active", ["0"])[0] == "1"
            self.store.set_active(item_id, active)
        except ValueError:
            pass
        self._redirect("/manage")

    def _handle_toggle_weather(self) -> None:
        self.store.toggle_weather_item()
        self._redirect("/manage")

    def _handle_delete(self, form: dict[str, list[str]]) -> None:
        try:
            item_id = int(form.get("id", ["0"])[0])
            self.store.delete(item_id)
        except ValueError:
            pass
        self._redirect("/manage")

    def _handle_upload(self, content_type: str, length: int) -> None:
        if length > MAX_UPLOAD_BYTES:
            self._redirect(f"/manage?error={quote('Datei ist zu groß (max ' + str(MAX_UPLOAD_BYTES // (1024*1024)) + ' MB)')}")
            return
        if "multipart/form-data" not in content_type:
            self._redirect(f"/manage?error={quote('Ungültiger Upload')}")
            return

        body = self.rfile.read(length)
        try:
            fields, files = parse_multipart(content_type, body)
        except Exception:
            self._redirect(f"/manage?error={quote('Upload konnte nicht verarbeitet werden')}")
            return

        if "file" not in files:
            self._redirect(f"/manage?error={quote('Keine Datei ausgewählt')}")
            return

        orig_name, data = files["file"]
        ext = Path(orig_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            self._redirect(f"/manage?error={quote('Dateityp nicht erlaubt: ' + ext)}")
            return

        safe_name = sanitize_filename(orig_name)
        safe_name = deduplicate_filename(MEDIA_DIR, safe_name)
        dest = MEDIA_DIR / safe_name
        dest.write_bytes(data)
        self.store.track_upload(safe_name)

        add_to_playlist = fields.get("add_to_playlist", "0") == "1"
        if add_to_playlist:
            title = fields.get("title", "").strip() or Path(safe_name).stem
            dur = int(fields.get("duration", str(DEFAULT_DURATION_SECONDS)))
            itype = "video" if ext in VIDEO_EXTENSIONS else "image"
            self.store.add_item(title=title, item_type=itype,
                                source=f"/media/{safe_name}",
                                duration_seconds=max(dur, 3), sort_order=0)

        self._redirect("/manage/media")

    def _handle_media_delete(self, form: dict[str, list[str]]) -> None:
        fname = form.get("filename", [""])[0].strip()
        if fname and not ("/" in fname or "\\" in fname or fname.startswith(".")):
            fpath = MEDIA_DIR / fname
            if fpath.exists():
                fpath.unlink()
                _log_cleanup(f"Manuell gelöscht: {fname}")
        self._redirect("/manage/media")

    def _handle_media_keep(self, form: dict[str, list[str]]) -> None:
        fname = form.get("filename", [""])[0].strip()
        if fname:
            self.store.reset_retention(fname)
        self._redirect("/manage/media")

    # ---- Auth ----

    def _require_auth(self) -> bool:
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
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="InfoBildschirm Admin"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authentifizierung erforderlich")
        return False

    # ---- Response helpers ----

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
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        with open(file_path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    # ================================================================
    # Page renderers
    # ================================================================

    def _page_mode_picker(self) -> None:
        body = """
<div class="container" style="display:flex;align-items:center;justify-content:center;min-height:90vh">
  <div style="max-width:580px;width:100%">
    <h1 style="text-align:center;font-weight:300;font-size:2.4rem;margin-bottom:0.3rem;letter-spacing:-0.5px">
      🖥️ InfoBildschirm</h1>
    <p style="text-align:center;color:#94a3b8;margin-bottom:2.2rem;font-size:1.05rem">Digitales Informationssystem</p>
    <div class="mode-grid">
      <a class="mode-link" href="/display">
        <div class="card" style="text-align:center;padding:2.2rem 1.2rem">
          <div style="font-size:3rem;margin-bottom:0.6rem">📺</div>
          <h2 style="margin:0 0 0.4rem;font-size:1.35rem">Anzeige-Modus</h2>
          <p style="color:#94a3b8;font-size:0.92rem;margin:0">Vollbild-Diashow für den Monitor</p>
        </div>
      </a>
      <a class="mode-link" href="/manage">
        <div class="card" style="text-align:center;padding:2.2rem 1.2rem">
          <div style="font-size:3rem;margin-bottom:0.6rem">⚙️</div>
          <h2 style="margin:0 0 0.4rem;font-size:1.35rem">Verwaltungs-Modus</h2>
          <p style="color:#94a3b8;font-size:0.92rem;margin:0">Inhalte hochladen & Playlist steuern</p>
        </div>
      </a>
    </div>
  </div>
</div>
"""
        self._respond_html(html_page("InfoBildschirm", body))

    def _page_display(self) -> None:
        body = """
<div id="display-wrap">
  <div id="slide-a" class="slide"></div>
  <div id="slide-b" class="slide"></div>
  <div id="start-hint">Drücke <kbd>F11</kbd> für Vollbild</div>
</div>
<style>
  html, body {
    margin: 0; padding: 0; width: 100vw; height: 100vh;
    overflow: hidden; cursor: none; background: #000;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  }
  #display-wrap {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: #000; overflow: hidden;
  }
  .slide {
    position: absolute; top: 0; left: 0; width: 100vw; height: 100vh;
    display: flex; align-items: center; justify-content: center;
    opacity: 0; transition: opacity 0.8s ease-in-out; z-index: 1;
    overflow: hidden;
  }
  .slide.active { opacity: 1; z-index: 2; }
  
  /* Fullscreen Auto-Scaling for Images & Videos */
  .slide img {
    width: 100vw; height: 100vh;
    object-fit: cover; object-position: center;
    display: block;
  }
  .slide video {
    width: 100vw; height: 100vh;
    object-fit: cover; object-position: center;
    display: block; background: #000;
  }
  .slide iframe {
    width: 100vw; height: 100vh; border: 0;
  }

  /* 7-Day Big Fullscreen Weather Slide */
  .slide .weather-container {
    width: 100vw; height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: radial-gradient(circle at center, #172554 0%, #0f172a 60%, #020617 100%);
    box-sizing: border-box; padding: 2rem;
  }
  .weather-slide-box {
    width: 94vw; max-width: 1400px;
    background: rgba(15, 23, 42, 0.8);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 36px; padding: 3rem 4rem;
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.7);
    color: #fff;
  }
  .weather-header {
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    padding-bottom: 1.2rem; margin-bottom: 2rem;
  }
  .weather-location {
    font-size: 2.5rem; font-weight: 700; color: #fff;
    margin: 0; letter-spacing: 0.5px; text-transform: uppercase;
  }
  .weather-tag {
    background: rgba(59, 130, 246, 0.25); border: 1px solid rgba(59, 130, 246, 0.5);
    color: #93c5fd; padding: 0.4rem 1.2rem; border-radius: 9999px;
    font-size: 1.1rem; font-weight: 600; letter-spacing: 1px;
  }
  
  .weather-hero {
    display: flex; align-items: center; justify-content: space-between;
    gap: 3rem; margin-bottom: 2.5rem;
  }
  .hero-left {
    display: flex; align-items: center; gap: 1.5rem;
  }
  .hero-emoji {
    font-size: 7.5rem; line-height: 1;
    filter: drop-shadow(0 10px 25px rgba(0,0,0,0.4));
  }
  .hero-temp-wrap {
    display: flex; align-items: flex-start;
  }
  .hero-temp {
    font-size: 7.5rem; font-weight: 200; line-height: 1; letter-spacing: -3px;
  }
  .hero-unit {
    font-size: 3rem; font-weight: 300; color: #93c5fd; margin-top: 0.5rem; margin-left: 0.2rem;
  }
  .hero-right {
    text-align: right;
  }
  .hero-desc {
    font-size: 2.8rem; font-weight: 600; color: #f1f5f9; margin-bottom: 0.8rem;
  }
  .hero-meta {
    display: flex; gap: 1.5rem; justify-content: flex-end;
    font-size: 1.25rem; color: #94a3b8;
  }
  .hero-meta span strong { color: #fff; }

  /* 7-Day Forecast Grid */
  .week-forecast-grid {
    display: grid; grid-template-columns: repeat(7, 1fr);
    gap: 1rem;
  }
  .forecast-card {
    background: rgba(30, 41, 59, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 20px; padding: 1.4rem 0.5rem;
    text-align: center; display: flex; flex-direction: column;
    align-items: center; justify-content: space-between;
  }
  .forecast-card.today-card {
    background: rgba(59, 130, 246, 0.2);
    border-color: rgba(59, 130, 246, 0.5);
    box-shadow: 0 0 25px rgba(59, 130, 246, 0.2);
  }
  .f-day {
    font-size: 1.35rem; font-weight: 700; color: #93c5fd; margin-bottom: 0.4rem;
  }
  .forecast-card.today-card .f-day { color: #60a5fa; }
  .f-emoji {
    font-size: 3.2rem; margin: 0.4rem 0; line-height: 1.1;
  }
  .f-temps {
    margin-top: 0.5rem;
  }
  .f-max {
    font-size: 1.6rem; font-weight: 700; color: #fff;
  }
  .f-min {
    font-size: 1.25rem; color: #94a3b8; margin-left: 0.3rem; font-weight: 400;
  }

  /* Standby Dashboard */
  .standby-wrap {
    width: 100vw; height: 100vh;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: radial-gradient(circle at center, #172554 0%, #0f172a 60%, #020617 100%);
    color: #fff; text-align: center; padding: 2rem; box-sizing: border-box;
  }
  .standby-clock {
    font-size: 8rem; font-weight: 200; letter-spacing: -2px;
    margin: 0; line-height: 1;
    text-shadow: 0 0 50px rgba(59, 130, 246, 0.4);
  }
  .standby-date {
    font-size: 2.6rem; color: #93c5fd; font-weight: 300;
    margin: 1.2rem 0 2.8rem 0; letter-spacing: 0.5px;
  }
  .standby-weather-box {
    background: rgba(15, 23, 42, 0.7);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 28px; padding: 1.8rem 3.5rem;
    display: inline-flex; align-items: center; gap: 2.5rem;
    box-shadow: 0 20px 45px rgba(0,0,0,0.5);
  }
  .standby-badge {
    margin-top: 2.8rem; display: inline-flex; align-items: center; gap: 0.6rem;
    background: rgba(255,255,255,0.06); padding: 0.6rem 1.4rem;
    border-radius: 9999px; font-size: 1rem; color: #94a3b8;
  }
  .pulse-dot {
    width: 10px; height: 10px; background: #10b981; border-radius: 50%;
    box-shadow: 0 0 12px #10b981; animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }

  #start-hint {
    position: fixed; bottom: 2rem; left: 50%; transform: translateX(-50%); z-index: 200;
    background: rgba(0,0,0,0.85); color: #94a3b8; padding: 0.6rem 1.6rem;
    border-radius: 9999px; font-size: 0.95rem; opacity: 1;
    transition: opacity 2s; pointer-events: none; border: 1px solid rgba(255,255,255,0.1);
  }
  #start-hint kbd { background: #334155; color: #fff; padding: 0.15rem 0.45rem; border-radius: 5px; font-size: 0.85rem; }
  #start-hint.hidden { opacity: 0; }
</style>
<script>
(function() {
  const slideA = document.getElementById('slide-a');
  const slideB = document.getElementById('slide-b');
  const hintEl = document.getElementById('start-hint');

  let items = [];
  let idx = 0;
  let useA = true;
  let advanceTimer = null;
  let latestWeather = null;

  setTimeout(() => hintEl.classList.add('hidden'), 5000);

  async function refreshWeather() {
    try {
      const r = await fetch('/api/weather', {cache:'no-store'});
      const data = await r.json();
      if (data.ok) {
        latestWeather = data;
      }
    } catch(e) {}
  }
  refreshWeather();
  setInterval(refreshWeather, 120000);

  async function loadPlaylist() {
    try {
      const r = await fetch('/api/playlist', {cache:'no-store'});
      const d = await r.json();
      items = d.items || [];
    } catch(e) {}
  }

  function createWeatherCard(w) {
    const container = document.createElement('div');
    container.className = 'weather-container';
    
    const days = w.forecast || [];
    const forecastHtml = days.map((day, dIdx) => {
      let dayName = 'Heute';
      if (dIdx === 1) dayName = 'Morgen';
      else if (dIdx > 1 && day.date) {
        const d = new Date(day.date + 'T12:00:00');
        dayName = d.toLocaleDateString('de-DE', { weekday: 'short' });
      }
      const minT = day.min != null ? Math.round(day.min) + '°' : '--';
      const maxT = day.max != null ? Math.round(day.max) + '°' : '--';
      const isToday = dIdx === 0 ? ' today-card' : '';
      return `
        <div class="forecast-card${isToday}">
          <div class="f-day">${dayName}</div>
          <div class="f-emoji">${day.emoji || '🌤️'}</div>
          <div class="f-temps">
            <span class="f-max">${maxT}</span>
            <span class="f-min">${minT}</span>
          </div>
        </div>
      `;
    }).join('');
    
    container.innerHTML = `
      <div class="weather-slide-box">
        <div class="weather-header">
          <h1 class="weather-location">${w.location || 'GÖTTINGEN'}</h1>
          <span class="weather-tag">7-TAGE-WETTER</span>
        </div>

        <div class="weather-hero">
          <div class="hero-left">
            <span class="hero-emoji">${w.emoji || '🌤️'}</span>
            <div class="hero-temp-wrap">
              <span class="hero-temp">${w.temperature != null ? Math.round(w.temperature) : '--'}</span>
              <span class="hero-unit">°C</span>
            </div>
          </div>
          <div class="hero-right">
            <div class="hero-desc">${w.description || 'Aktuelles Wetter'}</div>
            <div class="hero-meta">
              <span>💧 Luftfeuchte: <strong>${w.humidity != null ? w.humidity + '%' : '--'}</strong></span>
              <span>💨 Wind: <strong>${w.wind_speed != null ? Math.round(w.wind_speed) + ' km/h' : '--'}</strong></span>
              <span>🌡️ Heute: <strong>${w.today_min != null ? Math.round(w.today_min) + '°' : '--'} / ${w.today_max != null ? Math.round(w.today_max) + '°' : '--'}</strong></span>
            </div>
          </div>
        </div>

        <div class="week-forecast-grid">
          ${forecastHtml}
        </div>
      </div>
    `;
    return container;
  }

  function createStandbyScreen() {
    const wrap = document.createElement('div');
    wrap.className = 'standby-wrap';
    const now = new Date();
    const timeStr = now.toLocaleTimeString('de-DE', {hour:'2-digit', minute:'2-digit'});
    const dateStr = now.toLocaleDateString('de-DE', {weekday:'long', day:'numeric', month:'long', year:'numeric'});
    
    let weatherHtml = '';
    if (latestWeather && latestWeather.ok) {
      weatherHtml = `
        <div class="standby-weather-box">
          <div style="font-size:4rem">${latestWeather.emoji || '🌤️'}</div>
          <div style="text-align:left">
            <div style="font-size:2.6rem;font-weight:200">${Math.round(latestWeather.temperature)}°C</div>
            <div style="color:#94a3b8;font-size:1.2rem">${latestWeather.description || ''} · ${latestWeather.location || ''}</div>
          </div>
          <div style="border-left:1px solid rgba(255,255,255,0.12);padding-left:2rem;text-align:left;color:#94a3b8;font-size:1.05rem">
            <div>Heute: ${Math.round(latestWeather.today_min || 0)}° / ${Math.round(latestWeather.today_max || 0)}°</div>
            <div>Wind: ${Math.round(latestWeather.wind_speed || 0)} km/h</div>
          </div>
        </div>
      `;
    }

    wrap.innerHTML = `
      <div class="standby-clock">${timeStr}</div>
      <div class="standby-date">${dateStr}</div>
      ${weatherHtml}
      <div class="standby-badge">
        <div class="pulse-dot"></div>
        <span>InfoBildschirm betriebsbereit</span>
      </div>
    `;
    return wrap;
  }

  function transition(content) {
    const incoming = useA ? slideA : slideB;
    const outgoing = useA ? slideB : slideA;
    incoming.innerHTML = '';
    if (content) incoming.appendChild(content);
    incoming.classList.add('active');
    outgoing.classList.remove('active');
    useA = !useA;
  }

  async function advance() {
    await loadPlaylist();

    if (!items.length) {
      transition(createStandbyScreen());
      advanceTimer = setTimeout(advance, 3000);
      return;
    }

    const item = items[idx % items.length];
    idx++;
    let duration = (item.duration_seconds || 15) * 1000;

    if (item.item_type === 'image') {
      const img = new Image();
      img.src = item.source;
      img.onload = () => transition(img);
      img.onerror = () => { advanceTimer = setTimeout(advance, 2000); };
      advanceTimer = setTimeout(advance, duration);
    } else if (item.item_type === 'video') {
      const video = document.createElement('video');
      video.src = item.source;
      video.autoplay = true; video.muted = true; video.playsInline = true;
      video.onended = () => { clearTimeout(advanceTimer); advance(); };
      video.onerror = () => { clearTimeout(advanceTimer); advance(); };
      transition(video);
      advanceTimer = setTimeout(advance, Math.max(duration, 300000));
    } else if (item.item_type === 'weather') {
      await refreshWeather();
      if (latestWeather && latestWeather.ok) {
        transition(createWeatherCard(latestWeather));
      } else {
        advance();
        return;
      }
      advanceTimer = setTimeout(advance, duration);
    } else {
      const frame = document.createElement('iframe');
      frame.src = item.source;
      frame.referrerPolicy = 'no-referrer';
      frame.sandbox = 'allow-same-origin allow-scripts allow-forms';
      transition(frame);
      advanceTimer = setTimeout(advance, duration);
    }
  }

  advance();
})();
</script>
"""
        self._respond_html(html_page("InfoBildschirm Anzeige", body))

    def _page_manage(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        error = html.escape(params.get("error", [""])[0])
        error_html = f"<p style='color:#f87171;background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);padding:0.75rem 1.2rem;border-radius:10px;margin-bottom:1.2rem'>{error}</p>" if error else ""

        has_weather = self.store.has_active_weather()
        weather_btn_label = "✅ In Diashow aktiv (Klicken zum Deaktivieren)" if has_weather else "➕ Zu Diashow hinzufügen"
        weather_btn_class = "btn-success" if has_weather else "btn-alt"
        weather_badge = '<span class="badge badge-green">Aktiviert</span>' if has_weather else '<span class="badge badge-yellow">Nicht in Diashow</span>'

        # Build playlist rows
        rows = []
        for item in self.store.list_items():
            item_id = int(item["id"])
            active = bool(item["active"])
            itype = str(item["item_type"])
            source = str(item["source"])
            next_active = "0" if active else "1"
            btn_label = "Deaktivieren" if active else "Aktivieren"
            btn_class = "btn-alt btn-sm" if active else "btn-sm"

            thumb = ""
            if itype == "image" and source.startswith("/media/"):
                thumb = f'<img class="thumb" src="{html.escape(source)}" alt="" loading="lazy">'
            elif itype == "video":
                thumb = '<div class="thumb" style="display:flex;align-items:center;justify-content:center;font-size:1.4rem">🎬</div>'
            elif itype == "weather":
                thumb = '<div class="thumb" style="display:flex;align-items:center;justify-content:center;font-size:1.4rem">🌤️</div>'
            elif itype == "web":
                thumb = '<div class="thumb" style="display:flex;align-items:center;justify-content:center;font-size:1.4rem">🌐</div>'

            status_badge = '<span class="badge badge-green">Aktiv</span>' if active else '<span class="badge badge-yellow">Inaktiv</span>'

            rows.append(f"""
<tr>
  <td>{thumb}</td>
  <td><strong>{html.escape(str(item['title']))}</strong><br><small style="color:#64748b">{html.escape(itype)}</small></td>
  <td><code style="font-size:0.75rem">{html.escape(source[:45])}</code></td>
  <td>{int(item['duration_seconds'])}s</td>
  <td>{status_badge}</td>
  <td>
    <div class="flex-row">
      <form method="post" action="/manage/toggle" style="display:inline">
        <input type="hidden" name="id" value="{item_id}">
        <input type="hidden" name="active" value="{next_active}">
        <button class="{btn_class}" type="submit">{btn_label}</button>
      </form>
      <form method="post" action="/manage/delete" style="display:inline"
            onsubmit="return confirm('Eintrag wirklich löschen?')">
        <input type="hidden" name="id" value="{item_id}">
        <button class="btn-danger btn-sm" type="submit">Löschen</button>
      </form>
    </div>
  </td>
</tr>""")

        table_rows = "".join(rows) if rows else '<tr><td colspan="6" style="text-align:center;color:#64748b;padding:2.5rem">Keine Einträge in der Playlist. Lade unten eine Datei hoch oder aktiviere das Wetter!</td></tr>'

        body = f"""
<div class="container">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.8rem;margin-bottom:1.5rem">
    <h1 style="margin:0;font-weight:500;font-size:1.8rem">⚙️ Verwaltung</h1>
    <div class="flex-row">
      <a href="/manage/media"><button class="btn-alt">📁 Medien-Browser</button></a>
      <a href="/display" target="_blank"><button class="btn-alt">📺 Diashow öffnen</button></a>
      <a href="/"><button class="btn-alt" style="background:#1e293b">← Zurück</button></a>
    </div>
  </div>
  {error_html}

  <!-- Quick Weather Toggle Card -->
  <div class="card toggle-card">
    <div>
      <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.3rem">
        <span style="font-size:1.5rem">🌤️</span>
        <h3 style="margin:0;font-size:1.15rem;font-weight:600">Live-Wetter in Diashow</h3>
        {weather_badge}
      </div>
      <p style="margin:0;color:#94a3b8;font-size:0.88rem">
        Zeigt automatisch aktuelle Temperatur, Wetterlage und 2-Tage-Vorhersage in der Rotation.
      </p>
    </div>
    <form method="post" action="/manage/toggle-weather" style="margin:0">
      <button type="submit" class="{weather_btn_class}" style="white-space:nowrap">{weather_btn_label}</button>
    </form>
  </div>

  <!-- Upload Card -->
  <div class="card">
    <h2 style="margin-top:0;font-weight:500;font-size:1.3rem">📤 Datei hochladen</h2>
    <form method="post" action="/manage/upload" enctype="multipart/form-data" id="upload-form">
      <div class="upload-zone" id="drop-zone" onclick="document.getElementById('file-input').click()">
        <input type="file" name="file" id="file-input" accept=".jpg,.jpeg,.png,.gif,.webp,.mp4,.webm">
        <div style="font-size:2.5rem;margin-bottom:0.5rem">📁</div>
        <div style="font-size:1.05rem">Bilder oder Videos hierher ziehen oder <strong>klicken</strong></div>
        <div style="font-size:0.82rem;color:#64748b;margin-top:0.4rem">JPG, PNG, GIF, WebP, MP4, WebM – max. {MAX_UPLOAD_BYTES // (1024*1024)} MB</div>
        <div id="file-name" style="color:#60a5fa;margin-top:0.6rem;font-weight:600"></div>
      </div>
      <div class="progress-bar" id="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.8rem;margin-top:1rem">
        <div>
          <label>Titel (optional)</label>
          <input name="title" type="text" placeholder="z.B. Schulfest 2026">
        </div>
        <div>
          <label>Dauer (Sekunden)</label>
          <input name="duration" type="number" min="3" value="15">
        </div>
      </div>
      <div style="margin-top:0.2rem">
        <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer">
          <input type="checkbox" name="add_to_playlist" value="1" checked style="width:auto;margin:0">
          Direkt zur Diashow hinzufügen
        </label>
      </div>
      <button type="submit" style="margin-top:1rem;width:100%">⬆️ Datei hochladen</button>
    </form>
  </div>

  <!-- Add Item Card -->
  <div class="card">
    <h2 style="margin-top:0;font-weight:500;font-size:1.3rem">➕ Webseite oder Quelle hinzufügen</h2>
    <form method="post" action="/manage/add" id="add-form">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.8rem">
        <div>
          <label>Titel</label>
          <input name="title" type="text" placeholder="z.B. Vertretungsplan Online">
        </div>
        <div>
          <label>Typ</label>
          <select name="item_type" id="item-type-select">
            <option value="web">🌐 Webseite / Link</option>
            <option value="image">🖼️ Bild (URL / Pfad)</option>
            <option value="video">🎬 Video (URL / Pfad)</option>
            <option value="weather">🌤️ Wetter</option>
          </select>
        </div>
      </div>
      <div id="source-group">
        <label>Quelle / URL</label>
        <input name="source" type="text" id="source-input" placeholder="https://schule.de/vertretung oder /media/datei.jpg">
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.8rem">
        <div>
          <label>Dauer (Sekunden)</label>
          <input name="duration" type="number" min="3" value="15">
        </div>
        <div>
          <label>Sortierung (kleiner = früher)</label>
          <input name="sort_order" type="number" value="0">
        </div>
      </div>
      <button type="submit" style="margin-top:0.5rem">💾 Hinzufügen</button>
    </form>
  </div>

  <!-- Playlist Table -->
  <div class="card">
    <h2 style="margin-top:0;font-weight:500;font-size:1.3rem">📋 Playlist</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th style="width:70px"></th><th>Titel</th><th>Quelle</th><th>Dauer</th><th>Status</th><th>Aktion</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </div>
</div>

<script>
// Upload drag & drop
const zone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileName = document.getElementById('file-name');

['dragenter','dragover'].forEach(e => zone.addEventListener(e, ev => {{
  ev.preventDefault(); zone.classList.add('drag-over');
}}));
['dragleave','drop'].forEach(e => zone.addEventListener(e, ev => {{
  ev.preventDefault(); zone.classList.remove('drag-over');
}}));
zone.addEventListener('drop', ev => {{
  fileInput.files = ev.dataTransfer.files;
  fileName.textContent = ev.dataTransfer.files[0]?.name || '';
}});
fileInput.addEventListener('change', () => {{
  fileName.textContent = fileInput.files[0]?.name || '';
}});

// Upload progress via XHR
document.getElementById('upload-form').addEventListener('submit', function(e) {{
  const file = fileInput.files[0];
  if (!file) return;
  e.preventDefault();
  const form = new FormData(this);
  const xhr = new XMLHttpRequest();
  const bar = document.getElementById('progress-bar');
  const fill = document.getElementById('progress-fill');
  bar.style.display = 'block';
  xhr.upload.addEventListener('progress', ev => {{
    if (ev.lengthComputable) fill.style.width = Math.round(ev.loaded/ev.total*100) + '%';
  }});
  xhr.addEventListener('load', () => {{ window.location.href = '/manage/media'; }});
  xhr.addEventListener('error', () => {{ alert('Upload fehlgeschlagen'); bar.style.display='none'; }});
  xhr.open('POST', '/manage/upload');
  xhr.withCredentials = true;
  xhr.send(form);
}});

// Weather type: hide source field
const typeSelect = document.getElementById('item-type-select');
const sourceGroup = document.getElementById('source-group');
const sourceInput = document.getElementById('source-input');
typeSelect.addEventListener('change', function() {{
  if (this.value === 'weather') {{
    sourceGroup.style.display = 'none';
    sourceInput.removeAttribute('required');
  }} else {{
    sourceGroup.style.display = 'block';
  }}
}});
</script>
"""
        self._respond_html(html_page("InfoBildschirm Verwaltung", body))

    def _page_media_browser(self) -> None:
        referenced = self.store.get_referenced_media()
        tracking = self.store.get_media_tracking()

        disk = shutil.disk_usage(MEDIA_DIR)
        free_mb = disk.free // (1024 * 1024)
        used_mb = sum(f.stat().st_size for f in MEDIA_DIR.iterdir()
                      if f.is_file() and f.name != ".gitkeep") // (1024 * 1024)
        retention = 7 if free_mb < CLEANUP_MIN_FREE_MB else CLEANUP_DAYS

        cards = []
        for f in sorted(MEDIA_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not f.is_file() or f.name == ".gitkeep":
                continue
            ext = f.suffix.lower()
            size_kb = f.stat().st_size // 1024
            size_str = f"{size_kb} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            in_playlist = f.name in referenced
            info = tracking.get(f.name, {})
            unref_since = info.get("unreferenced_since")

            if in_playlist:
                badge = '<span class="badge badge-green">In Playlist</span>'
                extra_actions = ""
            elif unref_since:
                try:
                    unref_dt = datetime.fromisoformat(str(unref_since))
                    days_left = retention - (datetime.now() - unref_dt).days
                    days_left = max(days_left, 0)
                except (ValueError, TypeError):
                    days_left = retention
                badge_class = "badge-red" if days_left <= 7 else "badge-yellow"
                badge = f'<span class="badge {badge_class}">Löschung in {days_left} Tag{"en" if days_left != 1 else ""}</span>'
                extra_actions = f'''
                    <form method="post" action="/manage/media/keep" style="display:inline">
                      <input type="hidden" name="filename" value="{html.escape(f.name)}">
                      <button class="btn-sm" type="submit" title="Timer zurücksetzen">🔄 Behalten</button>
                    </form>'''
            else:
                badge = '<span class="badge badge-blue">Unbenutzt</span>'
                extra_actions = f'''
                    <form method="post" action="/manage/media/keep" style="display:inline">
                      <input type="hidden" name="filename" value="{html.escape(f.name)}">
                      <button class="btn-sm" type="submit" title="Timer zurücksetzen">🔄 Behalten</button>
                    </form>'''

            if ext in IMAGE_EXTENSIONS:
                preview = f'<img src="/media/{quote(f.name)}" alt="" loading="lazy">'
            elif ext in VIDEO_EXTENSIONS:
                preview = f'<video src="/media/{quote(f.name)}" muted preload="metadata"></video>'
            else:
                preview = '<div style="height:120px;display:flex;align-items:center;justify-content:center;background:#000;font-size:2.2rem">📄</div>'

            cards.append(f"""
<div class="media-card">
  {preview}
  <div class="info">
    <div class="fname">{html.escape(f.name)}</div>
    <div class="meta">{size_str}</div>
    <div style="margin:0.4rem 0">{badge}</div>
    <div class="flex-row">
      {extra_actions}
      <form method="post" action="/manage/media/delete" style="display:inline"
            onsubmit="return confirm('Datei endgültig löschen?')">
        <input type="hidden" name="filename" value="{html.escape(f.name)}">
        <button class="btn-danger btn-sm" type="submit">🗑️</button>
      </form>
    </div>
  </div>
</div>""")

        cards_html = "".join(cards) if cards else '<p style="color:#64748b;text-align:center;padding:3rem">Noch keine Medien-Dateien hochgeladen.</p>'

        body = f"""
<div class="container">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.8rem;margin-bottom:1.5rem">
    <h1 style="margin:0;font-weight:500;font-size:1.8rem">📁 Medien-Browser</h1>
    <div class="flex-row">
      <a href="/manage"><button class="btn-alt">← Verwaltung</button></a>
    </div>
  </div>

  <div class="card" style="display:flex;gap:2rem;flex-wrap:wrap;align-items:center;padding:1rem 1.4rem">
    <div><span style="color:#94a3b8">Speicher belegt:</span> <strong>{used_mb} MB</strong></div>
    <div><span style="color:#94a3b8">Freier Speicher:</span> <strong>{free_mb} MB</strong></div>
    <div><span style="color:#94a3b8">Bereinigung nach:</span> <strong>{retention} Tagen</strong></div>
    <div><span style="color:#94a3b8">Dateien:</span> <strong>{len(cards)}</strong></div>
  </div>

  <div class="media-grid">
    {cards_html}
  </div>
</div>
"""
        self._respond_html(html_page("Medien-Browser", body))


# ---------------------------------------------------------------------------
# Server Entry Point
# ---------------------------------------------------------------------------

def run() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    store = ItemStore(DB_PATH)
    weather_cache = WeatherCache()

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    server = ThreadingHTTPServer((host, port), InfoHandler)
    server.store = store  # type: ignore[attr-defined]
    server.admin_password = admin_password  # type: ignore[attr-defined]
    server.weather = weather_cache  # type: ignore[attr-defined]

    cleaner = MediaCleaner(store)
    cleaner.start()

    print(f"InfoBildschirm läuft auf http://{host}:{port}")
    if admin_password:
        print("Admin-Modus ist mit Passwort geschützt (HTTP Basic Auth).")
    else:
        print("Hinweis: ADMIN_PASSWORD ist nicht gesetzt. Verwaltungsmodus ist ungeschützt.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleaner.stop()
        server.server_close()


if __name__ == "__main__":
    run()
