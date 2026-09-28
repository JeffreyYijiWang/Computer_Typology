"""Reproducible synthetic size comparison, without reading personal history."""
import argparse
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker.storage import Store, stamp


def measure(count, unique):
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'example.sqlite3'
        db=sqlite3.connect(path)
        db.executescript('''CREATE TABLE intervals (
            id TEXT PRIMARY KEY,device_id TEXT NOT NULL,username TEXT NOT NULL,
            app_name TEXT NOT NULL,process_name TEXT NOT NULL,icon_key TEXT NOT NULL,
            window_title TEXT NOT NULL,browser TEXT,tab_title TEXT,domain TEXT,
            started_at TEXT NOT NULL,ended_at TEXT NOT NULL,start_epoch REAL NOT NULL,
            end_epoch REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
            synced_revision INTEGER NOT NULL DEFAULT 0,CHECK(end_epoch>=start_epoch));
            CREATE INDEX intervals_time ON intervals(end_epoch,start_epoch);''')
        device=str(uuid.uuid4())
        rows=[]
        for n in range(count):
            title=f'Document {n%unique} - project notes and reference material'
            start=1790568000+n*30
            rows.append((str(uuid.uuid4()),device,'Jeffr','Microsoft Edge','msedge.exe','a'*24,
                         title+' - Microsoft Edge','edge',title,'example.com',stamp(start),stamp(start+30),start,start+30,1,1))
        db.executemany('INSERT INTO intervals VALUES ('+','.join('?'*16)+')',rows)
        db.commit(); db.close()
        old=path.stat().st_size
        store=Store(path,device,'Jeffr')
        assert store.db.execute('SELECT count(*) FROM events').fetchone()[0]==count
        store.close()
        new=path.stat().st_size
        return dict(events=count,distinct_contexts=unique,old_bytes=old,new_bytes=new,
                    percent_smaller=100*(1-new/old),bytes_per_event=new/count,
                    estimated_mb_year_at_500_daily=new/count*500*365/1_000_000)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
    result=[measure(10000,100),measure(10000,10000)]
    output=json.dumps(result,indent=2)
    if args.output:Path(args.output).write_text(output)
    print(output)
