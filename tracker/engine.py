"""Interval state machine, independent of Windows and wall-clock polling."""
import threading
import time
from urllib.parse import urlsplit

BROWSERS = {"chrome.exe": "chrome", "msedge.exe": "edge", "firefox.exe": "firefox", "brave.exe": "brave"}


def clean_domain(url):
    try:
        parsed = urlsplit(url)
        return parsed.hostname.lower() if parsed.scheme in ("https", "http") and parsed.hostname else None
    except ValueError:
        return None


class TabCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}

    def update(self, payload, now=None):
        now = time.time() if now is None else now
        browser = payload.get("browser")
        if browser not in ("chrome", "edge", "brave", "firefox"):
            raise ValueError("Unsupported browser")
        title = payload.get("title", "")
        if not isinstance(title, str) or len(title) > 4096:
            raise ValueError("Invalid title")
        url = payload.get("url", "")
        if not isinstance(url, str) or len(url) > 8192:
            raise ValueError("Invalid URL")
        with self.lock:
            self.entries[browser] = {
                "received": now,
                "focused": payload.get("focused") is True,
                "private": payload.get("private") is True,
                "title": title,
                "domain": clean_domain(url),
                "favicon_key": payload.get("favicon_key"),
            }

    def match(self, browser, window_title, now):
        with self.lock:
            entry = self.entries.get(browser, {}).copy()
        if not entry or now - entry["received"] > 75 or not entry["focused"]:
            return None
        # Do not attribute another browser window/profile's tab to this window.
        title = entry["title"].strip()
        if entry["private"]:
            return entry
        if not title or not window_title.startswith(title):
            return None
        return entry


class Engine:
    def __init__(self, store, config, tabs):
        self.store, self.config, self.tabs = store, config, tabs
        self.lock = threading.RLock()
        self.current = None
        self.interval_id = None
        self.last_seen = None
        self.last_flush = 0
        self.state = "Starting"

    def close_interval(self, at):
        if self.interval_id:
            self.store.extend(self.interval_id, max(self.current["since"], at))
        self.current = self.interval_id = None

    def step(self, snapshot, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if self.last_seen is not None and now < self.last_seen:
                # Wait for wall time to catch up instead of creating overlapping
                # records when Windows corrects its clock backwards.
                self.close_interval(self.last_seen)
                self.state = "Clock changed; waiting"
                return
            # Sleep, suspended process, or clock jump: never fill unobserved time.
            gap = self.last_seen is not None and now - self.last_seen > 5
            if gap:
                self.close_interval(self.last_seen)
            self.last_seen = now
            activity = None
            self.state = "Tracking"
            if self.config["paused"]:
                self.state = "Paused"
            elif not snapshot or snapshot.get("locked"):
                self.state = "Locked or unavailable"
            elif snapshot.get("idle_seconds", 0) >= self.config["idle_seconds"]:
                self.state = "Idle"
            elif snapshot["process_name"].lower() in self.config["excluded_apps"]:
                self.state = "Excluded app"
            else:
                activity = {k: snapshot[k] for k in ("app_name", "process_name", "icon_key", "window_title")}
                browser = BROWSERS.get(activity["process_name"].lower())
                activity.update(browser=browser, tab_title=None, domain=None, favicon_key=None)
                entry = self.tabs.match(browser, activity["window_title"], now) if browser else None
                # Browsers expose private mode in their title even without an extension.
                private_title = browser and any(label in activity["window_title"].lower() for label in ("inprivate", "incognito", "private browsing"))
                if private_title or (entry and entry["private"]):
                    activity, self.state = None, "Private browsing"
                elif entry:
                    activity.update(tab_title=entry["title"], domain=entry["domain"], favicon_key=entry.get("favicon_key"))
                    if any(entry["domain"] == d or (entry["domain"] or "").endswith("." + d) for d in self.config["excluded_domains"]):
                        activity, self.state = None, "Excluded website"
                elif browser and self.config["excluded_domains"]:
                    # Fail closed if exclusions cannot be checked against a current tab.
                    activity, self.state = None, "Waiting for browser extension"
                elif browser:
                    # Without a matching extension report, retain only the browser
                    # identity. Do not persist a potentially private tab title.
                    activity["window_title"] = ""
                if activity and not self.config["capture_window_titles"]:
                    activity["window_title"] = ""
                    activity["tab_title"] = None
            key = tuple(activity.values()) if activity else None
            current_key = self.current["key"] if self.current else None
            if key != current_key:
                self.close_interval(now)
                if activity:
                    self.interval_id = self.store.begin(activity, now)
                    self.current = {**activity, "key": key, "since": now}
                    self.last_flush = now
            elif self.current and now - self.last_flush >= self.config.get("checkpoint_seconds", 60):
                self.store.extend(self.interval_id, now)
                self.last_flush = now

    def status(self):
        with self.lock:
            return {"state": self.state, "paused": self.config["paused"], "current": {k: v for k, v in self.current.items() if k != "key"} if self.current else None}

    def pause(self, value):
        with self.lock:
            self.config["paused"] = value
            if value:
                self.close_interval(time.time())
                self.state = "Paused"
            else:
                self.state = "Starting"

    def flush(self):
        with self.lock:
            if self.current and self.last_seen is not None:
                self.store.extend(self.interval_id, self.last_seen)

    def stop(self):
        with self.lock:
            if self.last_seen is not None:
                self.close_interval(self.last_seen)
