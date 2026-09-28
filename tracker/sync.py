"""Daily outbound sync with durable scheduling and retry-safe batches."""
import json
import logging
import threading
import time
from datetime import datetime, timedelta

from .storage import CONTEXT_FIELDS

DDL = """
CREATE TABLE IF NOT EXISTS activity_favicons (
    hash TEXT PRIMARY KEY, png BYTEA NOT NULL, CHECK(octet_length(png)<=16384));
CREATE TABLE IF NOT EXISTS activity_devices (
    device_id UUID PRIMARY KEY, username TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity_contexts (
    context_id UUID PRIMARY KEY, app_name TEXT NOT NULL, process_name TEXT NOT NULL,
    icon_key TEXT NOT NULL, window_title TEXT NOT NULL, browser TEXT, tab_title TEXT,
    domain TEXT, favicon_key TEXT REFERENCES activity_favicons(hash));
CREATE TABLE IF NOT EXISTS activity_events (
    id UUID PRIMARY KEY, device_id UUID NOT NULL REFERENCES activity_devices(device_id),
    context_id UUID NOT NULL REFERENCES activity_contexts(context_id),
    started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ NOT NULL,
    revision BIGINT NOT NULL, CHECK(ended_at>=started_at));
CREATE INDEX IF NOT EXISTS activity_events_time ON activity_events(device_id,started_at);
CREATE OR REPLACE VIEW activity_intervals AS
    SELECT e.id,e.device_id,d.username,c.app_name,c.process_name,c.icon_key,
           c.window_title,c.browser,c.tab_title,c.domain,e.started_at,e.ended_at,
           e.revision,c.favicon_key,c.context_id
    FROM activity_events e JOIN activity_devices d USING(device_id)
         JOIN activity_contexts c USING(context_id);
"""
COLUMNS = ("id", "device_id", "context_id", "started_at", "ended_at", "revision")
UPSERT = """INSERT INTO activity_events(id,device_id,context_id,started_at,ended_at,revision)
VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE
SET ended_at=EXCLUDED.ended_at, revision=EXCLUDED.revision
WHERE activity_events.revision<EXCLUDED.revision"""


def latest_due(now, daily_time):
    """Use OS local timezone rules, including DST and catch-up after sleep."""
    local = datetime.fromtimestamp(now)
    hour, minute = map(int, daily_time.split(":"))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due.timestamp() > now:
        due -= timedelta(days=1)
    return due.timestamp()


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
    def __init__(self, store, data_dir, stop, config=None, flush=None):
        self.store, self.data_dir, self.stop = store, data_dir, stop
        self.config = config if config is not None else {"sync_time": "23:55"}
        self.flush = flush
        self.state = "Daily upload ready" if (data_dir / "cloud-config.json").exists() else "Not configured"
        self.last_success = store.meta("last_sync")
        self.wake = threading.Event()
        self.manual = threading.Event()

    def request(self):
        self.manual.set()
        self.wake.set()

    def once(self, now=None):
        path = self.data_dir / "cloud-config.json"
        if not path.exists():
            self.state = "Not configured"
            return False
        import psycopg
        connection = load_connection(path)
        self.state = "Uploading"
        if self.flush:
            self.flush()
        cutoff = time.time() if now is None else now
        with psycopg.connect(**connection) as db:
            # Upload each tiny icon once, before rows referencing it.
            while assets := self.store.pending_favicons():
                with db.cursor() as cursor:
                    cursor.executemany("INSERT INTO activity_favicons(hash,png) VALUES (%s,%s) ON CONFLICT DO NOTHING", [(r["hash"], bytes(r["png"])) for r in assets])
                db.commit()
                self.store.acknowledge_favicons([r["hash"] for r in assets])
            # A fixed cutoff prevents a growing current interval from keeping
            # this loop alive. Later revisions remain queued for tomorrow.
            while rows := self.store.pending(cutoff=cutoff):
                with db.cursor() as cursor:
                    devices = {(r["device_id"], r["username"]) for r in rows}
                    cursor.executemany("INSERT INTO activity_devices VALUES (%s,%s) ON CONFLICT DO NOTHING", list(devices))
                    contexts = {r["context_id"]: (r["context_id"], *(r[k] for k in CONTEXT_FIELDS)) for r in rows}
                    cursor.executemany("INSERT INTO activity_contexts VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", list(contexts.values()))
                    cursor.executemany(UPSERT, [tuple(row[c] for c in COLUMNS) for row in rows])
                db.commit()
                self.store.acknowledge(rows)
        self.last_success = time.time()
        self.store.set_meta("last_sync", self.last_success)
        self.store.set_meta("last_sync_slot", latest_due(cutoff, self.config["sync_time"]))
        self.state = "Daily upload ready"
        return True

    def should_upload(self, now):
        return self.manual.is_set() or latest_due(now, self.config["sync_time"]) > self.store.meta("last_sync_slot", 0)

    def run(self):
        retry_at, failures = 0, 0
        while not self.stop.is_set():
            now = time.time()
            configured = (self.data_dir / "cloud-config.json").exists()
            if not configured:
                self.state = "Not configured"
                self.manual.clear()
            elif self.should_upload(now) and (now >= retry_at or self.manual.is_set()):
                self.manual.clear()
                try:
                    self.once()
                    failures, retry_at = 0, 0
                except Exception as error:
                    logging.warning("PostgreSQL sync failed (%s)", type(error).__name__)
                    self.state = "Upload failed; saved locally"
                    failures += 1
                    retry_at = now + min(60 * 2 ** min(failures - 1, 6), 3600)
            elif not failures:
                self.state = "Daily upload ready"
            self.wake.wait(60)
            self.wake.clear()

