"""Idempotent outbound sync; failed uploads stay queued in SQLite."""
import json
import logging
import time

DDL = """
CREATE TABLE IF NOT EXISTS activity_intervals (
    id UUID PRIMARY KEY, device_id UUID NOT NULL, username TEXT NOT NULL,
    app_name TEXT NOT NULL, process_name TEXT NOT NULL, icon_key TEXT NOT NULL,
    window_title TEXT NOT NULL, browser TEXT, tab_title TEXT, domain TEXT,
    started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ NOT NULL,
    revision BIGINT NOT NULL, CHECK (ended_at >= started_at)
);
CREATE INDEX IF NOT EXISTS activity_intervals_time ON activity_intervals (device_id, started_at);
"""
COLUMNS = ("id", "device_id", "username", "app_name", "process_name", "icon_key", "window_title", "browser", "tab_title", "domain", "started_at", "ended_at", "revision")
UPSERT = f"""INSERT INTO activity_intervals ({','.join(COLUMNS)}) VALUES ({','.join('%s' for _ in COLUMNS)})
ON CONFLICT (id) DO UPDATE SET ended_at=EXCLUDED.ended_at, revision=EXCLUDED.revision
WHERE activity_intervals.revision < EXCLUDED.revision"""


def load_connection(path):
    from .secrets import unprotect
    config = json.loads(path.read_text(encoding="utf-8"))
    connection = {key: config[key] for key in ("host", "port", "dbname", "user", "sslmode", "sslrootcert") if key in config}
    if connection.get("sslmode") != "verify-full":
        raise ValueError("Cloud sync requires sslmode=verify-full")
    connection["password"] = unprotect(config["password_encrypted"])
    connection.update(connect_timeout=10, application_name="ComputerTypology", options="-c statement_timeout=15000")
    return connection


class SyncWorker:
    def __init__(self, store, data_dir, stop):
        self.store, self.data_dir, self.stop = store, data_dir, stop
        self.state = "Not configured"
        self.last_success = None

    def once(self):
        path = self.data_dir / "cloud-config.json"
        if not path.exists():
            self.state = "Not configured"
            return
        import psycopg
        connection = load_connection(path)
        self.state = "Connecting"
        with psycopg.connect(**connection) as db:
            db.execute("SELECT id FROM activity_intervals LIMIT 0")
            rows = self.store.pending()
            if rows:
                with db.cursor() as cursor:
                    cursor.executemany(UPSERT, [tuple(row[c] for c in COLUMNS) for row in rows])
            db.commit()
            self.store.acknowledge(rows)
        self.last_success = time.time()
        self.state = "Connected"

    def run(self):
        delay = 1
        while not self.stop.wait(delay):
            try:
                self.once()
                delay = 60
            except Exception as error:
                # Do not log connection strings, passwords, or activity contents.
                logging.warning("PostgreSQL sync failed (%s)", type(error).__name__)
                self.state = "Connection failed; saved locally"
                delay = min(max(delay * 2, 15), 300)

