import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from maintenance_cleanup import apply_cleanup, cleanup_candidates


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cleanup-test-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "test.db"
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE media(filename TEXT);
            CREATE TABLE crew_monitoring_media(filename TEXT);
            CREATE TABLE crew_report_comment_media(filename TEXT);
            CREATE TABLE comments(media_json TEXT);
            CREATE TABLE director_comments(media_json TEXT);
        """)
        conn.commit()
        conn.close()
        self.now = 1_800_000_000

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, name, age_days):
        path = self.uploads / name
        path.write_bytes(b"fixture")
        import os
        stamp = self.now - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def _references(self, table, column, value):
        conn = sqlite3.connect(self.db)
        conn.execute(f"INSERT INTO {table}({column}) VALUES (?)", (value,))
        conn.commit()
        conn.close()

    def test_dry_run_only_lists_old_unreferenced_temp_regular_files(self):
        stale = self._touch("temp_stale.jpg", 8)
        referenced = self._touch("temp_referenced.jpg", 9)
        fresh = self._touch("temp_fresh.jpg", 2)
        ordinary = self._touch("report.jpg", 20)
        self._references("media", "filename", referenced.name)
        self._references("comments", "media_json", json.dumps(["/api/media/temp_json_ref.jpg"]))
        json_ref = self._touch("temp_json_ref.jpg", 10)
        directory = self.uploads / "temp_folder.jpg"
        directory.mkdir()
        link = self.uploads / "temp_link.jpg"
        try:
            link.symlink_to(ordinary)
        except OSError:
            link = None

        before = set(self.uploads.iterdir())
        candidates = cleanup_candidates(self.db, self.uploads, older_than_days=7, now=self.now)
        self.assertEqual([item["path"] for item in candidates], [stale])
        self.assertEqual(set(self.uploads.iterdir()), before)
        self.assertTrue(fresh.exists())
        self.assertTrue(referenced.exists())
        self.assertTrue(json_ref.exists())
        self.assertTrue(ordinary.exists())

    def test_apply_removes_only_rechecked_candidates(self):
        stale = self._touch("temp_stale.jpg", 8)
        referenced = self._touch("temp_ref.jpg", 9)
        self._references("director_comments", "media_json", json.dumps([{"filename": referenced.name}]))
        candidates = cleanup_candidates(self.db, self.uploads, older_than_days=7, now=self.now)
        removed = apply_cleanup(self.db, self.uploads, candidates, older_than_days=7, now=self.now)
        self.assertEqual(removed, [stale.name])
        self.assertFalse(stale.exists())
        self.assertTrue(referenced.exists())

    def test_missing_reference_table_or_malformed_json_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO comments(media_json) VALUES (?)", ("{broken",))
        conn.commit()
        conn.close()
        stale = self._touch("temp_stale.jpg", 10)
        with self.assertRaises(RuntimeError):
            cleanup_candidates(self.db, self.uploads, older_than_days=7, now=self.now)
        self.assertTrue(stale.exists())

    def test_optional_private_root_candidates_and_apply(self):
        private = self.root / "private-media"
        private.mkdir()
        old_private = private / "temp_private.jpg"
        old_private.write_bytes(b"private")
        stamp = self.now - 9 * 86400
        import os
        os.utime(old_private, (stamp, stamp))

        candidates = cleanup_candidates(
            self.db,
            self.uploads,
            private_uploads_dir=private,
            older_than_days=7,
            now=self.now,
        )
        self.assertEqual([candidate["path"] for candidate in candidates], [old_private])
        removed = apply_cleanup(
            self.db,
            self.uploads,
            candidates,
            private_uploads_dir=private,
            older_than_days=7,
            now=self.now,
        )
        self.assertEqual(removed, [old_private.name])
        self.assertFalse(old_private.exists())

    def test_cutoff_is_strictly_older_than_seven_days(self):
        boundary = self._touch("temp_boundary.jpg", 7)
        stale = self._touch("temp_older.jpg", 8)
        candidates = cleanup_candidates(self.db, self.uploads, older_than_days=7, now=self.now)
        self.assertEqual([item["path"] for item in candidates], [stale])
        self.assertTrue(boundary.exists())


if __name__ == "__main__":
    unittest.main()
