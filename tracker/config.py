import json
import os
import secrets
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
# A dedicated home folder avoids MSIX AppData virtualization when installed
# from a packaged terminal, and resolves identically during Windows login.
DATA_DIR = Path(os.environ.get("TYPOLOGY_DATA_DIR", str(Path.home() / ".computer-typology")))
PORT = 43128


def load_config(data_dir=DATA_DIR):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "config.json"
    defaults = {
        "device_id": str(uuid.uuid4()),
        "token": secrets.token_urlsafe(32),
        "idle_seconds": 300,
        "poll_seconds": 1,
        "checkpoint_seconds": 60,
        "sync_time": "23:55",
        "paused": False,
        "excluded_apps": [],
        "excluded_domains": [],
        "capture_window_titles": True,
    }
    if path.exists():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    else:
        save_config(defaults, data_dir)
    return defaults


def save_config(config, data_dir=DATA_DIR):
    path = data_dir / "config.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temporary.replace(path)

