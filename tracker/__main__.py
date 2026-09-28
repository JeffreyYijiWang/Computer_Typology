import ctypes
import getpass
import logging
import logging.handlers
import os
import threading
import webbrowser
from ctypes import wintypes

from .config import DATA_DIR, PORT, load_config, save_config
from .engine import Engine, TabCache
from .server import Server
from .storage import Store
from .sync import SyncWorker


def main():
    if os.name != "nt":
        raise SystemExit("The recorder requires Windows 10 or 11.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, False, "Local\\ComputerTypology-" + getpass.getuser())
    if not mutex or ctypes.get_last_error() == 183:
        return
    config = load_config()
    handler = logging.handlers.RotatingFileHandler(DATA_DIR / "tracker.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    from .windows import WindowsCollector
    from PIL import Image, ImageDraw
    import pystray

    store = Store(DATA_DIR / "activity.sqlite3", config["device_id"], getpass.getuser())
    tabs, stop = TabCache(), threading.Event()
    engine = Engine(store, config, tabs)
    collector = WindowsCollector(DATA_DIR / "icons")
    sync = SyncWorker(store, DATA_DIR, stop)
    try:
        server = Server(engine, store, tabs, config, sync, DATA_DIR)
    except OSError:
        logging.exception("Cannot start local dashboard")
        store.close()
        return

    def collect():
        while not stop.is_set():
            try:
                engine.step(collector.snapshot())
            except Exception:
                logging.exception("Activity sample failed")
                engine.step(None)
            stop.wait(config["poll_seconds"])

    def toggle(_icon=None, _item=None):
        engine.pause(not config["paused"])
        save_config(config)

    def quit_app(icon, _item):
        stop.set()
        icon.stop()

    picture = Image.new("RGBA", (64, 64), (18, 25, 37, 255))
    drawing = ImageDraw.Draw(picture)
    for x, top in ((14, 30), (28, 14), (42, 23)):
        drawing.rounded_rectangle((x, top, x + 8, 49), radius=3, fill="#63e8be")
    tray = pystray.Icon("Computer Typology", picture, "Computer Typology · activity tracker", menu=pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda: webbrowser.open(f"http://127.0.0.1:{PORT}"), default=True),
        pystray.MenuItem(lambda _: "Resume tracking" if config["paused"] else "Pause tracking", toggle),
        pystray.MenuItem("Quit", quit_app),
    ))
    workers = [threading.Thread(target=server.serve_forever, daemon=True), threading.Thread(target=collect, daemon=True), threading.Thread(target=sync.run, daemon=True)]
    for worker in workers:
        worker.start()
    logging.info("Tracker started")
    try:
        tray.run()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        workers[1].join(timeout=5)
        workers[2].join(timeout=30)
        engine.stop()
        store.close()
        logging.info("Tracker stopped")
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
