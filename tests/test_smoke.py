import os
import sys
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path

# app.py captures DB paths and initializes the schema during import. Isolate it
# before the first import so tests cannot reach project data/uploads.
TEST_ROOT = Path(tempfile.mkdtemp(prefix="albion-tests-"))
os.environ["DB_PATH"] = str(TEST_ROOT / "test.db")
os.environ["UPLOADS_DIR"] = str(TEST_ROOT / "uploads")
os.environ.pop("PRIVATE_MEDIA_DIR", None)
os.environ["JWT_SECRET"] = "unit-test-only-random-secret-0123456789abcdef"

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

try:
    from fastapi.testclient import TestClient
    from app import app
    from auth import create_token
    from database import get_db
except ImportError as exc:  # pragma: no cover - useful in minimal envs
    raise RuntimeError("Install app requirements and test extra httpx") from exc


class ApplicationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (id, login, password_hash, role) VALUES "
                "(1, 'qa', 'unused', 'admin'), (2, 'service-qa', 'unused', 'service'), "
                "(3, 'director-qa', 'unused', 'director'), "
                "(4, 'staff-qa', 'unused', 'staff'), "
                "(5, 'administrator-qa', 'unused', 'administrator'), "
                "(6, 'curator-qa', 'unused', 'curator')"
            )
            conn.execute(
                "INSERT INTO addresses (id, city, address_name, manufacturer) VALUES "
                "(1, 'Ростов', 'Адрес A', 'Производитель A'), "
                "(2, 'Таганрог', 'Адрес B', 'Производитель B')"
            )
            conn.execute(
                "INSERT INTO tasks (id, address_id, created_by, created_by_role, description, workflow_status) VALUES "
                "(1, 1, 1, 'admin', 'Задача A', 'open'), "
                "(2, 2, 1, 'admin', 'Задача B', 'open'), "
                "(3, 1, 3, 'director', 'Задача директора', 'open'), "
                "(4, 1, 3, 'director', 'Закрываемое задание директора', 'open')"
            )
            conn.execute(
                "INSERT INTO reports (id, address_id, user_id, text) VALUES "
                "(1, 1, 1, 'Отчёт A'), (2, 2, 1, 'Отчёт B')"
            )
            # The baseline schema in this repository predates workflow fields.
            # Export only needs legacy columns, so no live migration is needed.
        (TEST_ROOT / "uploads").mkdir(parents=True, exist_ok=True)
        from io import BytesIO
        from PIL import Image

        image_data = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_data, format="JPEG")
        (TEST_ROOT / "uploads" / "media.jpg").write_bytes(image_data.getvalue())
        (TEST_ROOT / "uploads" / "blocked.txt").write_text("not an allowed media type")
        (TEST_ROOT / "outside.jpg").write_bytes(image_data.getvalue())
        try:
            (TEST_ROOT / "uploads" / "media-link.jpg").symlink_to(TEST_ROOT / "outside.jpg")
        except OSError:
            pass
        cls.token = create_token(1, "qa", "admin")
        cls.headers = {"Authorization": f"Bearer {cls.token}"}
        cls.service_headers = {"Authorization": f"Bearer {create_token(2, 'service-qa', 'service')}"}
        cls.director_headers = {"Authorization": f"Bearer {create_token(3, 'director-qa', 'director')}"}
        cls.staff_headers = {"Authorization": f"Bearer {create_token(4, 'staff-qa', 'staff')}"}
        cls.administrator_headers = {
            "Authorization": f"Bearer {create_token(5, 'administrator-qa', 'administrator')}"
        }
        cls.curator_headers = {"Authorization": f"Bearer {create_token(6, 'curator-qa', 'curator')}"}

    def test_upload_quota_defaults_and_admin_authorization(self):
        defaults = {
            "photo_max_upload_bytes": str(10 * 1024 * 1024),
            "video_max_upload_bytes": str(100 * 1024 * 1024),
            "uploads_quota_bytes": "0",
        }
        self.client.put("/api/settings/quotas", json=defaults, headers=self.headers)
        response = self.client.get("/api/settings", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        settings = response.json()
        self.assertEqual(settings["photo_max_upload_bytes"], 10 * 1024 * 1024)
        self.assertEqual(settings["video_max_upload_bytes"], 100 * 1024 * 1024)
        self.assertEqual(settings["uploads_quota_bytes"], 0)

        unauthenticated = self.client.put(
            "/api/settings/quotas",
            json={
                "photo_max_upload_bytes": str(5 * 1024 * 1024),
                "video_max_upload_bytes": str(50 * 1024 * 1024),
                "uploads_quota_bytes": "0",
            },
        )
        self.assertEqual(unauthenticated.status_code, 401)
        service_token = create_token(2, "service-qa", "service")
        forbidden = self.client.put(
            "/api/settings/quotas",
            json={
                "photo_max_upload_bytes": str(5 * 1024 * 1024),
                "video_max_upload_bytes": str(50 * 1024 * 1024),
                "uploads_quota_bytes": "0",
            },
            headers={"Authorization": f"Bearer {service_token}"},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_user_settings_return_logins_roles_only(self):
        response = self.client.get("/api/users", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({"id", "login", "role"}, set(response.json()[0]))
        self.assertNotIn("password", response.json()[0])
        self.assertNotIn("password_hash", response.json()[0])

    def test_admin_can_create_user_and_set_new_password(self):
        created = self.client.post(
            "/api/users",
            json={"login": "new-qa", "password": "NewTestPass!", "role": "service"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200)
        users = self.client.get("/api/users", headers=self.headers).json()
        user = next(row for row in users if row["login"] == "new-qa")
        self.assertEqual(user["role"], "service")
        self.assertNotIn("password_hash", user)

        changed = self.client.put(
            f"/api/users/{user['id']}/password",
            json={"new_password": "ChangedTestPass!"},
            headers=self.headers,
        )
        self.assertEqual(changed.status_code, 200)

    def test_quota_settings_validation_and_update(self):
        payload = {
            "photo_max_upload_bytes": str(8 * 1024 * 1024),
            "video_max_upload_bytes": str(80 * 1024 * 1024),
            "uploads_quota_bytes": str(2 * 1024**3),
        }
        response = self.client.put("/api/settings/quotas", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/settings", headers=self.headers).json()["photo_max_upload_bytes"], 8 * 1024 * 1024)

        invalid = dict(payload, photo_max_upload_bytes="-1")
        self.assertEqual(self.client.put("/api/settings/quotas", json=invalid, headers=self.headers).status_code, 400)
        invalid = dict(payload, video_max_upload_bytes=str(101 * 1024 * 1024))
        self.assertEqual(self.client.put("/api/settings/quotas", json=invalid, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.put("/api/settings/quotas", json=payload, headers={"Authorization": "Bearer bad"}).status_code, 401)

    def test_legacy_schema_migration_preserves_rows(self):
        import sqlite3
        legacy_root = TEST_ROOT / "legacy"
        legacy_root.mkdir()
        legacy_db = legacy_root / "legacy.db"
        conn = sqlite3.connect(legacy_db)
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, password_hash TEXT, role TEXT);
            CREATE TABLE addresses (id INTEGER PRIMARY KEY, city TEXT, address_name TEXT, manufacturer TEXT, active INTEGER DEFAULT 1);
            CREATE TABLE tasks (
              id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL, created_by INTEGER NOT NULL,
              description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at DATETIME,
              closed_at DATETIME, closed_by INTEGER, hidden INTEGER DEFAULT 0
            );
            INSERT INTO users VALUES (1, 'legacy-admin', 'hash', 'admin');
            INSERT INTO addresses VALUES (1, 'Ростов', 'Legacy address', 'Maker', 1);
            INSERT INTO tasks VALUES (7, 1, 1, 'Legacy completed', 'done', '2024-01-01', NULL, NULL, 0);
        """)
        conn.commit()
        conn.close()
        code = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "import database; database.DB_PATH=Path(sys.argv[2]); "
            "database.UPLOADS_DIR=Path(sys.argv[3]); database.UPLOADS_DIR.mkdir(exist_ok=True); "
            "database.init_schema()"
        )
        env = os.environ.copy()
        result = subprocess.run([sys.executable, "-c", code, str(APP_DIR), str(legacy_db), str(legacy_root / "uploads")], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        conn = sqlite3.connect(legacy_db)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        self.assertTrue({"workflow_status", "created_by_role", "is_important", "close_reason"}.issubset(columns))
        task = conn.execute("SELECT description, status, workflow_status, created_by_role FROM tasks WHERE id=7").fetchone()
        self.assertEqual(task, ("Legacy completed", "done", "completed", "admin"))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"comments", "director_comments", "task_status_history", "schema_migrations"}.issubset(tables))
        conn.close()

    def test_task_workflow_transitions_and_history(self):
        complete = self.client.post("/api/tasks/1/complete", headers=self.service_headers)
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["workflow_status"], "pending_review")
        approve = self.client.post("/api/tasks/1/approve", headers=self.headers)
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["workflow_status"], "completed")
        invalid = self.client.post("/api/tasks/1/rework", headers=self.headers)
        self.assertEqual(invalid.status_code, 400)
        history = self.client.get("/api/tasks/1/status-history", headers=self.headers)
        self.assertEqual(history.status_code, 200)
        self.assertEqual([row["status"] for row in history.json()], ["pending_review", "completed"])

    def test_director_feed_serializes_comment_media(self):
        response = self.client.get("/api/director/feed", headers=self.director_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all("workflow_status" in item for item in response.json() if item["type"] == "task"))

    def test_director_review_and_close_scope(self):
        # Director cannot review or close a task created by an admin.
        from database import get_db
        with get_db() as db:
            db.execute("UPDATE tasks SET workflow_status='pending_review' WHERE id=2")
        self.assertEqual(self.client.post("/api/tasks/2/approve", headers=self.director_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks/2/rework", headers=self.director_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks/2/close", headers=self.director_headers).status_code, 403)

        # Director can complete review/close actions on their own tasks.
        with get_db() as db:
            db.execute("UPDATE tasks SET workflow_status='pending_review' WHERE id=3")
        self.assertEqual(self.client.post("/api/tasks/3/approve", headers=self.director_headers).status_code, 200)
        self.assertEqual(self.client.post("/api/tasks/3/close", headers=self.director_headers).status_code, 400)
        self.assertEqual(self.client.post("/api/tasks/4/close", headers=self.director_headers).status_code, 200)

    def test_service_cannot_approve_or_close_and_missing_task_is_404(self):
        self.assertEqual(self.client.post("/api/tasks/2/approve", headers=self.service_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks/2/close", headers=self.service_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks/999/complete", headers=self.service_headers).status_code, 404)

    def test_schema_migration_adds_workflow_comment_tables_and_is_idempotent(self):
        with get_db() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"schema_migrations", "comments", "director_comments", "task_status_history"}.issubset(tables))
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            self.assertTrue({"created_by_role", "workflow_status", "is_important", "close_reason"}.issubset(columns))
            applied = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
            self.assertEqual(
                [(row["version"], row["name"]) for row in applied],
                [(1, "workflow_and_comments"), (2, "soft_hide_and_original_filenames")],
            )
            existing_task = conn.execute("SELECT workflow_status, created_by_role, is_important FROM tasks WHERE id=1").fetchone()
            self.assertEqual(existing_task["workflow_status"], "open")
            self.assertEqual(existing_task["created_by_role"], "admin")
            self.assertEqual(existing_task["is_important"], 0)
        from database import init_schema
        init_schema()
        with get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 4)

    def test_address_limit_is_bounded(self):
        response = self.client.get("/api/addresses", params={"limit": 10000000})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json()), 1000)
        self.assertEqual(self.client.get("/api/addresses", params={"limit": 0}).status_code, 200)

    def test_health_is_public_and_non_sensitive(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_excel_requires_auth(self):
        response = self.client.get("/api/export/excel")
        self.assertEqual(response.status_code, 401)

    def test_excel_export_applies_filters(self):
        response = self.client.get(
            "/api/export/excel",
            params={"city": "Ростов", "manufacturer": "Производитель A"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            response.headers["content-type"],
        )
        from io import BytesIO
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(response.content), read_only=True)
        task_rows = list(workbook["Задания"].values)
        report_rows = list(workbook["Мониторинг"].values)
        self.assertEqual(len(task_rows), 4)
        self.assertTrue(all(row[2] == "Адрес A" for row in task_rows[1:]))
        self.assertEqual(len(report_rows), 2)
        self.assertEqual(report_rows[1][3], "Адрес A")
        all_rows = self.client.get("/api/export/excel", headers=self.headers)
        all_book = load_workbook(BytesIO(all_rows.content), read_only=True)
        self.assertEqual(len(list(all_book["Задания"].values)), 5)
        self.assertEqual(len(list(all_book["Мониторинг"].values)), 3)

    def test_frontend_loads_media_through_authenticated_helper(self):
        source = (APP_DIR / "static" / "media.js").read_text(encoding="utf-8")
        self.assertIn('headers: tokenHeaders()', source)
        self.assertIn('URL.createObjectURL(blob)', source)
        self.assertIn('URL.revokeObjectURL', source)
        self.assertIn("/api/media/", source)
        self.assertNotIn("token=", source)
        self.assertIn("IntersectionObserver", source)
        self.assertIn("blobCache", source)
        self.assertIn('element.removeAttribute("src")', source)
        self.assertIn("dataset.mediaLoading !== apiPath", source)

    def test_upload_uses_admin_configured_per_file_limits(self):
        quotas = {
            "photo_max_upload_bytes": "100",
            "video_max_upload_bytes": "5",
            "uploads_quota_bytes": "0",
        }
        self.assertEqual(self.client.put("/api/settings/quotas", json=quotas, headers=self.headers).status_code, 200)
        from io import BytesIO
        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (3, 3), "orange").save(image, format="JPEG")
        rejected_photo = self.client.post(
            "/api/upload",
            files={"file": ("small.jpg", image.getvalue(), "image/jpeg")},
            headers=self.headers,
        )
        self.assertEqual(rejected_photo.status_code, 413)
        rejected_video = self.client.post(
            "/api/upload",
            files={"file": ("video.mp4", b"123456", "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(rejected_video.status_code, 413)
        quotas.update({
            "photo_max_upload_bytes": str(10 * 1024 * 1024),
            "video_max_upload_bytes": str(100 * 1024 * 1024),
        })
        self.client.put("/api/settings/quotas", json=quotas, headers=self.headers)

    def test_upload_storage_quota_counts_files_and_never_deletes(self):
        (TEST_ROOT / "uploads" / "temp_held.jpg").write_bytes(b"held-temp")
        (TEST_ROOT / "uploads" / "unassociated.bin").write_bytes(b"orphan-but-preserved")
        (TEST_ROOT / "uploads" / ".upload-incomplete").write_bytes(b"ignored-partial")
        (TEST_ROOT / "uploads" / "not-a-file").mkdir(exist_ok=True)
        from app import uploads_usage_bytes

        usage_before = uploads_usage_bytes()
        self.assertGreaterEqual(usage_before, len(b"held-temp") + len(b"orphan-but-preserved"))
        self.assertEqual(usage_before, sum(
            path.stat().st_size
            for path in (TEST_ROOT / "uploads").iterdir()
            if path.is_file() and not path.is_symlink() and not path.name.startswith(".upload-") and path.name != ".quota.lock"
        ))

        quotas = {
            "photo_max_upload_bytes": str(10 * 1024 * 1024),
            "video_max_upload_bytes": str(100 * 1024 * 1024),
            "uploads_quota_bytes": str(usage_before),
        }
        self.assertEqual(self.client.put("/api/settings/quotas", json=quotas, headers=self.headers).status_code, 200)
        before_files = {p.name for p in (TEST_ROOT / "uploads").iterdir()}
        from io import BytesIO
        from PIL import Image
        data = BytesIO()
        Image.new("RGB", (5, 5), "purple").save(data, format="JPEG")
        response = self.client.post("/api/upload", files={"file": ("quota.jpg", data.getvalue(), "image/jpeg")}, headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual({p.name for p in (TEST_ROOT / "uploads").iterdir()}, before_files)
        self.assertEqual(uploads_usage_bytes(), usage_before)

        # Lowering quota under existing usage is allowed, but preserves files.
        quotas["uploads_quota_bytes"] = "1"
        self.assertEqual(self.client.put("/api/settings/quotas", json=quotas, headers=self.headers).status_code, 200)
        self.assertEqual(uploads_usage_bytes(), usage_before)

    def test_upload_accepts_and_normalizes_valid_jpeg(self):
        from io import BytesIO
        from PIL import Image

        data = BytesIO()
        Image.new("RGB", (3, 3), "green").save(data, format="JPEG")
        response = self.client.post(
            "/api/upload",
            files={"file": ("ok.jpg", data.getvalue(), "image/jpeg")},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["filename"].startswith("temp_"))
        self.assertEqual(payload["filename"].endswith(".jpg"), True)
        self.assertTrue((TEST_ROOT / "uploads" / payload["filename"]).is_file())

    def test_upload_rejects_malformed_or_mismatched_images_without_files(self):
        from io import BytesIO
        from PIL import Image

        before = {path.name for path in (TEST_ROOT / "uploads").iterdir()}
        png_data = BytesIO()
        Image.new("RGB", (3, 3), "blue").save(png_data, format="PNG")
        invalid_files = (
            ("bad.jpg", b"not-an-image", "image/jpeg"),
            ("wrong.png", b"not-an-image", "image/png"),
            ("renamed.jpg", png_data.getvalue(), "image/jpeg"),
        )
        for filename, content, content_type in invalid_files:
            response = self.client.post(
                "/api/upload",
                files={"file": (filename, content, content_type)},
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 400)
        after = {path.name for path in (TEST_ROOT / "uploads").iterdir()}
        self.assertEqual(after, before)

    def test_upload_rejects_photo_above_bounded_limit(self):
        response = self.client.post(
            "/api/upload",
            files={"file": ("huge.jpg", b"x" * (10 * 1024 * 1024 + 1), "image/jpeg")},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 413)

    def test_video_upload_keeps_extension_policy_and_size_limit(self):
        quota_payload = {
            "photo_max_upload_bytes": str(10 * 1024 * 1024),
            "video_max_upload_bytes": str(100 * 1024 * 1024),
            "uploads_quota_bytes": "0",
        }
        self.client.put("/api/settings/quotas", json=quota_payload, headers=self.headers)
        response = self.client.post(
            "/api/upload",
            files={"file": ("clip.mp4", b"not-validated-video", "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["filename"].endswith(".mp4"))
        empty = self.client.post(
            "/api/upload",
            files={"file": ("empty.mp4", b"", "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(empty.status_code, 400)
        before = {path.name for path in (TEST_ROOT / "uploads").iterdir()}
        response = self.client.post(
            "/api/upload",
            files={"file": ("huge.mp4", b"x" * (100 * 1024 * 1024 + 1), "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 413)
        after = {path.name for path in (TEST_ROOT / "uploads").iterdir()}
        self.assertEqual(after, before)

    def test_private_media_root_upload_and_authenticated_read(self):
        import app as app_module
        from io import BytesIO
        from PIL import Image

        private_root = TEST_ROOT / "private-media"
        private_root.mkdir(exist_ok=True)
        old_values = (
            app_module.PRIVATE_MEDIA_DIR,
            app_module.ACTIVE_MEDIA_DIR,
            app_module.QUOTA_LOCK_PATH,
        )
        app_module.PRIVATE_MEDIA_DIR = private_root
        app_module.ACTIVE_MEDIA_DIR = private_root
        app_module.QUOTA_LOCK_PATH = private_root / ".quota.lock"
        try:
            data = BytesIO()
            Image.new("RGB", (4, 4), "cyan").save(data, format="JPEG")
            upload = self.client.post(
                "/api/upload",
                files={"file": ("private.jpg", data.getvalue(), "image/jpeg")},
                headers=self.headers,
            )
            self.assertEqual(upload.status_code, 200)
            filename = upload.json()["filename"]
            self.assertTrue(filename.startswith("pvt_temp_"))
            self.assertTrue((private_root / filename).is_file())
            self.assertFalse((TEST_ROOT / "uploads" / filename).exists())

            legacy_local = TEST_ROOT / "uploads" / "legacy-local.jpg"
            legacy_local.write_bytes(bytes((0xFF, 0xD8, 0xFF)))
            legacy_response = self.client.get("/api/media/legacy-local.jpg", headers=self.headers)
            self.assertEqual(legacy_response.status_code, 200)

            symlink = private_root / "pvt_symlink.jpg"
            try:
                symlink.symlink_to(legacy_local)
            except OSError:
                symlink = None
            if symlink is not None:
                self.assertEqual(self.client.get(f"/api/media/{symlink.name}", headers=self.headers).status_code, 404)

            with get_db() as conn:
                conn.execute(
                    "INSERT INTO addresses (id, city, address_name, manufacturer) VALUES (10, 'City', 'Private address', 'Maker')"
                )
            attached = self.client.post(
                "/api/reports",
                json={"address_id": 10, "text": "private attachment", "media": [{"filename": filename, "original_filename": "private.jpg"}]},
                headers=self.headers,
            )
            self.assertEqual(attached.status_code, 200)
            with get_db() as conn:
                row = conn.execute("SELECT filename FROM media WHERE report_id = ?", (attached.json()["report_id"],)).fetchone()
            self.assertTrue(row["filename"].startswith("pvt_r"))
            self.assertTrue((private_root / row["filename"]).is_file())

            final_media = self.client.get(f"/api/media/{row['filename']}", headers=self.headers)
            self.assertEqual(final_media.status_code, 200)
            self.assertTrue(final_media.content.startswith(bytes((0xFF, 0xD8))))
            self.assertEqual(self.client.get(f"/api/media/{row['filename']}").status_code, 401)
        finally:
            (
                app_module.PRIVATE_MEDIA_DIR,
                app_module.ACTIVE_MEDIA_DIR,
                app_module.QUOTA_LOCK_PATH,
            ) = old_values

    def test_private_media_root_nested_inside_uploads_is_rejected(self):
        import app as app_module
        private_root = TEST_ROOT / "uploads" / "nested-private"
        private_root.mkdir(exist_ok=True)
        old_values = (
            app_module.PRIVATE_MEDIA_DIR,
            app_module.ACTIVE_MEDIA_DIR,
            app_module.QUOTA_LOCK_PATH,
        )
        app_module.PRIVATE_MEDIA_DIR = private_root
        app_module.ACTIVE_MEDIA_DIR = private_root
        app_module.QUOTA_LOCK_PATH = private_root / ".quota.lock"
        try:
            with self.assertRaises(Exception) as raised:
                app_module.ensure_active_media_dir()
            self.assertEqual(getattr(raised.exception, "status_code", None), 503)
        finally:
            (
                app_module.PRIVATE_MEDIA_DIR,
                app_module.ACTIVE_MEDIA_DIR,
                app_module.QUOTA_LOCK_PATH,
            ) = old_values

    def test_private_media_root_missing_fails_closed_without_legacy_fallback(self):
        import app as app_module
        from io import BytesIO
        from PIL import Image

        absent_root = TEST_ROOT / "not-mounted"
        old_values = (
            app_module.PRIVATE_MEDIA_DIR,
            app_module.ACTIVE_MEDIA_DIR,
            app_module.QUOTA_LOCK_PATH,
        )
        app_module.PRIVATE_MEDIA_DIR = absent_root
        app_module.ACTIVE_MEDIA_DIR = absent_root
        app_module.QUOTA_LOCK_PATH = absent_root / ".quota.lock"
        try:
            data = BytesIO()
            Image.new("RGB", (4, 4), "yellow").save(data, format="JPEG")
            response = self.client.post(
                "/api/upload",
                files={"file": ("missing-mount.jpg", data.getvalue(), "image/jpeg")},
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(list((TEST_ROOT / "uploads").glob("pvt_*")), [])
            self.assertFalse(absent_root.exists())
        finally:
            (
                app_module.PRIVATE_MEDIA_DIR,
                app_module.ACTIVE_MEDIA_DIR,
                app_module.QUOTA_LOCK_PATH,
            ) = old_values

    def test_media_requires_auth_and_is_not_public_static(self):
        self.assertEqual(self.client.get("/api/media/media.jpg").status_code, 401)
        self.assertEqual(self.client.get("/uploads/media.jpg").status_code, 404)
        self.assertEqual(self.client.get("/api/media/media.jpg", headers={"Authorization": "Bearer invalid"}).status_code, 401)

    def test_media_is_served_to_authenticated_users_only(self):
        response = self.client.get("/api/media/media.jpg", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xff\xd8"))
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_media_rejects_traversal_and_missing_files(self):
        self.assertEqual(
            self.client.get("/api/media/%2e%2e%2fsecret.jpg", headers=self.headers).status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/media/missing.jpg", headers=self.headers).status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/media/blocked.txt", headers=self.headers).status_code,
            404,
        )
        if (TEST_ROOT / "uploads" / "media-link.jpg").is_symlink():
            self.assertEqual(
                self.client.get("/api/media/media-link.jpg", headers=self.headers).status_code,
                404,
            )

    def test_auth_configuration_rejects_missing_secret(self):
        env = os.environ.copy()
        env.pop("JWT_SECRET", None)
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import auth", str(APP_DIR)],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JWT_SECRET must be set", result.stderr)

    def test_video_extension_patterns_match_normal_filenames(self):
        import re

        pattern = r"\.(mp4|mov|webm|m4v)$"
        video_pattern = re.compile(pattern, re.IGNORECASE)
        self.assertIsNotNone(video_pattern.search("report.MP4"))
        self.assertIsNotNone(video_pattern.search("clip.mov"))
        self.assertIsNone(video_pattern.search("photo.jpg"))
        for name in ("client.js", "admin.js", "director.js"):
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn(pattern, source)
            self.assertNotIn(r"/\\.(mp4|mov|webm|m4v)$", source)

    def test_list_load_errors_have_visible_state(self):
        expectations = {
            "client.js": [
                "Не удалось загрузить пользователей",
                "Не удалось загрузить настройки медиа",
                "if(append || allFeed.length)",
            ],
            "tasks.js": ["Ошибка загрузки заданий"],
            "admin.js": ["if(append || allFeedItems.length)"],
            "director.js": ["if(append || allFeedItems.length)"],
            "server.js": ["Ошибка загрузки отчётов"],
        }

        for name, markers in expectations.items():
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(marker, source, f"{name}: missing {marker}")

    def test_tasks_list_embeds_comments_without_per_task_requests(self):
        task_id = None
        try:
            created = self.client.post(
                "/api/tasks",
                json={"address_id": 1, "description": "Задание с комментарием"},
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 200)
            task_id = created.json()["task_id"]

            posted = self.client.post(
                "/api/comments",
                json={"task_id": task_id, "text": "Пакетный комментарий", "media": []},
                headers=self.service_headers,
            )
            self.assertEqual(posted.status_code, 200)

            listed = self.client.get(
                "/api/tasks", params={"status": "open"}, headers=self.headers
            )
            self.assertEqual(listed.status_code, 200)
            task = next(item for item in listed.json() if item["id"] == task_id)
            self.assertIn("comments", task)
            self.assertTrue(
                any(comment["text"] == "Пакетный комментарий" for comment in task["comments"])
            )
        finally:
            if task_id is not None:
                self._delete_task(task_id)

        source = (APP_DIR / "static" / "tasks.js").read_text(encoding="utf-8")
        self.assertIn("t.comments", source)
        self.assertIn("renderChatMessages", source)
        self.assertNotIn("tasks.forEach(t => loadChat(t.id));", source)

    def test_password_change_uses_masked_modal(self):
        source = (APP_DIR / "static" / "admin-settings.js").read_text(encoding="utf-8")
        template = (APP_DIR / "templates" / "admin.html").read_text(encoding="utf-8")

        self.assertNotIn("prompt(", source)
        self.assertIn("openPasswordModal", source)
        self.assertIn("submitPasswordChange", source)
        self.assertIn("Пароли не совпадают", source)

        self.assertIn('id="passwordModal"', template)
        self.assertIn('id="passwordNew"', template)
        self.assertIn('type="password"', template)
        self.assertIn('id="passwordConfirm"', template)

    def test_task_creation_uploads_before_create(self):
        for name in ("client.js", "admin.js", "director.js"):
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn("Upload first", source, name)
            self.assertIn("вложения не прикрепились", source, name)

        tasks = (APP_DIR / "static" / "tasks.js").read_text(encoding="utf-8")
        self.assertIn("Сначала загружаем файлы", tasks)
        self.assertIn("вложения не прикрепились", tasks)

    def test_search_uses_debounce_and_aborts_stale_requests(self):
        auth = (APP_DIR / "static" / "auth.js").read_text(encoding="utf-8")
        self.assertIn("window.debounce", auth)

        for name in ("client.js", "tasks.js"):
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn("new AbortController()", source)
            self.assertIn("debouncedTaskAddressSearch", source)
            self.assertIn("signal: controller.signal", source)
            self.assertIn('if(e.name === "AbortError") return;', source)

        client = (APP_DIR / "static" / "client.js").read_text(encoding="utf-8")
        director = (APP_DIR / "static" / "director.js").read_text(encoding="utf-8")
        self.assertIn("window.debounce(applyFilters, 150)", client)
        self.assertIn("window.debounce(refreshFeed, 150)", director)

    def test_shared_api_fetch_handles_expired_session(self):
        auth = (APP_DIR / "static" / "auth.js").read_text(encoding="utf-8")
        self.assertIn("window.apiFetch", auth)
        self.assertIn("response.status === 401", auth)
        self.assertIn("localStorage.clear()", auth)
        self.assertIn('window.location.href = "/"', auth)
        self.assertIn("Array.isArray(detail)", auth)

        for name in (
            "client.js",
            "admin.js",
            "director.js",
            "tasks.js",
            "server.js",
            "crew.js",
            "head.js",
            "admin-settings.js",
            "role_app.js",
        ):
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn("window.apiFetch", source, name)
            self.assertNotIn('fetch("/api/upload"', source, name)

        server = (APP_DIR / "static" / "server.js").read_text(encoding="utf-8")
        self.assertIn('response.status === 401', server)

    def test_feed_pagination_returns_meta_and_slices(self):
        first = self.client.get(
            "/api/feed", params={"limit": 2, "offset": 0}, headers=self.headers
        )
        self.assertEqual(first.status_code, 200)
        first_data = first.json()
        self.assertEqual(first_data["limit"], 2)
        self.assertEqual(first_data["offset"], 0)
        self.assertEqual(len(first_data["items"]), 2)
        self.assertTrue(first_data["has_more"])

        second = self.client.get(
            "/api/feed", params={"limit": 2, "offset": 2}, headers=self.headers
        )
        second_data = second.json()
        self.assertEqual(len(second_data["items"]), 2)
        first_keys = {(item["type"], item["id"]) for item in first_data["items"]}
        second_keys = {(item["type"], item["id"]) for item in second_data["items"]}
        self.assertTrue(first_keys.isdisjoint(second_keys))

        bounded = self.client.get(
            "/api/feed", params={"limit": 100000, "offset": 0}, headers=self.headers
        )
        self.assertEqual(bounded.json()["limit"], 500)

    def test_legacy_monitoring_endpoint_serializes_director_comment_media(self):
        text = "E2E monitoring endpoint comment"
        posted = self.client.post(
            "/api/director/comments",
            json={
                "item_type": "monitoring",
                "item_id": 1,
                "text": text,
                "media": [],
            },
            headers=self.director_headers,
        )
        self.assertEqual(posted.status_code, 200)

        try:
            response = self.client.get("/api/monitoring/1", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            comments = [
                item for item in response.json()
                if item.get("is_director_comment") and item.get("text") == text
            ]
            self.assertEqual(len(comments), 1)
            self.assertIsInstance(comments[0]["media"], list)
        finally:
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM director_comments WHERE text = ?", (text,)
                )

    def test_legacy_renderers_escape_user_content(self):
        auth = (APP_DIR / "static" / "auth.js").read_text(encoding="utf-8")
        self.assertIn("window.escapeHtml", auth)

        checks = {
            "client.js": [
                "esc(item.city)",
                "esc(item.address_name)",
                "esc(authorName)",
                "esc(sourceText)",
                "esc(name)",
                "esc(JSON.stringify",
            ],
            "tasks.js": [
                "esc(t.city)",
                "esc(t.address_name)",
                "esc(t.description)",
                "esc(t.author)",
                "esc(c.author)",
                "esc(c.text)",
                "esc(JSON.stringify",
            ],
            "server.js": [
                "esc(r.city)",
                "esc(r.address_name)",
                "esc(r.author)",
                "esc(r.text)",
            ],
        }

        for name, expected in checks.items():
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            for marker in expected:
                self.assertIn(marker, source, f"{name}: missing {marker}")

        client = (APP_DIR / "static" / "client.js").read_text(encoding="utf-8")
        tasks = (APP_DIR / "static" / "tasks.js").read_text(encoding="utf-8")
        server = (APP_DIR / "static" / "server.js").read_text(encoding="utf-8")
        self.assertNotIn("${item.city}", client)
        self.assertNotIn("${t.city}", tasks)
        self.assertNotIn("${r.city}", server)

    def test_feed_filters_are_persisted_and_sort_is_type_safe(self):
        shared = (APP_DIR / "static" / "filter_state.js").read_text(encoding="utf-8")
        self.assertIn("readFeedFilterParams", shared)
        self.assertIn("writeFeedFilterParams", shared)

        for name, template_name in (
            ("client.js", "client.html"),
            ("admin.js", "admin.html"),
            ("director.js", "director.html"),
        ):
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            template = (APP_DIR / "templates" / template_name).read_text(encoding="utf-8")

            self.assertIn("syncFilterUrl", source)
            self.assertIn("restoreFilterState", source)
            self.assertIn("resetFeedFilters", source)
            self.assertIn("typeRank", source)
            self.assertIn('["open", "rework"]', source)
            self.assertNotIn('["open", "pending_review", "rework"]', source)

            self.assertIn("/static/filter_state.js", template)
            self.assertIn("resetFeedFilters()", template)
            self.assertIn("По загруженным данным и фильтру", template)

        for template_name in ("admin.html", "director.html"):
            template = (APP_DIR / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('data-filter="crew_report"', template)

    def test_client_action_buttons_are_explicit_and_guarded(self):
        template = (APP_DIR / "templates" / "client.html").read_text(encoding="utf-8")
        source = (APP_DIR / "static" / "client.js").read_text(encoding="utf-8")

        self.assertIn('id="monitoringSubmitBtn"', template)
        self.assertIn('id="createTaskSubmitBtn"', template)
        self.assertIn('getElementById("monitoringSubmitBtn")', source)
        self.assertIn('getElementById("createTaskSubmitBtn")', source)
        self.assertNotIn('#monitoringModal .btn:not(.secondary)', source)
        self.assertIn('button.textContent = "Создание..."', source)

    def test_crew_report_has_no_director_comment_form(self):
        for name in ("admin.js", "director.js"):
            source = (APP_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn("Комментарии к отчётам экипажа доступны Руководителю", source)
            self.assertIn('if(type === "crew_report")', source)

        source = (APP_DIR / "static" / "director.js").read_text(encoding="utf-8")
        self.assertIn("!isCrewReport &&", source)

        response = self.client.post(
            "/api/director/comments",
            json={
                "item_type": "crew_report",
                "item_id": 1,
                "text": "Неверный тип",
                "media": [],
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_logout_closes_own_session(self):
        # Create a real session row and bind that ID to the token.
        with get_db() as conn:
            session_id = conn.execute(
                "INSERT INTO auth_sessions (user_id, login, role) VALUES (1, 'qa', 'admin')"
            ).lastrowid
        session_token = create_token(1, "qa", "admin", session_id=session_id)
        response = self.client.post(
            "/api/logout", headers={"Authorization": f"Bearer {session_token}"}
        )
        self.assertEqual(response.status_code, 200)
        with get_db() as conn:
            row = conn.execute(
                "SELECT logout_at FROM auth_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        self.assertIsNotNone(row["logout_at"])

    def test_role_profile_pages_use_base_interface(self):
        staff_page = self.client.get("/staff")
        self.assertEqual(staff_page.status_code, 200)
        self.assertIn('id="monitoringBtn"', staff_page.text)

        administrator_page = self.client.get("/administrator")
        self.assertEqual(administrator_page.status_code, 200)
        self.assertIn('id="settingsToggle"', administrator_page.text)

        curator_page = self.client.get("/curator")
        self.assertEqual(curator_page.status_code, 200)
        self.assertIn('id="feedSearch"', curator_page.text)

    def _delete_task(self, task_id):
        with get_db() as conn:
            conn.execute("DELETE FROM task_status_history WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM comments WHERE task_id = ?", (task_id,))
            conn.execute(
                "DELETE FROM director_comments WHERE item_type = 'task' AND item_id = ?",
                (task_id,),
            )
            conn.execute("DELETE FROM media WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def test_curator_task_is_stored_and_reviewed_as_director(self):
        task_id = None
        try:
            created = self.client.post(
                "/api/tasks",
                json={"address_id": 1, "description": "Задание куратора"},
                headers=self.curator_headers,
            )
            self.assertEqual(created.status_code, 200)
            task_id = created.json()["task_id"]

            with get_db() as conn:
                row = conn.execute(
                    "SELECT created_by_role FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            self.assertEqual(row["created_by_role"], "director")

            closed = self.client.post(
                f"/api/tasks/{task_id}/close", headers=self.curator_headers
            )
            self.assertEqual(closed.status_code, 200)
        finally:
            if task_id is not None:
                self._delete_task(task_id)

    def test_administrator_and_curator_use_director_comments(self):
        task_id = None
        try:
            created = self.client.post(
                "/api/tasks",
                json={"address_id": 1, "description": "Задание для комментариев"},
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 200)
            task_id = created.json()["task_id"]

            for headers in (self.administrator_headers, self.curator_headers):
                response = self.client.post(
                    "/api/director/comments",
                    json={
                        "item_type": "task",
                        "item_id": task_id,
                        "text": "Комментарий профиля",
                        "media": [],
                    },
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200)

            regular = self.client.post(
                "/api/comments",
                json={"task_id": task_id, "text": "Обычный комментарий", "media": []},
                headers=self.curator_headers,
            )
            self.assertEqual(regular.status_code, 403)

            sessions = self.client.get(
                "/api/auth-sessions", headers=self.administrator_headers
            )
            self.assertEqual(sessions.status_code, 200)
        finally:
            if task_id is not None:
                self._delete_task(task_id)

    def test_admin_can_create_new_role_profiles(self):
        try:
            for role in ("staff", "administrator", "curator"):
                response = self.client.post(
                    "/api/users",
                    json={
                        "login": f"created-{role}",
                        "password": "NewTestPass!",
                        "role": role,
                    },
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 200)
        finally:
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM users WHERE login IN (?, ?, ?)",
                    ("created-staff", "created-administrator", "created-curator"),
                )

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.close()
        finally:
            shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
