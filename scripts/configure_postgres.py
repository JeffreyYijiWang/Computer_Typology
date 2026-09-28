"""Interactive setup: never put a database password in shell history."""
import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker.config import DATA_DIR
from tracker.secrets import protect
from tracker.sync import DDL


def save_connection(connection, data_dir=DATA_DIR):
    data_dir.mkdir(parents=True, exist_ok=True)
    config = {k: v for k, v in connection.items() if k not in ("password", "connect_timeout")}
    config["password_encrypted"] = protect(connection["password"])
    path = data_dir / "cloud-config.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description="Connect the tracker to PostgreSQL with TLS certificate verification.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", default="typology")
    parser.add_argument("--user", default="typology_writer")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--ca", required=True, help="Path to the trusted PostgreSQL CA bundle")
    parser.add_argument("--initialize", action="store_true", help="Create the table using a role with CREATE permission")
    args = parser.parse_args()
    import psycopg
    ca = str(Path(args.ca).resolve(strict=True))
    password = getpass.getpass("Database password (not displayed): ")
    connection = dict(host=args.host, port=args.port, dbname=args.database, user=args.user, password=password, sslmode="verify-full", sslrootcert=ca, connect_timeout=10)
    with psycopg.connect(**connection) as db:
        if args.initialize:
            db.execute(DDL)
        db.execute("SELECT id FROM activity_intervals LIMIT 0")
    save_connection(connection)
    print("Connection verified and saved. The password is protected with Windows DPAPI. Sync starts within five minutes.")


if __name__ == "__main__":
    main()
