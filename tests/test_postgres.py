import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


def sqlalchemy_url(pg_uri: str) -> str:
    return pg_uri.replace("postgresql://", "postgresql+psycopg://", 1)


def make_alembic_config(database_url: str) -> Config:
    os.environ["DATABASE_URL"] = database_url
    config = Config(str(APP_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(APP_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@unittest.skipUnless(pgserver, "pgserver is required for PostgreSQL tests")
class PostgresSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="albion-pg-schema-"))
        cls.server = pgserver.get_server(str(cls.root / "pgdata"))
        cls.database_url = sqlalchemy_url(cls.server.get_uri())
        command.upgrade(make_alembic_config(cls.database_url), "head")

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        shutil.rmtree(cls.root, ignore_errors=True)

    def connect(self):
        return psycopg.connect(migrator.psycopg_url(self.database_url))

    def test_schema_contains_core_and_monitoring_tables(self):
        with self.connect() as conn:
            tables = {
                (row[0], row[1])
                for row in conn.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema IN ('core', 'monitoring')
                    """
                ).fetchall()
            }
        self.assertIn(("core", "users"), tables)
        self.assertIn(("core", "legal_entities"), tables)
        self.assertIn(("core", "roles"), tables)
        self.assertIn(("core", "capabilities"), tables)
        self.assertIn(("core", "user_roles"), tables)
        self.assertIn(("core", "audit_log"), tables)
        self.assertIn(("monitoring", "addresses"), tables)
        self.assertIn(("monitoring", "tasks"), tables)
        self.assertIn(("monitoring", "reports"), tables)
        self.assertIn(("monitoring", "media"), tables)
        self.assertIn(("monitoring", "crew_monitorings"), tables)
        self.assertIn(("monitoring", "crew_report_comments"), tables)
        self.assertIn(("monitoring", "settings"), tables)

        with self.connect() as conn:
            address_columns = {
                row[0] for row in conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'monitoring' AND table_name = 'addresses'
                    """
                ).fetchall()
            }
            media_columns = {
                row[0] for row in conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'monitoring' AND table_name = 'media'
                    """
                ).fetchall()
            }
        self.assertIn("legal_entity_id", address_columns)
        self.assertIn("shared", address_columns)
        self.assertIn("storage", media_columns)
        self.assertIn("disk_file_id", media_columns)
        self.assertIn("preview_key", media_columns)

    def test_rls_policies_and_helpers_exist(self):
        with self.connect() as conn:
            policies = {
                (row[0], row[1]) for row in conn.execute(
                    "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'monitoring'"
                ).fetchall()
            }
            functions = {
                row[0] for row in conn.execute(
                    """
                    SELECT p.proname FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'core'
                    """
                ).fetchall()
            }
        self.assertEqual(len(policies), 11)
        self.assertTrue(all(name == "tenant_isolation" for _, name in policies))
        self.assertIn("current_legal_entity_ids", functions)
        self.assertIn("is_group_admin", functions)

    def test_downgrade_and_upgrade_are_repeatable(self):
        config = make_alembic_config(self.database_url)
        command.downgrade(config, "base")
        with self.connect() as conn:
            schemas = {
                row[0] for row in conn.execute(
                    "SELECT schema_name FROM information_schema.schemata"
                ).fetchall()
            }
        self.assertNotIn("core", schemas)
        self.assertNotIn("monitoring", schemas)

        command.upgrade(config, "head")
        with self.connect() as conn:
            count = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'monitoring'"
            ).fetchone()[0]
        self.assertEqual(count, 12)


@unittest.skipUnless(pgserver, "pgserver is required for PostgreSQL tests")
class PostgresMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="albion-pg-migration-"))
        cls.sqlite_path = cls.root / "monitoring.db"
        cls.server = pgserver.get_server(str(cls.root / "pgdata"))
        cls.database_url = sqlalchemy_url(cls.server.get_uri())
        command.upgrade(make_alembic_config(cls.database_url), "head")
        cls._create_sqlite_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _create_sqlite_fixture(cls):
        script = """
import os, sys
sys.path.insert(0, sys.argv[1])
import database
database.init_schema()
with database.get_db() as conn:
    conn.execute("INSERT INTO users (id, login, password_hash, role) VALUES (1,'admin','h','admin'),(2,'service','h','service'),(3,'crew','h','crew')")
    conn.execute("INSERT INTO addresses (id, city, address_name, manufacturer) VALUES (1,'Ростов-на-Дону','Вавилова 59/101','Вижн Лед')")
    conn.execute("INSERT INTO tasks (id, address_id, created_by, created_by_role, description, workflow_status, is_important) VALUES (1,1,1,'admin','Задача','open',1)")
    conn.execute("INSERT INTO reports (id, address_id, user_id, text) VALUES (1,1,2,'Отчёт')")
    conn.execute("INSERT INTO media (id, report_id, filename, media_type, original_filename) VALUES (1,1,'r1_a.jpg','photo','orig.jpg')")
    conn.execute("INSERT INTO comments (id, task_id, user_id, text, media_json) VALUES (1,1,2,'Комментарий','[\\"r1_a.jpg\\"]')")
    conn.execute("INSERT INTO director_comments (id, item_type, item_id, user_id, text) VALUES (1,'monitoring',1,1,'Комментарий директора')")
    conn.execute("INSERT INTO task_status_history (id, task_id, status, changed_by) VALUES (1,1,'open',1)")
    conn.execute("INSERT INTO crew_monitorings (id, address_id, user_id, text) VALUES (1,1,3,'Отчёт экипажа')")
    conn.execute("INSERT INTO crew_monitoring_media (id, crew_monitoring_id, filename, media_type) VALUES (1,1,'cr1_a.jpg','photo')")
    conn.execute("INSERT INTO crew_report_comments (id, crew_monitoring_id, user_id, text) VALUES (1,1,1,'Комментарий руководителя')")
    conn.execute("INSERT INTO crew_report_comment_media (id, comment_id, filename, media_type) VALUES (1,1,'crc1_a.jpg','photo')")
"""
        env = {
            **os.environ,
            "DB_PATH": str(cls.sqlite_path),
            "UPLOADS_DIR": str(cls.root / "uploads"),
            "JWT_SECRET": "unit-test-only-random-secret-0123456789abcdef",
        }
        result = subprocess.run(
            [sys.executable, "-c", script, str(APP_DIR)],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

    def test_migrates_rows_with_checksums_and_booleans(self):
        report = migrator.migrate(
            self.sqlite_path,
            self.database_url,
            "ООО Тест",
            truncate=True,
        )
        self.assertTrue(report["ok"], report)

        with psycopg.connect(migrator.psycopg_url(self.database_url)) as conn:
            users = conn.execute(
                "SELECT id, login, role, legal_entity_id FROM core.users ORDER BY id"
            ).fetchall()
            task = conn.execute(
                "SELECT id, description, is_important, legal_entity_id FROM monitoring.tasks"
            ).fetchone()
            media = conn.execute(
                "SELECT filename, storage, original_filename FROM monitoring.media"
            ).fetchone()
            comment = conn.execute(
                "SELECT text, media_json FROM monitoring.comments"
            ).fetchone()
            address = conn.execute(
                "SELECT city, shared FROM monitoring.addresses"
            ).fetchone()
            entity_count = conn.execute(
                "SELECT count(*) FROM core.legal_entities"
            ).fetchone()[0]

        self.assertEqual(len(users), 3)
        self.assertEqual({row[3] for row in users}, {1})
        self.assertEqual(task[1], "Задача")
        self.assertEqual(task[2], 1)
        self.assertEqual(task[3], 1)
        self.assertEqual(media, ("r1_a.jpg", "local", "orig.jpg"))
        self.assertEqual(comment[0], "Комментарий")
        self.assertEqual(comment[1], ["r1_a.jpg"])
        self.assertEqual(address, ("Ростов-на-Дону", 1))
        self.assertEqual(entity_count, 1)

    def test_refuses_non_empty_target_without_truncate(self):
        migrator.migrate(self.sqlite_path, self.database_url, "ООО Тест", truncate=True)
        with self.assertRaises(RuntimeError):
            migrator.migrate(self.sqlite_path, self.database_url, "ООО Тест")
        report = migrator.migrate(
            self.sqlite_path, self.database_url, "ООО Тест", truncate=True
        )
        self.assertTrue(report["ok"], report)

    def test_rls_isolates_legal_entities(self):
        migrator.migrate(self.sqlite_path, self.database_url, "ООО Тест", truncate=True)
        pg_url = migrator.psycopg_url(self.database_url)

        with psycopg.connect(pg_url) as conn:
            conn.execute(
                "INSERT INTO core.legal_entities (id, name) VALUES (2, 'ООО Второе')"
            )
            conn.execute(
                """
                INSERT INTO monitoring.addresses
                    (id, legal_entity_id, shared, city, address_name)
                VALUES
                    (10, 1, 0, 'Ростов-на-Дону', 'Своя для первой'),
                    (11, 1, 1, 'Ростов-на-Дону', 'Общая группы'),
                    (12, 2, 0, 'Батайск', 'Своя для второй')
                """
            )
            conn.execute(
                "CREATE ROLE rls_test LOGIN PASSWORD 'rls_test'"
            )
            conn.execute("GRANT USAGE ON SCHEMA core, monitoring TO rls_test")
            conn.execute(
                "GRANT SELECT ON ALL TABLES IN SCHEMA monitoring TO rls_test"
            )
            conn.execute(
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core TO rls_test"
            )
            conn.commit()

        parsed = urlparse(pg_url)
        host = parse_qs(parsed.query).get("host", [None])[0]
        params = {"dbname": "postgres", "user": "rls_test", "password": "rls_test"}
        if host:
            params["host"] = host
        else:
            params["host"] = parsed.hostname or "127.0.0.1"
            params["port"] = parsed.port or 5432

        with psycopg.connect(**params) as conn:
            conn.execute("SET app.legal_entity_ids = '2'")
            rows = conn.execute(
                "SELECT id FROM monitoring.addresses ORDER BY id"
            ).fetchall()
            # id 1 migrated with shared=true, so it is visible to every entity.
            self.assertEqual([row[0] for row in rows], [1, 11, 12])

            conn.execute("SET app.is_group_admin = 'on'")
            rows = conn.execute(
                "SELECT id FROM monitoring.addresses ORDER BY id"
            ).fetchall()
            self.assertEqual([row[0] for row in rows], [1, 10, 11, 12])


if __name__ == "__main__":
    unittest.main()
