import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

try:
    import pgserver  # noqa: E402
except ImportError:  # pragma: no cover - optional dev dependency
    pgserver = None

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

import migrate_sqlite_to_pg as migrator  # noqa: E402


FIXTURE_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
import database
from auth import hash_password

database.init_schema()
with database.get_db() as conn:
    for user_id, login, role in (
        (1, "admin", "admin"),
        (2, "service", "service"),
        (3, "crew", "crew"),
        (4, "head", "head"),
        (5, "director", "director"),
    ):
        conn.execute(
            "INSERT INTO users (id, login, password_hash, role) VALUES (?, ?, ?, ?)",
            (user_id, login, hash_password("TestPass1!"), role),
        )
    conn.execute(
        "INSERT INTO addresses (id, city, address_name, manufacturer) "
        "VALUES (1, 'Ростов-на-Дону', 'Вавилова 59/101', 'Вижн Лед')"
    )
"""


APP_FLOW_SCRIPT = r"""
import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from app import app

client = TestClient(app)

# auth: real login writes a session row
r = client.post("/api/login", json={"login": "admin", "password": "TestPass1!"})
assert r.status_code == 200, r.text
admin = {"Authorization": "Bearer " + r.json()["token"]}
assert client.get("/api/me", headers=admin).json()["login"] == "admin"
assert client.get("/api/me").status_code == 401

tokens = {}
for login, role in (("service", "service"), ("crew", "crew"), ("head", "head"), ("director", "director")):
    r = client.post("/api/login", json={"login": login, "password": "TestPass1!"})
    assert r.status_code == 200, (login, r.text)
    tokens[role] = {"Authorization": "Bearer " + r.json()["token"]}

assert client.get("/api/auth-sessions", headers=admin).status_code == 200
assert len(client.get("/api/users", headers=admin).json()) == 5

feed = client.get("/api/feed?limit=100&offset=0", headers=admin).json()
assert feed["limit"] == 100 and "items" in feed, feed
# RLS: entity 1 must not see the address/report created for entity 2.
assert not any(i.get("address_id") == 99 for i in feed["items"]), feed["items"]

r = client.post("/api/tasks", json={"address_id": 1, "description": "PG task"}, headers=admin)
assert r.status_code == 200, r.text
task_id = r.json()["task_id"]

r = client.post("/api/comments", json={"task_id": task_id, "text": "PG comment", "media": []}, headers=admin)
assert r.status_code == 200, r.text

buffer = io.BytesIO()
Image.new("RGB", (4, 4), "blue").save(buffer, format="JPEG")
r = client.post("/api/upload", files={"file": ("pg.jpg", buffer.getvalue(), "image/jpeg")}, headers=admin)
assert r.status_code == 200, r.text
filename = r.json()["filename"]
assert client.get(f"/api/media/{filename}", headers=admin).status_code == 200
assert client.post(
    f"/api/tasks/{task_id}/media", json={"filenames": [filename]}, headers=admin
).status_code == 200

assert client.post(f"/api/tasks/{task_id}/complete", headers=tokens["service"]).status_code == 200
assert client.post(f"/api/tasks/{task_id}/rework", headers=admin).status_code == 200
assert client.post(f"/api/tasks/{task_id}/complete", headers=tokens["service"]).status_code == 200
assert client.post(f"/api/tasks/{task_id}/approve", headers=admin).status_code == 200

history = client.get(f"/api/tasks/{task_id}/status-history", headers=admin).json()
assert len(history) >= 5, history

r = client.post(
    "/api/monitoring",
    json={"address_id": 1, "text": "PG monitoring", "media": []},
    headers=tokens["service"],
)
assert r.status_code == 200, r.text
report_id = r.json()["report_id"]
assert client.post(
    "/api/director/comments",
    json={"item_type": "monitoring", "item_id": report_id, "text": "PG director", "media": []},
    headers=tokens["director"],
).status_code == 200

