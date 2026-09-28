import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from tracker.config import load_config
from tracker.engine import Engine, TabCache, clean_domain
from tracker.server import Server
from tracker.storage import Store


def snapshot(name="code.exe", title="Editor", **kwargs):
    return dict(app_name=name, process_name=name, icon_key="a" * 24, window_title=title, idle_seconds=0, locked=False, **kwargs)


class TrackerFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.config = load_config(self.path)
        self.store = Store(self.path / "test.sqlite3", self.config["device_id"], "test-user")
        self.tabs = TabCache()
        self.engine = Engine(self.store, self.config, self.tabs)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()


class TrackerTests(TrackerFixture):
    def test_switch_has_contiguous_non_overlapping_periods(self):
        for t in (100, 101, 102):
            self.engine.step(snapshot(), t)
        self.engine.step(snapshot("notepad.exe"), 103)
        self.engine.step(snapshot("notepad.exe"), 105)
        self.engine.stop()
        rows = self.store.query(0, 1000)
        self.assertEqual([r["duration_seconds"] for r in rows], [2, 3])
        self.assertEqual(rows[1]["end_epoch"], rows[0]["start_epoch"])

    def test_sleep_gap_is_not_counted(self):
        self.engine.step(snapshot(), 100)
        self.engine.step(snapshot(), 101)
        self.engine.step(snapshot(), 1000)
        self.engine.step(snapshot(), 1001)
        self.engine.stop()
        self.assertEqual(sum(r["duration_seconds"] for r in self.store.query(0, 2000)), 2)

    def test_backwards_clock_does_not_double_count(self):
        self.engine.step(snapshot(), 100)
        self.engine.step(snapshot(), 103)
        self.engine.step(snapshot(), 101)
        self.assertIsNone(self.engine.current)
        self.engine.step(snapshot(), 102)
        self.engine.step(snapshot(), 103)
        self.engine.step(snapshot(), 104)
        self.engine.stop()
        rows = self.store.query(0, 2000)
        self.assertEqual(sum(r["duration_seconds"] for r in rows), 4)
        self.assertEqual(rows[0]["start_epoch"], rows[1]["end_epoch"])

    def test_idle_locked_paused_and_excluded_states(self):
        self.engine.step(snapshot(), 100)
        idle = snapshot()
        idle["idle_seconds"] = 301
        self.engine.step(idle, 101)
        self.assertEqual(self.engine.state, "Idle")
        self.engine.step({"locked": True}, 102)
        self.assertEqual(self.engine.state, "Locked or unavailable")
        self.config["paused"] = True
        self.engine.step(snapshot(), 103)
        self.assertIsNone(self.engine.current)
        self.config["paused"] = False
        self.config["excluded_apps"] = ["code.exe"]
        self.engine.step(snapshot(), 104)
        self.assertEqual(self.engine.state, "Excluded app")
        self.assertEqual(sum(r["duration_seconds"] for r in self.store.query(0, 1000)), 1)

    def test_browser_report_matching_and_redaction(self):
        self.tabs.update(dict(browser="chrome", focused=True, title="Example", url="https://user:secret@example.com/private?q=token#fragment"), 100)
        self.engine.step(snapshot("chrome.exe", "Example - Google Chrome"), 100)
        self.assertEqual(self.engine.current["domain"], "example.com")
        self.assertEqual(self.engine.current["tab_title"], "Example")
        self.engine.step(snapshot("chrome.exe", "Different - Google Chrome"), 101)
        self.assertIsNone(self.engine.current["domain"])
        self.assertEqual(self.engine.current["window_title"], "")
        self.engine.step(snapshot("chrome.exe", "Example - Google Chrome"), 180)
        self.assertIsNone(self.engine.current["tab_title"])

    def test_private_browser_never_persists_title_without_report(self):
        self.engine.step(snapshot("msedge.exe", "Sensitive page"), 100)
        self.assertEqual(self.engine.current["window_title"], "")
        self.engine.step(snapshot("msedge.exe", "Sensitive page - InPrivate"), 101)
        self.assertIsNone(self.engine.current)

    def test_domain_exclusions_fail_closed_and_match_subdomains(self):
        self.config["excluded_domains"] = ["example.com"]
        self.engine.step(snapshot("chrome.exe", "Example - Google Chrome"), 100)
        self.assertIsNone(self.engine.current)
        self.tabs.update(dict(browser="chrome", focused=True, title="Example", url="https://login.example.com"), 101)
        self.engine.step(snapshot("chrome.exe", "Example - Google Chrome"), 101)
        self.assertIsNone(self.engine.current)
        self.tabs.update(dict(browser="chrome", focused=True, title="Example", url="https://safe.test"), 102)
        self.engine.step(snapshot("chrome.exe", "Example - Google Chrome"), 102)
        self.assertEqual(self.engine.current["domain"], "safe.test")

    def test_disable_title_capture(self):
        self.config["capture_window_titles"] = False
        self.engine.step(snapshot(), 100)
        self.assertEqual(self.engine.current["window_title"], "")

    def test_day_boundary_clips_duration(self):
        key = self.store.begin(snapshot(), 86390)
        self.store.extend(key, 86420)
        self.assertEqual(self.store.query(0, 86400)[0]["duration_seconds"], 10)
        self.assertEqual(self.store.query(86400, 172800)[0]["duration_seconds"], 20)

    def test_sync_acknowledgement_preserves_newer_updates(self):
        key = self.store.begin(snapshot(), 100)
        pending = self.store.pending()
        self.store.extend(key, 105)
        self.store.acknowledge(pending)
        self.assertEqual(self.store.pending_count(), 1)
        self.store.acknowledge(self.store.pending())
        self.assertEqual(self.store.pending_count(), 0)

    def test_crash_recovery_does_not_extend_previous_session(self):
        key = self.store.begin(snapshot(), 100)
        self.store.extend(key, 105)
        engine = Engine(self.store, self.config, self.tabs)
        engine.step(snapshot(), 1000)
        engine.step(snapshot(), 1001)
        engine.stop()
        self.assertEqual(sum(r["duration_seconds"] for r in self.store.query(0, 2000)), 6)

    def test_url_scheme_and_malformed_url(self):
        for url in ("file:///secret", "chrome://settings", "http://[invalid", "javascript:alert(1)"):
            self.assertIsNone(clean_domain(url))


