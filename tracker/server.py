import csv
import hmac
import io
import json
import math
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import APP_DIR, PORT, save_config


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, engine, store, tabs, config, sync, data_dir, port=PORT):
        self.engine, self.store, self.tabs = engine, store, tabs
        self.config, self.sync, self.data_dir = config, sync, data_dir
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ComputerTypology"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def guard(self):
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.reply(403, {"error": "Use the local dashboard address"})
            return False
        origin = self.headers.get("Origin", "")
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}") and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
            self.reply(403, {"error": "Origin not allowed"})
            return False
        return True

    def authorized(self):
        return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.config["token"])

    def reply(self, status, body, content_type="application/json; charset=utf-8", filename=None):
        data = json.dumps(body).encode() if content_type.startswith("application/json") else body
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        origin = self.headers.get("Origin", "")
        if re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        if self.guard():
            self.reply(200, {})

    def do_GET(self):
        if not self.guard():
            return
        if self.headers.get("Origin", "").startswith("chrome-extension://") and not self.authorized():
            return self.reply(401, {"error": "Pair the extension first"})
        path = urlsplit(self.path)
        try:
            if path.path == "/api/status":
                self.server.engine.flush()
                return self.reply(200, {**self.server.engine.status(), "sync": {
                    "state": self.server.sync.state, "last_success": self.server.sync.last_success,
                    "pending": self.server.store.pending_count()}, "username": self.server.store.username})
            if path.path == "/api/settings":
                return self.reply(200, {**self.server.config, "extension_path": str(APP_DIR / "extension"), "data_path": str(self.server.data_dir)})
            if path.path in ("/api/activity", "/api/export"):
                query = parse_qs(path.query)
                start, end = float(query["start"][0]), float(query["end"][0])
                if not all(map(math.isfinite, (start, end))) or not 0 < end - start <= 32 * 86400:
                    raise ValueError("Select a range of at most 32 days")
                self.server.engine.flush()
                rows = self.server.store.query(start, end)
                if path.path == "/api/export":
                    if query.get("format", ["json"])[0] == "csv":
                        buffer = io.StringIO(newline="")
                        fields = ("id", "device_id", "username", "app_name", "process_name", "window_title", "browser", "tab_title", "domain", "started_at", "ended_at", "duration_seconds")
                        writer = csv.DictWriter(buffer, fields, extrasaction="ignore")
                        writer.writeheader()
                        for row in rows:
                            safe = {k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v) for k, v in row.items()}
                            writer.writerow(safe)
                        return self.reply(200, "\ufeff" + buffer.getvalue(), "text/csv; charset=utf-8", "typology-activity.csv")
                    return self.reply(200, rows, filename="typology-activity.json")
                totals = {}
                for row in rows:
                    key = row["process_name"]
                    item = totals.setdefault(key, {"app_name": row["app_name"], "icon_key": row["icon_key"], "seconds": 0})
                    item["seconds"] += row["duration_seconds"]
                return self.reply(200, {"intervals": rows, "apps": sorted(totals.values(), key=lambda x: -x["seconds"]), "total_seconds": sum(r["duration_seconds"] for r in rows)})
            if re.fullmatch(r"/icons/[a-f0-9]{24}\.png", path.path):
                target = self.server.data_dir / "icons" / path.path.rsplit("/", 1)[-1]
                if target.is_file():
                    return self.reply(200, target.read_bytes(), "image/png")
                return self.reply(200, (APP_DIR / "web" / "app-icon.svg").read_bytes(), "image/svg+xml")
            files = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css", "/app-icon.svg": "app-icon.svg"}
            if path.path in files:
                target = APP_DIR / "web" / files[path.path]
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".js":
                    content_type = "text/javascript"
                return self.reply(200, target.read_bytes(), content_type)
            return self.reply(404, {"error": "Not found"})
        except (ValueError, KeyError, IndexError) as error:
            return self.reply(400, {"error": str(error)})

    def do_POST(self):
        if not self.guard():
            return
        if not self.authorized():
            return self.reply(401, {"error": "Pairing token required"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 16384:
                raise ValueError("Invalid request size")
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("Expected JSON")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            if self.path == "/api/tab":
                self.server.tabs.update(payload)
                return self.reply(200, {"ok": True})
            if self.path == "/api/settings":
                if self.headers.get("Origin", "").startswith("chrome-extension://"):
                    return self.reply(403, {"error": "Use the dashboard to change settings"})
                updated = self.server.config.copy()
                for key, value in payload.items():
                    if key in ("paused", "capture_window_titles") and isinstance(value, bool):
                        updated[key] = value
                    elif key == "idle_seconds" and type(value) is int and 30 <= value <= 3600:
                        updated[key] = value
                    elif key in ("excluded_apps", "excluded_domains") and isinstance(value, list) and len(value) <= 100 and all(isinstance(v, str) and len(v) <= 253 for v in value):
                        updated[key] = sorted(set(v.strip().lower() for v in value if v.strip()))
                    else:
                        raise ValueError("Invalid setting: " + key)
                with self.server.engine.lock:
                    self.server.engine.pause(updated["paused"])
                    self.server.config.update(updated)
                    save_config(self.server.config, self.server.data_dir)
                return self.reply(200, {"ok": True})
            return self.reply(404, {"error": "Not found"})
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return self.reply(400, {"error": str(error)})

