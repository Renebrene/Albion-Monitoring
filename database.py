import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import os

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "data" / "monitoring.db"))
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", Path(__file__).parent.parent / "uploads"))
PRIVATE_MEDIA_DIR = (
    Path(os.environ["PRIVATE_MEDIA_DIR"])
    if os.environ.get("PRIVATE_MEDIA_DIR", "").strip()
    else None
)

DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# PostgreSQL backend
#
# app.py keeps using sqlite3-style SQL (?, CURRENT_TIMESTAMP, lastrowid).
# The adapter below translates that to psycopg/PostgreSQL and returns
# dict-like rows and SQLite-like timestamp strings, so API/UI stay unchanged.
# Schema and migrations are managed by Alembic (`migrations/`).
# ---------------------------------------------------------------------------

_INSERT_RE = re.compile(r"^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][\w\.]*)", re.IGNORECASE)
_PG_TABLES_WITHOUT_ID = {"settings", "schema_migrations"}

_pg_engine = None


def _get_pg_engine():
    global _pg_engine
    if _pg_engine is None:
        if not DATABASE_URL:
            raise RuntimeError("DB_BACKEND=postgres requires DATABASE_URL")
        from sqlalchemy import create_engine, event

        _pg_engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
            connect_args={
                "options": "-csearch_path=monitoring,core,public",
            },
        )

        @event.listens_for(_pg_engine, "connect")
        def _apply_app_role(dbapi_connection, _record):
            # Enforce RLS: the owner bypasses policies, albion_app does not.
            # Tenant context is single-entity until per-user context lands.
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET ROLE albion_app")
                cursor.execute("SET app.legal_entity_ids = '1'")
                cursor.execute("SET app.is_group_admin = 'off'")
            finally:
                cursor.close()

    return _pg_engine


def _pg_value(value):
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


class _PgRow:
    def __init__(self, mapping):
        self._mapping = {key: _pg_value(value) for key, value in mapping.items()}
        self._values = tuple(self._mapping.values())

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._mapping[key]
        return self._values[key]

    def __contains__(self, key):
        return key in self._mapping

    def __iter__(self):
        return iter(self._values)

    def keys(self):
        return self._mapping.keys()

    def items(self):
        return self._mapping.items()

    def get(self, key, default=None):
        return self._mapping.get(key, default)


class _PgResult:
    def __init__(self, result, lastrowid=None):
        self._result = result
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        return _PgRow(dict(row._mapping))

    def fetchall(self):
        return [_PgRow(dict(row._mapping)) for row in self._result.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


def _translate_pg_sql(sql: str):
    translated = sql.replace("?", "%s")
    match = _INSERT_RE.match(translated)
    use_returning = False

    if match:
        table = match.group(1).split(".")[-1].lower()
        if table not in _PG_TABLES_WITHOUT_ID and "returning" not in translated.lower():
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
            use_returning = True

    return translated, use_returning


class _PgConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=()):
        # Named parameters (:name) are used by the Excel export queries.
        if isinstance(params, dict):
            translated = re.sub(r"(?<![\w:]):([A-Za-z_]\w*)", r"%(\1)s", sql)
            # PostgreSQL cannot infer types for bare parameters in IS NULL
            # and in date(...) comparisons.
            translated = re.sub(
                r"%\((\w+)\)s IS NULL",
                r"CAST(%(\1)s AS text) IS NULL",
                translated,
            )
            translated = re.sub(
                r"(date\([^)]*\)\s*(?:>=|<=|>|<|=)\s*)%\((\w+)\)s",
                r"\1CAST(%(\2)s AS date)",
                translated,
            )
            result = self._connection.exec_driver_sql(translated, params)
            return _PgResult(result)

        translated, use_returning = _translate_pg_sql(sql)
        result = self._connection.exec_driver_sql(translated, tuple(params or ()))
        lastrowid = None

        if use_returning:
            row = result.fetchone()
            lastrowid = row[0] if row is not None else None

        return _PgResult(result, lastrowid)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


@contextmanager
def _sqlite_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _postgres_db():
    engine = _get_pg_engine()
    connection = engine.connect()
    wrapper = _PgConnection(connection)
    try:
        yield wrapper
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def get_db():
    backend = _sqlite_db() if DB_BACKEND != "postgres" else _postgres_db()
    with backend as conn:
        yield conn