r = client.post(
    "/api/crew/reports",
    json={"address_id": 1, "text": "PG crew report", "media": []},
    headers=tokens["crew"],
)
assert r.status_code == 200, r.text
crew_id = r.json()["report_id"]
assert client.post(
    f"/api/head/reports/{crew_id}/comments",
    json={"text": "PG head comment", "media": []},
    headers=tokens["head"],
).status_code == 200

assert client.put(
    "/api/settings", json={"key": "photos_enabled", "value": "0"}, headers=admin
).status_code == 200
assert client.get("/api/settings", headers=admin).json()["photos_enabled"] is False

export = client.get("/api/export/excel", headers=admin)
assert export.status_code == 200, export.text
assert "spreadsheet" in export.headers["content-type"]

feed = client.get("/api/feed?limit=200&offset=0", headers=admin).json()
task_item = [i for i in feed["items"] if i["type"] == "task" and i["id"] == task_id][0]
assert task_item["workflow_status"] == "completed", task_item
assert any(m["text"] == "PG comment" for m in task_item["messages"]), task_item["messages"]
assert isinstance(task_item["created_at"], str), task_item["created_at"]
assert any(i["type"] == "monitoring" and i["id"] == report_id for i in feed["items"])
assert any(i["type"] == "crew_report" and i["id"] == crew_id for i in feed["items"])

page1 = client.get("/api/feed?limit=1&offset=0", headers=admin).json()
page2 = client.get("/api/feed?limit=1&offset=1", headers=admin).json()
key1 = (page1["items"][0]["type"], page1["items"][0]["id"])
key2 = (page2["items"][0]["type"], page2["items"][0]["id"])
assert key1 != key2, (key1, key2)

assert client.post("/api/logout", headers=admin).status_code == 200
print(json.dumps({"ok": True, "task_id": task_id, "report_id": report_id, "crew_id": crew_id}))
"""


@unittest.skipUnless(pgserver, "pgserver is required for PostgreSQL tests")
class PostgresAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="albion-pg-app-"))
        cls.sqlite_path = cls.root / "monitoring.db"
        cls.server = pgserver.get_server(str(cls.root / "pgdata"))
        cls.database_url = cls.server.get_uri().replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

        fixture_env = {
            **os.environ,
            "DB_PATH": str(cls.sqlite_path),
            "UPLOADS_DIR": str(cls.root / "uploads"),
            "JWT_SECRET": "unit-test-only-random-secret-0123456789abcdef",
        }
        result = subprocess.run(
            [sys.executable, "-c", FIXTURE_SCRIPT, str(APP_DIR)],
            env=fixture_env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

        config = Config(str(APP_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(APP_DIR / "migrations"))
        os.environ["DATABASE_URL"] = cls.database_url
        command.upgrade(config, "head")

        report = migrator.migrate(cls.sqlite_path, cls.database_url, "ООО Тест", truncate=True)
        if not report.get("ok"):
            raise RuntimeError(report)

        # Data of another legal entity must stay invisible to the app (RLS).
        raw_url = migrator.psycopg_url(cls.database_url)
        with psycopg.connect(raw_url) as conn:
            conn.execute("INSERT INTO core.legal_entities (id, name) VALUES (2, 'ООО Второе')")
            conn.execute(
                """
                INSERT INTO monitoring.addresses
                    (id, legal_entity_id, shared, city, address_name)
                VALUES (99, 2, 0, 'Батайск', 'Чужая для второй')
                """
            )
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_full_app_flow_on_postgres(self):
        env = {
            **os.environ,
            "DB_BACKEND": "postgres",
            "DATABASE_URL": self.database_url,
            "JWT_SECRET": "unit-test-only-random-secret-0123456789abcdef",
            "UPLOADS_DIR": str(self.root / "uploads"),
            "DB_PATH": str(self.sqlite_path),
        }
        result = subprocess.run(
            [sys.executable, "-c", APP_FLOW_SCRIPT],
            env=env,
            cwd=str(APP_DIR),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["task_id"], 1)
        self.assertGreaterEqual(payload["report_id"], 1)
        self.assertGreaterEqual(payload["crew_id"], 1)


if __name__ == "__main__":
    unittest.main()
