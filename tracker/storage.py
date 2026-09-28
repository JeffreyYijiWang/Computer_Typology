import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path, device_id, username):
        self.device_id, self.username = device_id, username
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS intervals (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL, username TEXT NOT NULL,
                app_name TEXT NOT NULL, process_name TEXT NOT NULL, icon_key TEXT NOT NULL,
                window_title TEXT NOT NULL, browser TEXT, tab_title TEXT, domain TEXT,
                started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
                start_epoch REAL NOT NULL, end_epoch REAL NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1, synced_revision INTEGER NOT NULL DEFAULT 0,
                CHECK (end_epoch >= start_epoch)
            );
            CREATE INDEX IF NOT EXISTS intervals_time ON intervals(end_epoch, start_epoch);
        """)

    def begin(self, activity, now):
        row = {k: activity.get(k) for k in ("app_name", "process_name", "icon_key", "window_title", "browser", "tab_title", "domain")}
        row.update(id=str(uuid.uuid4()), device_id=self.device_id, username=self.username,
                   started_at=stamp(now), ended_at=stamp(now), start_epoch=now, end_epoch=now)
        with self.lock, self.db:
            columns = ",".join(row)
            self.db.execute(f"INSERT INTO intervals ({columns}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
        return row["id"]

    def extend(self, interval_id, now):
        with self.lock, self.db:
            self.db.execute("UPDATE intervals SET end_epoch=MAX(start_epoch,?), ended_at=?, revision=revision+1 WHERE id=?",
                            (now, stamp(now), interval_id))

    def query(self, start, end):
        with self.lock:
            rows = self.db.execute("SELECT * FROM intervals WHERE end_epoch>? AND start_epoch<? ORDER BY start_epoch DESC", (start, end)).fetchall()
        result = []
        for source in rows:
            row = dict(source)
            row["duration_seconds"] = max(0, min(row["end_epoch"], end) - max(row["start_epoch"], start))
            row.pop("synced_revision", None)
            result.append(row)
        return result

    def pending(self, limit=500):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM intervals WHERE revision>synced_revision ORDER BY start_epoch LIMIT ?", (limit,)).fetchall()]

    def acknowledge(self, rows):
        with self.lock, self.db:
            self.db.executemany("UPDATE intervals SET synced_revision=MAX(synced_revision,?) WHERE id=?", [(r["revision"], r["id"]) for r in rows])

    def pending_count(self):
        with self.lock:
            return self.db.execute("SELECT count(*) FROM intervals WHERE revision>synced_revision").fetchone()[0]

    def close(self):
        with self.lock:
            self.db.close()

