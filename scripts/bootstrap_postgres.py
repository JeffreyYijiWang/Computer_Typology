"""Called by asm-exec; administrator credentials exist only in this process."""
import argparse
import os
import secrets
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker.config import DATA_DIR
from tracker.sync import DDL
from configure_postgres import save_connection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--database', default='typology')
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ca = DATA_DIR / 'rds-ca.pem'
    with urllib.request.urlopen('https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem', timeout=30) as response:
        ca.write_bytes(response.read())
    import psycopg
    from psycopg import sql
    connection = dict(host=args.host, port=args.port, dbname=args.database,
                      user=os.environ.pop('TYPOLOGY_ADMIN_USER'),
                      password=os.environ.pop('TYPOLOGY_ADMIN_PASSWORD'),
                      sslmode='verify-full', sslrootcert=str(ca), connect_timeout=20)
    password = secrets.token_urlsafe(40)
    with psycopg.connect(**connection) as db:
        db.execute(DDL)
        exists = db.execute("SELECT 1 FROM pg_roles WHERE rolname='typology_writer'").fetchone()
        command = 'ALTER ROLE' if exists else 'CREATE ROLE'
        db.execute(sql.SQL(command + ' typology_writer LOGIN PASSWORD {}').format(sql.Literal(password)))
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO typology_writer').format(sql.Identifier(args.database)))
        db.execute('GRANT USAGE ON SCHEMA public TO typology_writer')
        db.execute('GRANT SELECT, INSERT, UPDATE ON activity_events, activity_contexts, activity_devices, activity_favicons TO typology_writer')
        db.execute('GRANT SELECT ON activity_intervals TO typology_writer')
    connection.update(user='typology_writer', password=password)
    with psycopg.connect(**connection) as db:
        db.execute('SELECT id FROM activity_intervals LIMIT 0')
        assert db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]
    save_connection(connection)
    print('Schema and restricted writer ready; TLS verified; password saved with Windows DPAPI.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # A driver error can contain connection details. Never echo it here.
        print('Database bootstrap failed (' + type(error).__name__ + '). Check endpoint, firewall and stack status.', file=sys.stderr)
        raise SystemExit(1)
