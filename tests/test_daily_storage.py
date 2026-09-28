import base64
import io
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from tracker.storage import Store
from tracker.sync import SyncWorker, latest_due


ACTIVITY = dict(app_name='Chrome', process_name='chrome.exe', icon_key='a'*24,
                window_title='Example - Google Chrome', browser='chrome',
                tab_title='Example', domain='example.com')


class DailyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Store(self.path/'activity.sqlite3', 'device', 'Jeffr')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_repeated_context_and_checkpoints_do_not_duplicate_data(self):
        for n in range(4):
            key = self.store.begin(ACTIVITY, 100+n*20)
            self.store.extend(key, 110+n*20)
            revision = self.store.pending()[-1]['revision']
            self.store.extend(key, 110+n*20)
            self.assertEqual(self.store.pending()[-1]['revision'], revision)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM contexts').fetchone()[0], 1)
        rows = self.store.query(0,1000)
        self.assertEqual(sum(r['duration_seconds'] for r in rows), 40)
        self.assertEqual(rows[0]['window_title'], '')
        self.assertEqual(rows[0]['tab_title'], 'Example')

    def test_cutoff_and_revision_ack_preserve_later_activity(self):
        key = self.store.begin(ACTIVITY, 100)
        self.store.extend(key, 110)
        batch = self.store.pending(cutoff=111)
        self.store.extend(key, 112)
        self.store.acknowledge(batch)
        self.assertEqual(self.store.pending(cutoff=111), [])
        self.assertEqual(self.store.pending_count(), 1)
        self.assertEqual(self.store.pending(cutoff=113)[0]['end_epoch'],112)

    def test_favicons_are_validated_and_saved_once(self):
        output = io.BytesIO()
        Image.new('RGBA',(32,32),'blue').save(output,format='PNG')
        encoded = base64.b64encode(output.getvalue()).decode()
        key = self.store.save_favicon(encoded)
        self.assertEqual(key,self.store.save_favicon(encoded))
        self.assertEqual(len(self.store.pending_favicons()),1)
        self.assertEqual(self.store.favicon(key),output.getvalue())
        self.store.acknowledge_favicons([key])
        self.assertEqual(self.store.pending_favicons(),[])
        for invalid in ('not base64',base64.b64encode(b'<svg/>').decode(),'A'*23000):
            with self.assertRaises(ValueError): self.store.save_favicon(invalid)

    def test_daily_slot_survives_restart_and_catches_up_after_sleep(self):
        now=datetime(2026,9,28,23,54).timestamp()
        worker=SyncWorker(self.store,self.path,threading.Event(),{'sync_time':'23:55'})
        self.assertTrue(worker.should_upload(now))
        self.store.set_meta('last_sync_slot',latest_due(now,'23:55'))
        self.assertFalse(worker.should_upload(now))
        self.assertTrue(worker.should_upload(datetime(2026,9,29,8).timestamp()))
        self.store.set_meta('last_sync_slot',latest_due(datetime(2026,9,29,8).timestamp(),'23:55'))
        restarted=SyncWorker(self.store,self.path,threading.Event(),{'sync_time':'23:55'})
        self.assertFalse(restarted.should_upload(datetime(2026,9,29,9).timestamp()))
        restarted.request()
        self.assertTrue(restarted.should_upload(now))

    def test_failed_upload_does_not_acknowledge_data_or_schedule(self):
        self.store.begin(ACTIVITY,100)
        (self.path/'cloud-config.json').write_text('{}')
        worker=SyncWorker(self.store,self.path,threading.Event(),{'sync_time':'23:55'})
        with patch('tracker.sync.load_connection',return_value={}), patch('psycopg.connect',side_effect=OSError):
            with self.assertRaises(OSError): worker.once(now=200)
        self.assertEqual(self.store.pending_count(),1)
        self.assertIsNone(self.store.meta('last_sync_slot'))

    def test_legacy_migration_retains_ids_timing_revisions_and_backup(self):
        path=self.path/'legacy.sqlite3'
        db=sqlite3.connect(path)
        fields='id TEXT,device_id TEXT,username TEXT,app_name TEXT,process_name TEXT,icon_key TEXT,window_title TEXT,browser TEXT,tab_title TEXT,domain TEXT,start_epoch REAL,end_epoch REAL,revision INT,synced_revision INT'
        db.execute('CREATE TABLE intervals('+fields+')')
        db.execute('INSERT INTO intervals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('id','device','Jeffr',*ACTIVITY.values(),100.123,110.456,3,2))
        db.commit();db.close()
        migrated=Store(path,'device','Jeffr')
        try:
            row=migrated.pending()[0]
            self.assertEqual((row['id'],row['start_epoch'],row['end_epoch'],row['revision'],row['synced_revision']),('id',100.123,110.456,3,2))
            self.assertTrue((self.path/'legacy.pre-v2.sqlite3').exists())
            self.assertEqual(migrated.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally: migrated.close()