class ApiTests(TrackerFixture):
    def setUp(self):
        super().setUp()
        self.server = Server(self.engine, self.store, self.tabs, self.config, SimpleNamespace(state="Not configured", last_success=None), self.path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, path, data=None, headers=None):
        headers = headers or {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_reject_cross_origin_and_dns_rebinding(self):
        self.assertEqual(self.request("/api/settings", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/settings", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/settings", headers={"Origin": "chrome-extension://" + "a" * 32})[0], 401)

    def test_mutation_needs_token_and_valid_settings(self):
        self.assertEqual(self.request("/api/settings", {"paused": True})[0], 401)
        headers = {"Authorization": "Bearer " + self.config["token"]}
        self.assertEqual(self.request("/api/settings", {"paused": True}, headers)[0], 200)
        self.assertTrue(self.config["paused"])
        self.assertEqual(self.request("/api/settings", {"idle_seconds": -1}, headers)[0], 400)
        self.assertEqual(self.request("/api/settings", {"token": "replacement"}, headers)[0], 400)

    def test_csv_formula_escape_and_json_export(self):
        key = self.store.begin(snapshot(title="=malicious-formula"), 100)
        self.store.extend(key, 102)
        status, body, headers = self.request("/api/export?start=0&end=200&format=csv")
        self.assertEqual(status, 200)
        self.assertIn("'=malicious-formula", body.decode("utf-8"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(json.loads(self.request("/api/export?start=0&end=200")[1])[0]["duration_seconds"], 2)

    def test_invalid_range_and_path(self):
        self.assertEqual(self.request("/api/activity?start=nan&end=2")[0], 400)
        self.assertEqual(self.request("/api/activity?start=0&end=99999999")[0], 400)
        self.assertEqual(self.request("/../config.json")[0], 404)

    def test_extension_ingest_and_static_app(self):
        headers = {"Authorization": "Bearer " + self.config["token"], "Origin": "chrome-extension://" + "a" * 32}
        status, _, returned = self.request("/api/tab", dict(browser="edge", focused=True, title="Page", url="https://example.com/a?b=secret"), headers)
        self.assertEqual(status, 200)
        self.assertEqual(returned["Access-Control-Allow-Origin"], headers["Origin"])
        self.assertEqual(self.tabs.entries["edge"]["domain"], "example.com")
        self.assertEqual(self.request("/")[0], 200)
        self.assertIn("text/javascript", self.request("/app.js")[2]["Content-Type"])


if __name__ == "__main__":
    unittest.main()
