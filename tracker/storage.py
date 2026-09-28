"""Normalized SQLite storage. Migrate v1 atomically, keeping a backup."""
import base64
import hashlib
import io
import json
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone

CONTEXT_FIELDS = ("app_name", "process_name", "icon_key", "window_title", "browser", "tab_title", "domain", "favicon_key")


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path, device_id, username):
        self.device_id, self.username = device_id, username
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        legacy = self.db.execute("SELECT type FROM sqlite_master WHERE name='intervals'").fetchone()
        migrate = legacy and legacy["type"] == "table"
        if migrate:
            backup = path.with_name(path.stem + ".pre-v2.sqlite3")
            if not backup.exists():
                with closing(sqlite3.connect(str(backup))) as destination:
                    self.db.backup(destination)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                device_key INTEGER PRIMARY KEY, device_id TEXT UNIQUE NOT NULL, username TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contexts (
                context_key INTEGER PRIMARY KEY, context_id TEXT UNIQUE NOT NULL,
                app_name TEXT NOT NULL, process_name TEXT NOT NULL, icon_key TEXT NOT NULL,
                window_title TEXT NOT NULL, browser TEXT, tab_title TEXT, domain TEXT, favicon_key TEXT);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, device_key INTEGER NOT NULL REFERENCES devices(device_key),
                context_key INTEGER NOT NULL REFERENCES contexts(context_key),
                start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1, synced_revision INTEGER NOT NULL DEFAULT 0,
                CHECK(end_ms >= start_ms));
            CREATE INDEX IF NOT EXISTS events_time ON events(end_ms, start_ms);
            CREATE INDEX IF NOT EXISTS events_pending ON events(start_ms) WHERE revision>synced_revision;
            CREATE TABLE IF NOT EXISTS favicons (
                hash TEXT PRIMARY KEY, png BLOB NOT NULL, uploaded INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if migrate:
            with self.db:
                for source in self.db.execute("SELECT * FROM intervals").fetchall():
                    row = dict(source)
                    self._insert(row, row["start_epoch"], row["end_epoch"], row["id"], row["device_id"], row["username"], row["revision"], row["synced_revision"])
                old_count = self.db.execute("SELECT COUNT(*) FROM intervals").fetchone()[0]
                new_count = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if new_count != old_count:
                    raise RuntimeError("Migration count mismatch; original history preserved")
                self.db.execute("DROP TABLE intervals")
        self.db.execute("""CREATE VIEW IF NOT EXISTS intervals AS
            SELECT e.id,d.device_id,d.username,c.context_id,c.app_name,c.process_name,c.icon_key,
              c.window_title,c.browser,c.tab_title,c.domain,c.favicon_key,
              strftime('%Y-%m-%dT%H:%M:%fZ',e.start_ms/1000.0,'unixepoch') AS started_at,
              strftime('%Y-%m-%dT%H:%M:%fZ',e.end_ms/1000.0,'unixepoch') AS ended_at,
              e.start_ms/1000.0 AS start_epoch,e.end_ms/1000.0 AS end_epoch,e.revision,e.synced_revision
            FROM events e JOIN contexts c USING(context_key) JOIN devices d USING(device_key)""")
        self.db.commit()
        if migrate:
            self.db.execute("VACUUM")

    def _insert(self, activity, start, end, identifier, device_id, username, revision=1, synced=0):
        # Browser tab title contains the useful title; avoid its duplicate.
        context = {k: activity.get(k) for k in CONTEXT_FIELDS}
        if context["tab_title"]:
            context["window_title"] = ""
        context_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(context, sort_keys=True, ensure_ascii=False)))
        self.db.execute("INSERT OR IGNORE INTO devices(device_id,username) VALUES (?,?)", (device_id, username))
        self.db.execute(f"INSERT OR IGNORE INTO contexts(context_id,{','.join(CONTEXT_FIELDS)}) VALUES ({','.join('?' for _ in range(9))})", [context_id, *context.values()])
        device_key = self.db.execute("SELECT device_key FROM devices WHERE device_id=?", (device_id,)).fetchone()[0]
        context_key = self.db.execute("SELECT context_key FROM contexts WHERE context_id=?", (context_id,)).fetchone()[0]
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (identifier, device_key, context_key, round(start*1000), round(end*1000), revision, synced))

    def begin(self, activity, now):
        identifier = str(uuid.uuid4())
        with self.lock, self.db:
            self._insert(activity, now, now, identifier, self.device_id, self.username)
        return identifier

    def extend(self, interval_id, now):
        milliseconds = round(now * 1000)
        with self.lock, self.db:
            self.db.execute("UPDATE events SET end_ms=?, revision=revision+1 WHERE id=? AND end_ms<?", (milliseconds, interval_id, milliseconds))

    def query(self, start, end):
        with self.lock:
            rows = self.db.execute("SELECT * FROM intervals WHERE id IN (SELECT id FROM events WHERE end_ms>? AND start_ms<?) ORDER BY start_epoch DESC", (round(start*1000), round(end*1000))).fetchall()
        result = []
        for source in rows:
            row = dict(source)
            row["duration_seconds"] = max(0, min(row["end_epoch"], end) - max(row["start_epoch"], start))
            row.pop("synced_revision", None)
            result.append(row)
        return result

    def pending(self, limit=500, cutoff=None):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM intervals WHERE id IN (SELECT id FROM events WHERE revision>synced_revision AND end_ms<=? ORDER BY start_ms LIMIT ?) ORDER BY start_epoch", (round(cutoff*1000) if cutoff is not None else 9223372036854775807, limit)).fetchall()]

    def acknowledge(self, rows):
        with self.lock, self.db:
            self.db.executemany("UPDATE events SET synced_revision=MAX(synced_revision,?) WHERE id=?", [(r["revision"], r["id"]) for r in rows])

    def pending_count(self):
        with self.lock:
            return self.db.execute("SELECT count(*) FROM events WHERE revision>synced_revision").fetchone()[0]

    def meta(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def save_favicon(self, encoded):
        if not isinstance(encoded, str) or len(encoded) > 22000:
            raise ValueError("Favicon too large")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid favicon encoding") from error
        if len(raw) > 16384:
            raise ValueError("Favicon too large")
        key = hashlib.sha256(raw).hexdigest()
        with self.lock:
            if self.db.execute("SELECT 1 FROM favicons WHERE hash=?", (key,)).fetchone():
                return key
        from PIL import Image
        try:
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format != "PNG" or picture.size != (32, 32):
                    raise ValueError("Expected a 32 x 32 PNG")
                picture.verify()
        except Exception as error:
            raise ValueError("Invalid favicon image") from error
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO favicons(hash,png) VALUES (?,?)", (key, raw))
        return key

    def favicon(self, key):
        with self.lock:
            row = self.db.execute("SELECT png FROM favicons WHERE hash=?", (key,)).fetchone()
            return bytes(row[0]) if row else None

    def pending_favicons(self, limit=100):
        with self.lock:
            return self.db.execute("SELECT hash,png FROM favicons WHERE uploaded=0 LIMIT ?", (limit,)).fetchall()

    def acknowledge_favicons(self, keys):
        with self.lock, self.db:
            self.db.executemany("UPDATE favicons SET uploaded=1 WHERE hash=?", [(k,) for k in keys])

    def close(self):
        with self.lock:
            self.db.close()

