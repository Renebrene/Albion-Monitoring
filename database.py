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


def init_schema():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','service','director')),
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
                text TEXT NOT NULL
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
                text TEXT NOT NULL
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
                closed_by INTEGER REFERENCES users(id)
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

        # Исходные имена загруженных файлов.
        # Старые записи остаются без имени, новые получают его при загрузке.
        for table in (
            "media",
            "crew_monitoring_media",
            "crew_report_comment_media",
        ):
            columns = {
                row["name"]
                for row in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if "original_filename" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN original_filename TEXT"
                )

        # Медиа в комментариях директора/админа.
        # Таблица director_comments могла быть создана предыдущей
        # версией приложения, поэтому мигрируем безопасно.
        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(director_comments)"
                ).fetchall()
            }
            if "media_json" not in columns:
                conn.execute(
                    "ALTER TABLE director_comments "
                    "ADD COLUMN media_json TEXT"
                )

            if "report_id" not in columns:
                conn.execute(
                    "ALTER TABLE director_comments "
                    "ADD COLUMN report_id INTEGER REFERENCES reports(id)"
                )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dc_report "
                "ON director_comments(report_id)"
            )
        except Exception:
            # Если таблицы ещё нет в конкретной старой базе,
            # ничего не ломаем — существующая логика останется.
            pass

        # Настройки по умолчанию (только если таблица пустая)
        defaults = {
            "photos_enabled": "1",
            "videos_enabled": "1",
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
