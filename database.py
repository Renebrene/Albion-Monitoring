import sqlite3
from contextlib import contextmanager
from pathlib import Path
import os

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "data" / "monitoring.db"))
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", Path(__file__).parent.parent / "uploads"))

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db():
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


def init_schema():

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