def _table_columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn, table: str, column: str, definition: str) -> bool:
    if column in _table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def _migration_1_workflow_and_comments(conn):
    """Add the workflow/comment schema missing from legacy installs; additive only."""
    workflow_status_added = _add_column_if_missing(
        conn, "tasks", "workflow_status",
        "TEXT NOT NULL DEFAULT 'open' CHECK(workflow_status IN ('open','pending_review','completed','rework','closed'))",
    )
    _add_column_if_missing(conn, "tasks", "created_by_role", "TEXT")
    _add_column_if_missing(conn, "tasks", "is_important", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "tasks", "close_reason", "TEXT")
    if workflow_status_added:
        conn.execute(
            "UPDATE tasks SET workflow_status = CASE WHEN status = 'done' THEN 'completed' ELSE 'open' END"
        )
    else:
        conn.execute(
            "UPDATE tasks SET workflow_status = CASE WHEN status = 'done' THEN 'completed' ELSE 'open' END "
            "WHERE workflow_status IS NULL OR workflow_status = ''"
        )
    conn.execute(
        "UPDATE tasks SET created_by_role = (SELECT role FROM users WHERE users.id = tasks.created_by) "
        "WHERE created_by_role IS NULL OR created_by_role = ''"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            text TEXT NOT NULL,
            media_json TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS director_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL CHECK(item_type IN ('task','monitoring')),
            item_id INTEGER NOT NULL,
            report_id INTEGER REFERENCES reports(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            text TEXT NOT NULL,
            media_json TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id),
            status TEXT NOT NULL,
            changed_by INTEGER NOT NULL REFERENCES users(id),
            changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            comment TEXT
        )
    """)

    # Upgrade older partial versions of these tables without rebuilding or deleting rows.
    for column, definition in (
        ("task_id", "INTEGER REFERENCES tasks(id)"),
        ("user_id", "INTEGER REFERENCES users(id)"),
        ("text", "TEXT NOT NULL DEFAULT ''"),
        ("media_json", "TEXT"),
        ("created_at", "DATETIME"),
    ):
        _add_column_if_missing(conn, "comments", column, definition)
    for column, definition in (
        ("item_type", "TEXT"),
        ("item_id", "INTEGER"),
        ("user_id", "INTEGER REFERENCES users(id)"),
        ("text", "TEXT NOT NULL DEFAULT ''"),
        ("media_json", "TEXT"),
        ("created_at", "DATETIME"),
        ("report_id", "INTEGER REFERENCES reports(id)"),
    ):
        _add_column_if_missing(conn, "director_comments", column, definition)
    for column, definition in (
        ("task_id", "INTEGER REFERENCES tasks(id)"),
        ("status", "TEXT NOT NULL DEFAULT 'open'"),
        ("changed_by", "INTEGER REFERENCES users(id)"),
        ("changed_at", "DATETIME"),
        ("comment", "TEXT"),
    ):
        _add_column_if_missing(conn, "task_status_history", column, definition)

    conn.execute("UPDATE comments SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    conn.execute("UPDATE director_comments SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    conn.execute("UPDATE task_status_history SET changed_at = CURRENT_TIMESTAMP WHERE changed_at IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_task_created ON comments(task_id, created_at, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_director_comments_item ON director_comments(item_type, item_id, created_at, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dc_report ON director_comments(report_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_history_task_changed ON task_status_history(task_id, changed_at, id)")


def _migration_2_soft_hide_and_original_filenames(conn):
    for table in ("tasks", "reports", "crew_monitorings"):
        _add_column_if_missing(conn, table, "hidden", "INTEGER NOT NULL DEFAULT 0")

    for table in ("media", "crew_monitoring_media", "crew_report_comment_media"):
        _add_column_if_missing(conn, table, "original_filename", "TEXT")

    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_reports_address ON reports(address_id)",
        "CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_reports_task ON reports(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_media_report ON media(report_id)",
        "CREATE INDEX IF NOT EXISTS idx_media_task ON media(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_addresses_city ON addresses(city)",
        "CREATE INDEX IF NOT EXISTS idx_addresses_manufacturer ON addresses(manufacturer)",
    ):
        conn.execute(index_sql)


def _apply_schema_migrations(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    migrations = (
        (1, "workflow_and_comments", _migration_1_workflow_and_comments),
        (2, "soft_hide_and_original_filenames", _migration_2_soft_hide_and_original_filenames),
    )
    for version, name, migration in migrations:
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        if applied:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (version, name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _init_sqlite_schema():

    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','service','director','crew','head','staff','administrator','curator')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                login TEXT NOT NULL,
                role TEXT NOT NULL,
                login_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                logout_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY,
                city TEXT NOT NULL,
                address_name TEXT NOT NULL,
                manufacturer TEXT,
                filter1 TEXT,
                filter2 TEXT,
                filter3 TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address_id INTEGER NOT NULL REFERENCES addresses(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                task_id INTEGER REFERENCES tasks(id),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                text TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0
            );

            -- Фото и видео (media_type: 'photo' или 'video')
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
                task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('photo','video')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Отдельные мониторинги Экипажа.
            -- Не связаны с обычными service-мониторингами из reports.
            CREATE TABLE IF NOT EXISTS crew_monitorings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address_id INTEGER NOT NULL REFERENCES addresses(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                text TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0
            );

            -- Фото и видео мониторингов Экипажа.
            CREATE TABLE IF NOT EXISTS crew_monitoring_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew_monitoring_id INTEGER NOT NULL
                    REFERENCES crew_monitorings(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('photo','video')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Комментарии Руководителя к отчётам Экипажа.
            CREATE TABLE IF NOT EXISTS crew_report_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew_monitoring_id INTEGER NOT NULL
                    REFERENCES crew_monitorings(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL
                    REFERENCES users(id),
                text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Фото и видео в комментариях Руководителя.
            CREATE TABLE IF NOT EXISTS crew_report_comment_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id INTEGER NOT NULL
                    REFERENCES crew_report_comments(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('photo','video')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address_id INTEGER NOT NULL REFERENCES addresses(id),
                created_by INTEGER NOT NULL REFERENCES users(id),
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                closed_by INTEGER REFERENCES users(id),
                hidden INTEGER NOT NULL DEFAULT 0
            );

            -- Настройки приложения (вкл/выкл фото, видео)
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_reports_address ON reports(address_id);
            CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);
            CREATE INDEX IF NOT EXISTS idx_reports_task ON reports(task_id);
            CREATE INDEX IF NOT EXISTS idx_media_report ON media(report_id);
            CREATE INDEX IF NOT EXISTS idx_media_task ON media(task_id);
            CREATE INDEX IF NOT EXISTS idx_crew_monitorings_address
                ON crew_monitorings(address_id);
            CREATE INDEX IF NOT EXISTS idx_crew_monitorings_created
                ON crew_monitorings(created_at);

            CREATE INDEX IF NOT EXISTS idx_crew_report_comments_report
                ON crew_report_comments(crew_monitoring_id);

            CREATE INDEX IF NOT EXISTS idx_crew_report_comments_created
                ON crew_report_comments(created_at);

            CREATE INDEX IF NOT EXISTS idx_crew_report_comments_user
                ON crew_report_comments(user_id);

            CREATE INDEX IF NOT EXISTS idx_crew_report_comment_media
                ON crew_report_comment_media(comment_id);
            CREATE INDEX IF NOT EXISTS idx_crew_monitorings_user
                ON crew_monitorings(user_id);
            CREATE INDEX IF NOT EXISTS idx_crew_monitoring_media
                ON crew_monitoring_media(crew_monitoring_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
            CREATE INDEX IF NOT EXISTS idx_addresses_city ON addresses(city);
            CREATE INDEX IF NOT EXISTS idx_addresses_manufacturer ON addresses(manufacturer);
        """)

        _apply_schema_migrations(conn)

        # Настройки по умолчанию (только если таблица пустая)
        defaults = {
            "photos_enabled": "1",
            "videos_enabled": "1",
            "photo_max_upload_bytes": str(10 * 1024 * 1024),
            "video_max_upload_bytes": str(100 * 1024 * 1024),
            "uploads_quota_bytes": "0",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


DEFAULT_SETTINGS = {
    "photos_enabled": "1",
    "videos_enabled": "1",
    "photo_max_upload_bytes": str(10 * 1024 * 1024),
    "video_max_upload_bytes": str(100 * 1024 * 1024),
    "uploads_quota_bytes": "0",
}


def _init_postgres_schema():
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'monitoring' AND table_name = 'settings'"
        ).fetchone()
        if not exists:
            raise RuntimeError(
                "PostgreSQL schema is missing. Run: alembic upgrade head"
            )

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO monitoring.settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO NOTHING",
                (key, value),
            )


def init_schema():
    if DB_BACKEND == "postgres":
        _init_postgres_schema()
    else:
        _init_sqlite_schema()


def get_setting(key: str, default: str = "") -> str:
    """Получить значение настройки"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """Установить значение настройки"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )


if __name__ == "__main__":
    init_schema()
    print(f"✓ База данных создана: {DB_PATH}")
    print(f"✓ Папка для медиа: {UPLOADS_DIR}")
