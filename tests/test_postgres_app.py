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


def bcrypt_hash(password: str) -> str:
    """Хэш пароля в отдельном процессе.

    Импортировать `auth`/`database` внутри тест-процесса нельзя: модуль базы
    фиксирует DB_PATH при импорте и сломает изоляцию соседних тест-модулей.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from auth import hash_password; print(hash_password(sys.argv[2]))",
            str(APP_DIR),
            password,
        ],
        env={**os.environ, "JWT_SECRET": "unit-test-only-random-secret-0123456789abcdef"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


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
# Админ группы начинает работу в своём юрлице и видит только его пользователей.
assert len(client.get("/api/users", headers=admin).json()) == 5
assert client.get("/api/me", headers=admin).json()["legal_entity_id"] == 1

scope = client.get("/api/legal-entities", headers=admin).json()
assert scope["can_view_all"] is True, scope
assert {option["id"] for option in scope["options"]} == {1, 2}, scope
assert scope["current"] == {"legal_entity_id": 1, "all_entities": False}, scope

# Переключение в режим «Все юрлица»: видны пользователи обеих организаций.
assert client.post(
    "/api/session/legal-entity", json={"legal_entity_id": None}, headers=admin
).status_code == 200
assert len(client.get("/api/users", headers=admin).json()) == 6
assert client.get("/api/me", headers=admin).json()["all_entities"] is True

# В сводном режиме создавать нельзя — сначала нужно выбрать юрлицо.
blocked = client.post(
    "/api/tasks", json={"address_id": 1, "description": "Без юрлица"}, headers=admin
)
assert blocked.status_code == 400, blocked.text

# Возврат в конкретное юрлицо.
assert client.post(
    "/api/session/legal-entity", json={"legal_entity_id": 1}, headers=admin
).status_code == 200
assert client.get("/api/me", headers=admin).json()["all_entities"] is False
assert len(client.get("/api/users", headers=admin).json()) == 5

# Юрлицо берётся из сессии и уходит в RLS: вторая организация видит только своё
# и общие адреса, а личные адреса первой организации ей недоступны.
r = client.post("/api/login", json={"login": "entity2", "password": "TestPass1!"})
assert r.status_code == 200, r.text
entity2 = {"Authorization": "Bearer " + r.json()["token"]}
assert client.get("/api/me", headers=entity2).json()["role"] == "staff"

entity2_addresses = {row["id"] for row in client.get("/api/addresses", headers=entity2).json()}
assert 99 in entity2_addresses, entity2_addresses      # свой адрес
assert 100 in entity2_addresses, entity2_addresses    # общий адрес
assert 101 not in entity2_addresses, entity2_addresses  # личный адрес первой

entity1_addresses = {
    row["id"] for row in client.get("/api/addresses", headers=tokens["service"]).json()
}
assert 100 in entity1_addresses and 101 in entity1_addresses, entity1_addresses
assert 99 not in entity1_addresses, entity1_addresses

# Администратор группы в конкретном юрлице видит только его данные,
# а в сводном режиме — данные всех юрлиц группы.
admin_addresses = {row["id"] for row in client.get("/api/addresses", headers=admin).json()}
assert 101 in admin_addresses and 99 not in admin_addresses, admin_addresses

assert client.post(
    "/api/session/legal-entity", json={"legal_entity_id": None}, headers=admin
).status_code == 200
all_addresses = {row["id"] for row in client.get("/api/addresses", headers=admin).json()}
assert {1, 99, 100, 101}.issubset(all_addresses), all_addresses

assert client.post(
    "/api/session/legal-entity", json={"legal_entity_id": 1}, headers=admin
).status_code == 200

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

# Отзыв сессий: выход закрывает именно свою сессию, смена пароля — все сессии.
assert client.post("/api/logout", headers=tokens["crew"]).status_code == 200
assert client.get("/api/me", headers=tokens["crew"]).status_code == 401
assert client.get("/api/me", headers=tokens["service"]).status_code == 200

assert client.put(
    "/api/users/2/password",
    json={"new_password": "Rotated-Pass-9"},
    headers=admin,
).status_code == 200
assert client.get("/api/me", headers=tokens["service"]).status_code == 401
assert client.post(
    "/api/login", json={"login": "service", "password": "TestPass1!"}
).status_code == 401
assert client.post(
    "/api/login", json={"login": "service", "password": "Rotated-Pass-9"}
).status_code == 200

assert client.post("/api/logout", headers=admin).status_code == 200
assert client.get("/api/me", headers=admin).status_code == 401
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
        cls.raw_url = raw_url
        with psycopg.connect(raw_url) as conn:
            conn.execute("INSERT INTO core.legal_entities (id, name) VALUES (2, 'ООО Второе')")
            conn.execute(
                """
                INSERT INTO monitoring.addresses
                    (id, legal_entity_id, shared, city, address_name)
                VALUES (99, 2, 0, 'Батайск', 'Чужая для второй')
                """
            )
            # Общий адрес первой организации доступен обеим организациям.
            conn.execute(
                """
                INSERT INTO monitoring.addresses
                    (id, legal_entity_id, shared, city, address_name)
                VALUES (100, 1, 1, 'Шахты', 'Общая для всех')
                """
            )
            # Личный адрес первой организации: второй он не виден.
            conn.execute(
                """
                INSERT INTO monitoring.addresses
                    (id, legal_entity_id, shared, city, address_name)
                VALUES (101, 1, 0, 'Азов', 'Только для первой')
                """
            )
            # Пользователь второй организации: роль без групповых прав.
            conn.execute(
                """
                INSERT INTO core.users (id, login, password_hash, role, legal_entity_id)
                VALUES (6, 'entity2', %s, 'staff', 2)
                """,
                (bcrypt_hash("TestPass1!"),),
            )
            # Явные id сдвигают последовательности: иначе следующая вставка
            # из приложения упрётся в конфликт первичного ключа.
            for table in ("core.users", "monitoring.addresses"):
                conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT MAX(id) FROM {table}))"
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

        # Аудит: приложение реально писало действия в core.audit_log,
        # а не теряло их из-за ошибок или отката транзакции.
        with psycopg.connect(self.raw_url) as conn:
            rows = conn.execute(
                "SELECT action, COUNT(*) FROM core.audit_log GROUP BY action"
            ).fetchall()
            entities = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT legal_entity_id FROM core.audit_log"
                ).fetchall()
            }

        counts = dict(rows)
        for action in (
            "auth.login",
            "auth.logout",
            "auth.login.failed",
            "user.password.change",
            "task.create",
            "task.complete",
        ):
            self.assertIn(action, counts, counts)
            self.assertGreaterEqual(counts[action], 1, counts)

        # Аудит ведётся с юрлицом автора: обе организации попали в журнал.
        # У неудачных входов юрлицо неизвестно, поэтому там NULL.
        self.assertTrue({1, 2}.issubset(entities), entities)

    def test_audit_log_is_isolated_per_entity(self):
        with psycopg.connect(self.raw_url) as conn:
            conn.execute(
                "INSERT INTO core.audit_log (legal_entity_id, action) VALUES (1, 'probe.first')"
            )
            conn.execute(
                "INSERT INTO core.audit_log (legal_entity_id, action) VALUES (2, 'probe.second')"
            )
            conn.commit()

        def visible(entity, group_admin):
            with psycopg.connect(self.raw_url) as conn:
                conn.execute("SET ROLE albion_app")
                conn.execute(f"SET app.legal_entity_ids = '{entity}'")
                conn.execute(f"SET app.is_group_admin = '{group_admin}'")
                return {
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT legal_entity_id FROM core.audit_log"
                    ).fetchall()
                }

        self.assertNotIn(2, visible(1, "off"))
        self.assertNotIn(1, visible(2, "off"))
        self.assertEqual(visible(1, "on"), {1, 2})


if __name__ == "__main__":
    unittest.main()
