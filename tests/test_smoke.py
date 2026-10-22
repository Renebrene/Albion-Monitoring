import atexit
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
# Временный каталог общий для всех тест-классов модуля, поэтому удаляем его
# только в конце процесса: иначе классы, идущие следом, останутся без БД.
atexit.register(lambda: shutil.rmtree(TEST_ROOT, ignore_errors=True))
os.environ["DB_PATH"] = str(TEST_ROOT / "test.db")
os.environ["UPLOADS_DIR"] = str(TEST_ROOT / "uploads")
os.environ.pop("PRIVATE_MEDIA_DIR", None)
os.environ["JWT_SECRET"] = "unit-test-only-random-secret-0123456789abcdef"

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

try:
    from fastapi.testclient import TestClient
    from starlette.requests import Request as StarletteRequest

    from app import app
    from auth import create_token, get_current_user, hash_password
    from database import get_db, get_tenant_context
except ImportError as exc:  # pragma: no cover - useful in minimal envs
    raise RuntimeError("Install app requirements and test extra httpx") from exc


def make_session_token(user_id, login, role):
    """Создаёт серверную сессию и привязанный к ней JWT."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT token_version FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        cursor = conn.execute(
            "INSERT INTO auth_sessions (user_id, login, role) VALUES (?, ?, ?)",
            (user_id, login, role),
        )
    return create_token(
        user_id,
        login,
        role,
        session_id=cursor.lastrowid,
        token_version=row["token_version"] if row else 1,
    )


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
        cls.token = make_session_token(1, "qa", "admin")
        cls.headers = {"Authorization": f"Bearer {cls.token}"}
        cls.service_headers = {"Authorization": f"Bearer {make_session_token(2, 'service-qa', 'service')}"}
        cls.director_headers = {"Authorization": f"Bearer {make_session_token(3, 'director-qa', 'director')}"}
        cls.staff_headers = {"Authorization": f"Bearer {make_session_token(4, 'staff-qa', 'staff')}"}
        cls.administrator_headers = {
            "Authorization": f"Bearer {make_session_token(5, 'administrator-qa', 'administrator')}"
        }
        cls.curator_headers = {"Authorization": f"Bearer {make_session_token(6, 'curator-qa', 'curator')}"}

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
        service_token = make_session_token(2, "service-qa", "service")
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
        self.assertTrue({"legal_entities", "audit_log"}.issubset(tables))
        # v3: у мигрированного пользователя появляются активность, версия прав
        # и юрлицо по умолчанию — прежние поля не меняются.
        user = conn.execute(
            "SELECT login, role, active, token_version, legal_entity_id FROM users WHERE id = 1"
        ).fetchone()
        self.assertEqual(user, ("legacy-admin", "admin", 1, 1, 1))
        self.assertEqual(
            conn.execute("SELECT name FROM legal_entities WHERE id = 1").fetchone()[0],
            "Головная организация",
        )
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6])
        # v4: юрлицо у рабочих таблиц, общий признак адреса и членство.
        task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        self.assertIn("legal_entity_id", task_columns)
        address_columns = {row[1] for row in conn.execute("PRAGMA table_info(addresses)")}
        self.assertIn("shared", address_columns)
        membership = conn.execute(
            "SELECT user_id, legal_entity_id, is_default FROM user_entities WHERE user_id = 1"
        ).fetchone()
        self.assertEqual(membership, (1, 1, 1))
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
                [
                    (1, "workflow_and_comments"),
                    (2, "soft_hide_and_original_filenames"),
                    (3, "sessions_audit_active"),
                    (4, "entity_scope"),
                    (5, "roles_and_capabilities"),
                    (6, "custom_role_codes"),
                ],
            )
            existing_task = conn.execute("SELECT workflow_status, created_by_role, is_important FROM tasks WHERE id=1").fetchone()
            self.assertEqual(existing_task["workflow_status"], "open")
            self.assertEqual(existing_task["created_by_role"], "admin")
            self.assertEqual(existing_task["is_important"], 0)
        from database import init_schema
        init_schema()
        with get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 6)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 4)

    def test_address_limit_is_bounded(self):
        response = self.client.get(
            "/api/addresses", params={"limit": 10000000}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json()), 1000)
        self.assertEqual(
            self.client.get(
                "/api/addresses", params={"limit": 0}, headers=self.headers
            ).status_code,
            200,
        )

    def test_directories_require_auth(self):
        # Без токена неизвестно юрлицо, поэтому справочники закрыты.
        for path in ("/api/addresses", "/api/cities", "/api/manufacturers"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

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
                    "DELETE FROM user_entities WHERE user_id IN ("
                    "SELECT id FROM users WHERE login IN (?, ?, ?))",
                    ("created-staff", "created-administrator", "created-curator"),
                )
                conn.execute(
                    "DELETE FROM users WHERE login IN (?, ?, ?)",
                    ("created-staff", "created-administrator", "created-curator"),
                )

    @classmethod
    def tearDownClass(cls):
        cls.client.close()


class AccessStageTests(unittest.TestCase):
    """Отзыв сессий, роль/активность из БД, аудит и контекст юрлица."""

    PASSWORD = "Start-Pass-Access-1"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        password_hash = hash_password(cls.PASSWORD)
        with get_db() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE user_id IN (10, 11, 12, 13)")
            conn.execute("DELETE FROM users WHERE id IN (10, 11, 12, 13)")
            conn.execute(
                """
                INSERT INTO users (id, login, password_hash, role, active, token_version, legal_entity_id)
                VALUES
                    (10, 'access-admin', ?, 'admin', 1, 1, 1),
                    (11, 'session-qa', ?, 'staff', 1, 1, 1),
                    (12, 'blocked-qa', ?, 'staff', 0, 1, 1),
                    (13, 'entity2-qa', ?, 'administrator', 1, 1, 2)
                """,
                (password_hash, password_hash, password_hash, password_hash),
            )
        cls.admin_headers = {
            "Authorization": f"Bearer {make_session_token(10, 'access-admin', 'admin')}"
        }

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    @staticmethod
    def _request(token):
        """Минимальный ASGI-scope для прямого вызова get_current_user."""
        return StarletteRequest(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/me",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )

    def _login(self, login, password=None):
        return self.client.post(
            "/api/login", json={"login": login, "password": password or self.PASSWORD}
        )

    def _audit_rows(self, action, actor_user_id=None):
        with get_db() as conn:
            if actor_user_id is None:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE action = ?", (action,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE action = ? AND actor_user_id = ?",
                    (action, actor_user_id),
                ).fetchall()
        return rows

    def test_token_without_session_is_rejected(self):
        token = create_token(11, "session-qa", "staff")
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_token_with_unknown_session_is_rejected(self):
        token = create_token(11, "session-qa", "staff", session_id=999999)
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_login_logout_flow_is_audited(self):
        login = self._login("session-qa")
        self.assertEqual(login.status_code, 200)
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)

        self.assertTrue(self._audit_rows("auth.login", actor_user_id=11))
        self.assertTrue(self._audit_rows("auth.logout", actor_user_id=11))

    def test_logout_leaves_other_sessions_alive(self):
        first = make_session_token(11, "session-qa", "staff")
        second = make_session_token(11, "session-qa", "staff")
        self.assertEqual(
            self.client.post(
                "/api/logout", headers={"Authorization": f"Bearer {first}"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {first}"}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {second}"}
            ).status_code,
            200,
        )

    def test_expired_session_is_rejected(self):
        with get_db() as conn:
            session_id = conn.execute(
                "INSERT INTO auth_sessions (user_id, login, role, expires_at) "
                "VALUES (11, 'session-qa', 'staff', '2000-01-01 00:00:00')"
            ).lastrowid
        token = create_token(11, "session-qa", "staff", session_id=session_id)
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_token_hash_mismatch_is_rejected(self):
        session_token = make_session_token(11, "session-qa", "staff")
        with get_db() as conn:
            conn.execute(
                "UPDATE auth_sessions SET token_hash = ? WHERE user_id = 11 "
                "AND token_hash IS NULL",
                (create_token(11, "session-qa", "staff", session_id=12345),),
            )
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {session_token}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_password_change_revokes_all_sessions(self):
        login = self._login("session-qa")
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 200)

        changed = self.client.put(
            "/api/users/11/password",
            json={"new_password": "Rotated-Pass-2"},
            headers=self.admin_headers,
        )
        self.assertEqual(changed.status_code, 200)

        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)
        with get_db() as conn:
            row = conn.execute(
                "SELECT token_version FROM users WHERE id = 11"
            ).fetchone()
            revoked = conn.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = 11 AND revoked_at IS NULL"
            ).fetchone()[0]
        self.assertGreaterEqual(row["token_version"], 2)
        self.assertEqual(revoked, 0)
        self.assertTrue(self._audit_rows("user.password.change", actor_user_id=10))

        # Пароль заменяем обратно, чтобы тест был повторяемым.
        self.assertEqual(
            self.client.put(
                "/api/users/11/password",
                json={"new_password": self.PASSWORD},
                headers=self.admin_headers,
            ).status_code,
            200,
        )

    def test_inactive_user_is_blocked_on_login_and_by_token(self):
        blocked_login = self._login("blocked-qa")
        self.assertEqual(blocked_login.status_code, 403)
        self.assertTrue(self._audit_rows("auth.login.blocked", actor_user_id=12))

        token = make_session_token(12, "blocked-qa", "staff")
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("заблокирован", response.json()["detail"].lower())

    def test_failed_login_is_audited(self):
        response = self._login("session-qa", password="definitely-wrong")
        self.assertEqual(response.status_code, 401)
        rows = self._audit_rows("auth.login.failed")
        self.assertTrue(any(row["object_id"] == "session-qa" for row in rows))

    def test_role_and_entity_come_from_database(self):
        with get_db() as conn:
            session_id = conn.execute(
                "INSERT INTO auth_sessions (user_id, login, role) "
                "VALUES (13, 'entity2-qa', 'administrator')"
            ).lastrowid
        # Роль в токене намеренно устаревшая: приложение обязано читать её из БД.
        token = create_token(
            13,
            "entity2-qa",
            "staff",
            session_id=session_id,
            legal_entity_id=2,
        )
        user = get_current_user(self._request(token))

        self.assertEqual(user["role"], "administrator")
        self.assertEqual(user["legal_entity_id"], 2)
        self.assertEqual(user["scope_entity_ids"], [2])
        # Администратор без режима «Все юрлица» видит только своё юрлицо.
        self.assertFalse(user["all_entities"])
        self.assertEqual(
            get_tenant_context(), {"legal_entity_ids": [2], "is_group_admin": False}
        )

        # Понижение роли в БД действует немедленно, без перевыпуска токена.
        with get_db() as conn:
            conn.execute("UPDATE users SET role = 'staff' WHERE id = 13")
        try:
            demoted = get_current_user(self._request(token))
            self.assertEqual(demoted["role"], "staff")
            self.assertEqual(demoted["scope_entity_ids"], [2])
            self.assertEqual(
                get_tenant_context(), {"legal_entity_ids": [2], "is_group_admin": False}
            )
        finally:
            with get_db() as conn:
                conn.execute("UPDATE users SET role = 'administrator' WHERE id = 13")


class EntityScopeTests(unittest.TestCase):
    """Переключение юрлица: членство, сводный режим и запрет создания."""

    PASSWORD = "Scope-Pass-1"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        password_hash = hash_password(cls.PASSWORD)
        with get_db() as conn:
            conn.execute("DELETE FROM user_entities WHERE user_id IN (20, 21, 22)")
            conn.execute("DELETE FROM auth_sessions WHERE user_id IN (20, 21, 22)")
            conn.execute("DELETE FROM users WHERE id IN (20, 21, 22)")
            conn.execute(
                """
                INSERT INTO legal_entities (id, name, short_name, active)
                VALUES (2, 'Второе юрлицо', 'Второе', 1)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name
                """
            )
            conn.execute("DELETE FROM addresses WHERE id IN (5, 6, 7)")
            conn.execute(
                """
                INSERT INTO addresses (id, city, address_name, manufacturer, legal_entity_id, shared)
                VALUES (5, 'Ростов', 'Личный адрес первой', 'М1', 1, 0),
                       (6, 'Батайск', 'Личный адрес второй', 'М2', 2, 0),
                       (7, 'Азов', 'Общий адрес группы', 'М3', 1, 1)
                """
            )
            conn.execute(
                """
                INSERT INTO users (id, login, password_hash, role, legal_entity_id)
                VALUES (20, 'scope-admin', ?, 'admin', 1),
                       (21, 'scope-two', ?, 'staff', 2),
                       (22, 'scope-single', ?, 'staff', 1)
                """,
                (password_hash, password_hash, password_hash),
            )
            conn.execute(
                """
                INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
                VALUES (20, 1, 'admin', 1), (20, 2, 'admin', 0),
                       (21, 2, 'staff', 1),
                       (22, 1, 'staff', 1)
                """
            )

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def _login(self, login):
        response = self.client.post(
            "/api/login", json={"login": login, "password": self.PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def _switch(self, headers, entity_id):
        return self.client.post(
            "/api/session/legal-entity",
            json={"legal_entity_id": entity_id},
            headers=headers,
        )

    def test_options_follow_membership(self):
        single = self.client.get(
            "/api/legal-entities", headers=self._login("scope-single")
        ).json()
        self.assertEqual([option["id"] for option in single["options"]], [1])
        self.assertFalse(single["can_view_all"])
        self.assertEqual(single["current"]["legal_entity_id"], 1)

        admin = self.client.get(
            "/api/legal-entities", headers=self._login("scope-admin")
        ).json()
        self.assertEqual([option["id"] for option in admin["options"]], [1, 2])
        self.assertTrue(admin["can_view_all"])

    def test_switch_to_foreign_entity_is_forbidden(self):
        headers = self._login("scope-single")
        self.assertEqual(self._switch(headers, 2).status_code, 403)
        # Сводный режим доступен только групповому администратору.
        self.assertEqual(self._switch(headers, None).status_code, 403)

    def test_switch_persists_in_session_and_is_audited(self):
        headers = self._login("scope-two")
        self.assertEqual(
            self.client.get("/api/me", headers=headers).json()["legal_entity_id"], 2
        )
        self.assertEqual(self._switch(headers, 2).status_code, 200)
        self.assertEqual(
            self.client.get("/api/me", headers=headers).json()["legal_entity_id"], 2
        )
        rows = get_db_audit("auth.entity.switch")
        self.assertTrue(rows)

    def test_new_records_carry_session_entity(self):
        headers = self._login("scope-two")
        response = self.client.post(
            "/api/monitoring",
            json={"address_id": 6, "text": "Мониторинг второй организации", "media": []},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        report_id = response.json()["report_id"]
        with get_db() as conn:
            report = conn.execute(
                "SELECT legal_entity_id FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
            conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        self.assertEqual(report["legal_entity_id"], 2)

    def test_all_entities_mode_blocks_creation(self):
        headers = self._login("scope-admin")
        self.assertEqual(self._switch(headers, None).status_code, 200)
        me = self.client.get("/api/me", headers=headers).json()
        self.assertTrue(me["all_entities"])

        blocked = self.client.post(
            "/api/tasks",
            json={"address_id": 5, "description": "Нельзя без юрлица"},
            headers=headers,
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("юрлицо", blocked.json()["detail"].lower())

        # Возврат в конкретное юрлицо снова разрешает создание.
        self.assertEqual(self._switch(headers, 2).status_code, 200)
        allowed = self.client.post(
            "/api/tasks",
            json={"address_id": 6, "description": "Задание второго юрлица"},
            headers=headers,
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        task_id = allowed.json()["task_id"]
        with get_db() as conn:
            entity_id = conn.execute(
                "SELECT legal_entity_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()["legal_entity_id"]
            conn.execute("DELETE FROM task_status_history WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.assertEqual(entity_id, 2)

    def test_switcher_is_wired_into_role_pages(self):
        for page in ("client", "admin", "director", "crew", "head", "role_app"):
            html = (APP_DIR / "templates" / f"{page}.html").read_text(encoding="utf-8")
            self.assertIn("entity_scope.js", html, page)

        script = (APP_DIR / "static" / "entity_scope.js").read_text(encoding="utf-8")
        self.assertIn("/api/legal-entities", script)
        self.assertIn("/api/session/legal-entity", script)
        # Создание в сводном режиме блокируется и в интерфейсе.
        self.assertIn("entity-scope-blocked", script)

    def test_users_list_follows_scope(self):
        headers = self._login("scope-admin")
        in_entity = {row["login"] for row in self.client.get("/api/users", headers=headers).json()}
        self.assertIn("scope-single", in_entity)
        self.assertIn("scope-admin", in_entity)
        self.assertNotIn("scope-two", in_entity)

        self.assertEqual(self._switch(headers, None).status_code, 200)
        all_logins = {row["login"] for row in self.client.get("/api/users", headers=headers).json()}
        self.assertIn("scope-two", all_logins)

        # Пароль пользователя другого юрлица недоступен вне области видимости.
        self.assertEqual(self._switch(headers, 1).status_code, 200)
        foreign = self.client.put(
            "/api/users/21/password",
            json={"new_password": "Foreign-Pass-1"},
            headers=headers,
        )
        self.assertEqual(foreign.status_code, 403)


class LegalEntityAdminTests(unittest.TestCase):
    """Управление юрлицами и членством: только групповой админ в сводном режиме.

    Класс создаёт собственные юрлицо и пользователей, чтобы не зависеть от
    фикстуры соседних классов.
    """

    PASSWORD = "Group-Admin-Pass-1"
    ENTITY_ID = 3
    ADMIN_ID = 30
    MEMBER_ID = 31

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        password_hash = hash_password(cls.PASSWORD)
        with get_db() as conn:
            conn.execute(
                "DELETE FROM user_entities WHERE user_id IN (?, ?)",
                (cls.ADMIN_ID, cls.MEMBER_ID),
            )
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id IN (?, ?)",
                (cls.ADMIN_ID, cls.MEMBER_ID),
            )
            conn.execute(
                "DELETE FROM users WHERE id IN (?, ?)", (cls.ADMIN_ID, cls.MEMBER_ID)
            )
            conn.execute(
                """
                INSERT INTO legal_entities (id, name, short_name, active)
                VALUES (?, 'ООО Тестовое-3', 'Тестовое-3', 1)
                ON CONFLICT(id) DO UPDATE
                    SET name = excluded.name, active = 1
                """,
                (cls.ENTITY_ID,),
            )
            conn.execute(
                """
                INSERT INTO users (id, login, password_hash, role, legal_entity_id)
                VALUES (?, 'group-admin-qa', ?, 'admin', 1),
                       (?, 'member-3-qa', ?, 'staff', ?)
                """,
                (cls.ADMIN_ID, password_hash, cls.MEMBER_ID, password_hash, cls.ENTITY_ID),
            )
            conn.execute(
                """
                INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
                VALUES (?, 1, 'admin', 1), (?, ?, 'admin', 0),
                       (?, ?, 'staff', 1)
                """,
                (cls.ADMIN_ID, cls.ADMIN_ID, cls.ENTITY_ID, cls.MEMBER_ID, cls.ENTITY_ID),
            )

    @classmethod
    def tearDownClass(cls):
        with get_db() as conn:
            conn.execute(
                "DELETE FROM user_entities WHERE user_id IN (?, ?)",
                (cls.ADMIN_ID, cls.MEMBER_ID),
            )
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id IN (?, ?)",
                (cls.ADMIN_ID, cls.MEMBER_ID),
            )
            conn.execute(
                "DELETE FROM users WHERE id IN (?, ?)", (cls.ADMIN_ID, cls.MEMBER_ID)
            )
            conn.execute("DELETE FROM legal_entities WHERE id = ?", (cls.ENTITY_ID,))
        cls.client.close()

    def _login(self, login, password=None):
        response = self.client.post(
            "/api/login", json={"login": login, "password": password or self.PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def _group_admin(self):
        """Администратор группы, переключённый в сводный режим."""
        headers = self._login("group-admin-qa")
        response = self.client.post(
            "/api/session/legal-entity", json={"legal_entity_id": None}, headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        return headers

    def _set_entity_active(self, entity_id, active):
        with get_db() as conn:
            conn.execute(
                "UPDATE legal_entities SET active = ? WHERE id = ?",
                (1 if active else 0, entity_id),
            )

    def test_requires_group_admin_in_all_entities_mode(self):
        staff = self._login("member-3-qa")
        self.assertEqual(
            self.client.get("/api/admin/legal-entities", headers=staff).status_code, 403
        )
        # Админ группы, но в конкретном юрлице — тоже без доступа.
        in_entity = self._login("group-admin-qa")
        self.assertEqual(
            self.client.get("/api/admin/legal-entities", headers=in_entity).status_code, 403
        )
        self.assertEqual(
            self.client.get(
                "/api/admin/legal-entities", headers=self._group_admin()
            ).status_code,
            200,
        )

    def test_create_and_update_legal_entity(self):
        headers = self._group_admin()
        created = self.client.post(
            "/api/admin/legal-entities",
            json={"name": "ООО Четвёртое", "short_name": "Четвёртое", "inn": "6160000001"},
            headers=headers,
        )
        self.assertEqual(created.status_code, 200, created.text)
        entity_id = created.json()["id"]

        try:
            listed = self.client.get("/api/admin/legal-entities", headers=headers).json()
            row = [item for item in listed if item["id"] == entity_id][0]
            self.assertEqual(row["name"], "ООО Четвёртое")
            self.assertEqual(row["inn"], "6160000001")
            self.assertTrue(row["active"])
            self.assertEqual(row["usage"]["users"], 0)

            renamed = self.client.put(
                f"/api/admin/legal-entities/{entity_id}",
                json={"name": "ООО Четвёртое (переименовано)", "active": False},
                headers=headers,
            )
            self.assertEqual(renamed.status_code, 200, renamed.text)
            listed = self.client.get("/api/admin/legal-entities", headers=headers).json()
            row = [item for item in listed if item["id"] == entity_id][0]
            self.assertEqual(row["name"], "ООО Четвёртое (переименовано)")
            self.assertFalse(row["active"])

            # Выключить последнее активное юрлицо нельзя.
            active_ids = [
                item["id"]
                for item in self.client.get("/api/admin/legal-entities", headers=headers).json()
                if item["active"]
            ]
            for other_id in active_ids:
                if other_id != self.ENTITY_ID:
                    self._set_entity_active(other_id, False)
            last = self.client.put(
                f"/api/admin/legal-entities/{self.ENTITY_ID}",
                json={"active": False},
                headers=headers,
            )
            self.assertEqual(last.status_code, 400)
            self.assertIn("последнее", last.json()["detail"].lower())
        finally:
            with get_db() as conn:
                conn.execute("DELETE FROM legal_entities WHERE id = ?", (entity_id,))
                # Возвращаем всем юрлицам исходное состояние: соседние тесты
                # полагаются на два активных юрлица.
                conn.execute("UPDATE legal_entities SET active = 1")

        self.assertTrue(get_db_audit("legal_entity.create"))
        self.assertTrue(get_db_audit("legal_entity.update"))

    def test_cannot_disable_entity_leaving_users_without_access(self):
        headers = self._group_admin()
        # member-3-qa имеет доступ только к третьему юрлицу.
        response = self.client.put(
            f"/api/admin/legal-entities/{self.ENTITY_ID}",
            json={"active": False},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("пользовател", response.json()["detail"])

        # После выдачи доступа к первому юрлицу выключение проходит.
        membership = self.client.put(
            f"/api/admin/users/{self.MEMBER_ID}/entities",
            json={
                "role": "staff",
                "entities": [
                    {"legal_entity_id": self.ENTITY_ID, "is_default": True},
                    {"legal_entity_id": 1, "is_default": False},
                ],
            },
            headers=headers,
        )
        self.assertEqual(membership.status_code, 200, membership.text)

        response = self.client.put(
            f"/api/admin/legal-entities/{self.ENTITY_ID}",
            json={"active": False},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self._set_entity_active(self.ENTITY_ID, True)

        # Возвращаем исходное членство.
        self.client.put(
            f"/api/admin/users/{self.MEMBER_ID}/entities",
            json={
                "role": "staff",
                "entities": [{"legal_entity_id": self.ENTITY_ID, "is_default": True}],
            },
            headers=headers,
        )

    def test_membership_update_changes_role_and_default_entity(self):
        headers = self._group_admin()
        updated = self.client.put(
            f"/api/admin/users/{self.MEMBER_ID}/entities",
            json={
                "role": "head",
                "entities": [
                    {"legal_entity_id": 1, "is_default": True},
                    {"legal_entity_id": self.ENTITY_ID, "is_default": False},
                ],
            },
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["default_entity_id"], 1)

        users = {
            row["login"]: row
            for row in self.client.get("/api/admin/users", headers=headers).json()
        }
        target = users["member-3-qa"]
        self.assertEqual(target["role"], "head")
        self.assertEqual(target["legal_entity_id"], 1)
        self.assertEqual(
            sorted(item["legal_entity_id"] for item in target["entities"]), [1, self.ENTITY_ID]
        )
        self.assertTrue(get_db_audit("user.entities.update"))

        # Понижение роли действует на следующем же запросе пользователя.
        member_headers = self._login("member-3-qa")
        self.assertEqual(
            self.client.get("/api/me", headers=member_headers).json()["role"], "head"
        )
        self.assertEqual(
            self.client.get("/api/me", headers=member_headers).json()["legal_entity_id"], 1
        )

        # Возврат исходного состояния.
        self.client.put(
            f"/api/admin/users/{self.MEMBER_ID}/entities",
            json={
                "role": "staff",
                "entities": [{"legal_entity_id": self.ENTITY_ID, "is_default": True}],
            },
            headers=headers,
        )
        with get_db() as conn:
            row = conn.execute(
                "SELECT role, legal_entity_id FROM users WHERE id = ?", (self.MEMBER_ID,)
            ).fetchone()
        self.assertEqual((row["role"], row["legal_entity_id"]), ("staff", self.ENTITY_ID))

    def test_membership_validation(self):
        headers = self._group_admin()
        cases = [
            ("не-роль", [{"legal_entity_id": 1}], self.MEMBER_ID, 400),
            ("staff", [], self.MEMBER_ID, 400),
            ("staff", [{"legal_entity_id": 999}], self.MEMBER_ID, 400),
            ("staff", [{"legal_entity_id": 1}], 999999, 404),
        ]
        for role, entities, user_id, expected in cases:
            response = self.client.put(
                f"/api/admin/users/{user_id}/entities",
                json={"role": role, "entities": entities},
                headers=headers,
            )
            self.assertEqual(response.status_code, expected, (role, entities, response.text))

        # В группе должен остаться администратор: временно убираем остальных.
        with get_db() as conn:
            others = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM users WHERE id <> ? AND role IN ('admin', 'administrator')",
                    (self.ADMIN_ID,),
                ).fetchall()
            ]
            conn.execute(
                "UPDATE users SET role = 'service' WHERE id IN (%s)"
                % ",".join("?" * len(others)),
                tuple(others),
            ) if others else None
        try:
            only_admin = self.client.put(
                f"/api/admin/users/{self.ADMIN_ID}/entities",
                json={
                    "role": "service",
                    "entities": [
                        {"legal_entity_id": 1, "is_default": True},
                        {"legal_entity_id": self.ENTITY_ID, "is_default": False},
                    ],
                },
                headers=headers,
            )
            self.assertEqual(only_admin.status_code, 400, only_admin.text)
            self.assertIn("администратора", only_admin.json()["detail"])
        finally:
            with get_db() as conn:
                for user_id in others:
                    conn.execute(
                        "UPDATE users SET role = 'administrator' WHERE id = ?", (user_id,)
                    )

    def test_admin_ui_is_wired(self):
        html = (APP_DIR / "templates" / "admin.html").read_text(encoding="utf-8")
        for element in ("entitiesSection", "entitiesList", "createEntityBtn", "newUserEntity"):
            self.assertIn(element, html, element)

        script = (APP_DIR / "static" / "admin-settings.js").read_text(encoding="utf-8")
        self.assertIn("/api/admin/legal-entities", script)
        self.assertIn("/api/admin/users", script)
        self.assertIn("entities`,", script)  # PUT доступа к юрлицам пользователя
        self.assertIn("all_entities", script)

    def test_user_creation_accepts_entity(self):
        headers = self._group_admin()
        response = self.client.post(
            "/api/users",
            json={
                "login": "scoped-created",
                "password": "Scoped-Pass-1",
                "role": "service",
                "legal_entity_id": self.ENTITY_ID,
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        try:
            with get_db() as conn:
                user = conn.execute(
                    "SELECT id, legal_entity_id FROM users WHERE login = 'scoped-created'"
                ).fetchone()
                membership = conn.execute(
                    "SELECT legal_entity_id, is_default FROM user_entities WHERE user_id = ?",
                    (user["id"],),
                ).fetchall()
            self.assertEqual(user["legal_entity_id"], self.ENTITY_ID)
            self.assertEqual(
                [(row["legal_entity_id"], row["is_default"]) for row in membership],
                [(self.ENTITY_ID, 1)],
            )
        finally:
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM user_entities WHERE user_id IN "
                    "(SELECT id FROM users WHERE login = 'scoped-created')"
                )
                conn.execute("DELETE FROM users WHERE login = 'scoped-created'")

        # В сводном режиме юрлицо обязательно.
        missing = self.client.post(
            "/api/users",
            json={"login": "no-entity", "password": "Scoped-Pass-1", "role": "service"},
            headers=headers,
        )
        self.assertEqual(missing.status_code, 400)


def get_db_audit(action):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM audit_log WHERE action = ?", (action,)
        ).fetchall()


if __name__ == "__main__":
    unittest.main()


class RoleConstructorTests(unittest.TestCase):
    """Конструктор ролей: свои роли, возможности и область «только свои»."""

    PASSWORD = "Role-Pass-1"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        password_hash = hash_password(cls.PASSWORD)
        with get_db() as conn:
            # Класс сам обеспечивает второе активное юрлицо: сводный режим
            # доступен администратору только при двух и более юрлицах.
            conn.execute(
                """
                INSERT INTO legal_entities (id, name, short_name, active)
                VALUES (7, 'ООО Тестовое-роли', 'Тестовое-роли', 1)
                ON CONFLICT(id) DO UPDATE SET active = 1
                """
            )
            conn.execute(
                """
                INSERT INTO addresses (id, city, address_name, manufacturer,
                                       legal_entity_id, shared)
                VALUES (900, 'Тест-город', 'Тестовый адрес', 'Тест', 1, 0)
                ON CONFLICT(id) DO UPDATE SET active = 1
                """
            )
            conn.execute(
                "DELETE FROM user_entities WHERE user_id IN (40, 41, 42)"
            )
            conn.execute("DELETE FROM auth_sessions WHERE user_id IN (40, 41, 42)")
            conn.execute("DELETE FROM users WHERE id IN (40, 41, 42)")
            conn.execute(
                """
                INSERT INTO users (id, login, password_hash, role, legal_entity_id)
                VALUES (40, 'roles-admin', ?, 'admin', 1),
                       (42, 'roles-other', ?, 'service', 1)
                """,
                (password_hash, password_hash),
            )
            conn.execute(
                """
                INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
                VALUES (40, 1, 'admin', 1), (42, 1, 'service', 1)
                """
            )
        cls.admin_headers = {"Authorization": f"Bearer {make_session_token(40, 'roles-admin', 'admin')}"}

    @classmethod
    def tearDownClass(cls):
        with get_db() as conn:
            conn.execute(
                "DELETE FROM reports WHERE user_id IN (40, 41, 42) "
                "OR text IN ('Свой мониторинг', 'Чужой мониторинг')"
            )
            conn.execute("DELETE FROM user_entities WHERE user_id IN (40, 41, 42)")
            conn.execute("DELETE FROM auth_sessions WHERE user_id IN (40, 41, 42)")
            conn.execute("DELETE FROM users WHERE id IN (40, 41, 42)")
            conn.execute("DELETE FROM addresses WHERE id = 900")
            conn.execute("DELETE FROM legal_entities WHERE id = 7")
            conn.execute("DELETE FROM role_capabilities WHERE role_id IN (SELECT id FROM roles WHERE code LIKE 'qa_%')")
            conn.execute("DELETE FROM roles WHERE code LIKE 'qa_%'")
        cls.client.close()

    def _group_admin(self):
        headers = {"Authorization": f"Bearer {make_session_token(40, 'roles-admin', 'admin')}"}
        response = self.client.post(
            "/api/session/legal-entity", json={"legal_entity_id": None}, headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        return headers

    def _create_role(self, headers, code, capabilities, scope="entity", workplace="service"):
        response = self.client.post(
            "/api/admin/roles",
            json={
                "code": code,
                "name": f"Тестовая {code}",
                "workplace": workplace,
                "scope": scope,
                "capabilities": capabilities,
            },
            headers=headers,
        )
        return response

    def test_role_catalog_is_exposed(self):
        data = self.client.get("/api/admin/roles", headers=self._group_admin()).json()
        codes = {item["code"] for item in data["roles"]}
        self.assertTrue({"admin", "service", "crew"}.issubset(codes), codes)
        self.assertIn("task.approve", {item["code"] for item in data["capabilities"]})
        admin_role = [item for item in data["roles"] if item["code"] == "admin"][0]
        self.assertTrue(admin_role["is_system"])
        self.assertGreater(len(admin_role["capabilities"]), 5)

    def test_role_validation_and_system_protection(self):
        headers = self._group_admin()

        unknown = self._create_role(headers, "qa_bad_caps", ["nope.capability"])
        self.assertEqual(unknown.status_code, 400)

        bad_code = self._create_role(headers, "Плохой код", ["monitoring.send"])
        self.assertEqual(bad_code.status_code, 400)

        duplicate = self._create_role(headers, "admin", ["monitoring.send"])
        self.assertEqual(duplicate.status_code, 400)

        data = self.client.get("/api/admin/roles", headers=headers).json()
        system_role = [item for item in data["roles"] if item["code"] == "service"][0]
        protected = self.client.put(
            f"/api/admin/roles/{system_role['id']}",
            json={"name": "Изменённая", "workplace": "service", "scope": "own", "capabilities": []},
            headers=headers,
        )
        self.assertEqual(protected.status_code, 400)
        self.assertIn("Системную", protected.json()["detail"])

    def test_custom_role_limits_actions(self):
        headers = self._group_admin()
        created = self._create_role(
            headers, "qa_contractor", ["monitoring.send", "task.comment"], scope="own"
        )
        self.assertEqual(created.status_code, 200, created.text)
        try:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO users (id, login, password_hash, role, legal_entity_id)
                    VALUES (41, 'qa-contractor', ?, 'qa_contractor', 1)
                    """,
                    (hash_password(self.PASSWORD),),
                )
                conn.execute(
                    """
                    INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
                    VALUES (41, 1, 'qa_contractor', 1)
                    """
                )

            login = self.client.post(
                "/api/login", json={"login": "qa-contractor", "password": self.PASSWORD}
            )
            self.assertEqual(login.status_code, 200, login.text)
            payload = login.json()
            self.assertEqual(payload["workplace"], "service")
            self.assertEqual(payload["home"], "/staff")
            self.assertIn("monitoring.send", payload["capabilities"])
            self.assertNotIn("task.create", payload["capabilities"])

            contractor = {"Authorization": f"Bearer {payload['token']}"}
            me = self.client.get("/api/me", headers=contractor).json()
            self.assertEqual(me["workplace"], "service")
            self.assertEqual(me["data_scope"], "own")

            # Возможности ограничивают действия: задания создавать нельзя.
            self.assertEqual(
                self.client.post(
                    "/api/tasks",
                    json={"address_id": 900, "description": "Нельзя"},
                    headers=contractor,
                ).status_code,
                403,
            )
            # Мониторинг разрешён.
            self.assertEqual(
                self.client.post(
                    "/api/monitoring",
                    json={"address_id": 900, "text": "Свой мониторинг", "media": []},
                    headers=contractor,
                ).status_code,
                200,
            )
            # Область «только свои»: чужой мониторинг в ленту не попадает.
            other = self.client.post(
                "/api/monitoring",
                json={"address_id": 900, "text": "Чужой мониторинг", "media": []},
                headers={"Authorization": f"Bearer {make_session_token(42, 'roles-other', 'service')}"},
            )
            self.assertEqual(other.status_code, 200, other.text)

            feed = self.client.get("/api/feed?limit=50", headers=contractor).json()
            texts = [item.get("text") for item in feed["items"]]
            self.assertIn("Свой мониторинг", texts)
            self.assertNotIn("Чужой мониторинг", texts)

            # Смена роли действует без перевыпуска токена.
            with get_db() as conn:
                conn.execute(
                    "UPDATE user_entities SET role = 'service' WHERE user_id = 41"
                )
            self.assertEqual(
                self.client.post(
                    "/api/tasks",
                    json={"address_id": 900, "description": "Теперь нельзя"},
                    headers=contractor,
                ).status_code,
                403,
            )
            with get_db() as conn:
                conn.execute(
                    "UPDATE user_entities SET role = 'qa_contractor' WHERE user_id = 41"
                )
        finally:
            with get_db() as conn:
                # Отчёты ссылаются на пользователя — сначала они.
                conn.execute("DELETE FROM media WHERE report_id IN ("
                             "SELECT id FROM reports WHERE user_id IN (41, 42))")
                conn.execute("DELETE FROM reports WHERE user_id IN (41, 42)")
                conn.execute("DELETE FROM auth_sessions WHERE user_id = 41")
                conn.execute("DELETE FROM user_entities WHERE user_id = 41")
                conn.execute("DELETE FROM users WHERE id = 41")

    def test_disabling_assigned_role_is_blocked(self):
        headers = self._group_admin()
        created = self._create_role(headers, "qa_assigned", ["monitoring.send"])
        self.assertEqual(created.status_code, 200, created.text)
        role_id = created.json()["id"]

        with get_db() as conn:
            conn.execute(
                "INSERT INTO user_entities (user_id, legal_entity_id, role, is_default) "
                "VALUES (42, 7, 'qa_assigned', 0)"
            )
        try:
            blocked = self.client.put(
                f"/api/admin/roles/{role_id}",
                json={
                    "name": "Тестовая qa_assigned",
                    "workplace": "service",
                    "scope": "entity",
                    "capabilities": ["monitoring.send"],
                    "active": False,
                },
                headers=headers,
            )
            self.assertEqual(blocked.status_code, 400, blocked.text)
            self.assertIn("назначена", blocked.json()["detail"])
        finally:
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM user_entities WHERE role = 'qa_assigned'"
                )
