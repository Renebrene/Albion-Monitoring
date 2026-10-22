"""Перенос данных из SQLite (текущая схема) в PostgreSQL (core + monitoring).

Скрипт работает только на чтение SQLite и пишет в PostgreSQL в одной транзакции.
Перед запуском: `alembic upgrade head`.

Примеры:
    python migrate_sqlite_to_pg.py --dry-run
    python migrate_sqlite_to_pg.py --truncate
    python migrate_sqlite_to_pg.py --verify-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb


DEFAULT_SQLITE = Path(
    os.environ.get("DB_PATH", Path(__file__).parent.parent / "data" / "monitoring.db")
)
DEFAULT_LEGAL_ENTITY = os.environ.get("LEGAL_ENTITY_NAME", "Текущая компания")

# SQLite table -> (PG table, columns)
COPY_TABLES = {
    "users": (
        "core.users",
        ["id", "login", "password_hash", "role", "created_at"],
    ),
    "auth_sessions": (
        "core.sessions",
        ["id", "user_id", "login", "role", "login_at", "logout_at"],
    ),
    "addresses": (
        "monitoring.addresses",
        [
            "id", "city", "address_name", "manufacturer",
            "filter1", "filter2", "filter3", "active", "updated_at",
        ],
    ),
    "tasks": (
        "monitoring.tasks",
        [
            "id", "address_id", "created_by", "created_by_role",
            "description", "status", "workflow_status", "is_important",
            "close_reason", "created_at", "closed_at", "closed_by", "hidden",
        ],
    ),
    "reports": (
        "monitoring.reports",
        ["id", "address_id", "user_id", "task_id", "created_at", "text", "hidden"],
    ),
    "media": (
        "monitoring.media",
        [
            "id", "report_id", "task_id", "filename", "media_type",
            "created_at", "original_filename",
        ],
    ),
    "crew_monitorings": (
        "monitoring.crew_monitorings",
        ["id", "address_id", "user_id", "created_at", "text", "hidden"],
    ),
    "crew_monitoring_media": (
        "monitoring.crew_monitoring_media",
        ["id", "crew_monitoring_id", "filename", "media_type", "created_at", "original_filename"],
    ),
    "crew_report_comments": (
        "monitoring.crew_report_comments",
        ["id", "crew_monitoring_id", "user_id", "text", "created_at"],
    ),
    "crew_report_comment_media": (
        "monitoring.crew_report_comment_media",
        ["id", "comment_id", "filename", "media_type", "created_at", "original_filename"],
    ),
    "comments": (
        "monitoring.comments",
        ["id", "task_id", "user_id", "text", "media_json", "created_at"],
    ),
    "director_comments": (
        "monitoring.director_comments",
        ["id", "item_type", "item_id", "report_id", "user_id", "text", "media_json", "created_at"],
    ),
    "task_status_history": (
        "monitoring.task_status_history",
        ["id", "task_id", "status", "changed_by", "changed_at", "comment"],
    ),
    "settings": (
        "monitoring.settings",
        ["key", "value", "updated_at"],
    ),
}

TIMESTAMP_COLUMNS = {
    "created_at", "updated_at", "login_at", "logout_at",
    "closed_at", "changed_at",
}

JSON_COLUMNS = {"media_json"}
BOOL_COLUMNS = {"hidden", "active", "shared", "is_important"}

# Columns to compute a deterministic checksum for.
CHECKSUM_COLUMNS = {
    "users": ["id", "login", "role", "created_at"],
    "auth_sessions": ["id", "user_id", "login", "role", "login_at", "logout_at"],
    "addresses": ["id", "city", "address_name", "manufacturer", "active"],
    "tasks": ["id", "address_id", "created_by", "description", "status"],
    "reports": ["id", "address_id", "user_id", "task_id", "text", "hidden"],
    "media": ["id", "report_id", "task_id", "filename", "media_type"],
    "crew_monitorings": ["id", "address_id", "user_id", "text", "hidden"],
    "crew_monitoring_media": ["id", "crew_monitoring_id", "filename", "media_type"],
    "crew_report_comments": ["id", "crew_monitoring_id", "user_id", "text", "created_at"],
    "crew_report_comment_media": ["id", "comment_id", "filename", "media_type"],
    "comments": ["id", "task_id", "user_id", "text", "created_at"],
    "director_comments": ["id", "item_type", "item_id", "report_id", "user_id", "text", "created_at"],
    "task_status_history": ["id", "task_id", "status", "changed_by", "changed_at", "comment"],
    "settings": ["key", "value"],
}


def psycopg_url(database_url: str) -> str:
    """Accept the SQLAlchemy-style URL used by the app and Alembic."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def parse_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_value(column: str, value):
    if column in TIMESTAMP_COLUMNS:
        return parse_timestamp(value)
    if column in JSON_COLUMNS and value not in (None, ""):
        if isinstance(value, str):
            try:
                return Jsonb(json.loads(value))
            except json.JSONDecodeError:
                return Jsonb([])
        return Jsonb(value)
    if column in BOOL_COLUMNS:
        return 1 if bool(value) else 0
    return value


def normalize_for_checksum(column: str, value):
    if column in BOOL_COLUMNS:
        return "1" if bool(value) else "0"
    if column in TIMESTAMP_COLUMNS:
        parsed = parse_timestamp(value)
        return parsed.astimezone(timezone.utc).isoformat() if parsed else None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def table_checksum(rows, columns):
    payload = []
    for row in rows:
        payload.append([
            normalize_for_checksum(column, row.get(column)) for column in columns
        ])
    payload.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.md5(encoded).hexdigest()


def sqlite_rows(conn, table, columns):
    present = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not present:
        return []

    existing = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    selected = [column for column in columns if column in existing]
    if not selected:
        return []

    order = "id" if "id" in existing else selected[0]
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM {table} ORDER BY {order}"
    ).fetchall()
    return [dict(zip(selected, row)) for row in rows]


def truncate_target(conn):
    tables = [
        "monitoring.crew_report_comment_media",
        "monitoring.crew_report_comments",
        "monitoring.crew_monitoring_media",
        "monitoring.crew_monitorings",
        "monitoring.director_comments",
        "monitoring.comments",
        "monitoring.task_status_history",
        "monitoring.media",
        "monitoring.reports",
        "monitoring.tasks",
        "monitoring.addresses",
        "monitoring.settings",
        "core.user_roles",
        "core.sessions",
        "core.user_entities",
        "core.users",
        "core.legal_entities",
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")


def ensure_schema(conn):
    exists = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'monitoring'"
    ).fetchone()
    if not exists:
        raise RuntimeError("Схема не найдена. Сначала выполните: alembic upgrade head")


def existing_counts(conn):
    counts = {}
    for table in (
        "core.users", "monitoring.addresses", "monitoring.tasks",
        "monitoring.reports", "monitoring.media", "monitoring.settings",
    ):
        counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def migrate(sqlite_path: Path, database_url: str, legal_entity_name: str,
            dry_run: bool = False, truncate: bool = False,
            verify_only: bool = False) -> dict:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite не найден: {sqlite_path}")

    sqlite_conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    sqlite_conn.row_factory = sqlite3.Row

    report = {"sqlite": str(sqlite_path), "tables": {}, "checksums": {}}

    with psycopg.connect(psycopg_url(database_url)) as conn:
        ensure_schema(conn)

        if verify_only:
            return verify(conn, sqlite_conn, report)

        if truncate and not dry_run:
            truncate_target(conn)

        counts = existing_counts(conn)
        non_empty = {table: count for table, count in counts.items() if count}
        if non_empty and not truncate:
            raise RuntimeError(
                f"Целевые таблицы не пусты: {non_empty}. "
                "Используйте --truncate или --verify-only."
            )

        if not dry_run:
            with conn.transaction():
                migrate_core(conn, sqlite_conn, legal_entity_name)
                migrate_monitoring(conn, sqlite_conn)
                reset_sequences(conn)

        return verify(conn, sqlite_conn, report, dry_run=dry_run)


def migrate_core(conn, sqlite_conn, legal_entity_name: str) -> None:
    conn.execute(
        "INSERT INTO core.legal_entities (id, name) VALUES (%s, %s)",
        (1, legal_entity_name),
    )

    for row in sqlite_rows(sqlite_conn, "users", COPY_TABLES["users"][1]):
        conn.execute(
            """
            INSERT INTO core.users
                (id, login, password_hash, role, legal_entity_id, created_at)
            VALUES (%s, %s, %s, %s, 1, %s)
            """,
            (
                row["id"],
                row["login"],
                row["password_hash"],
                row["role"],
                normalize_value("created_at", row.get("created_at")),
            ),
        )
        # is_default — smallint-флаг (0/1), как остальные флаги приложения.
        conn.execute(
            """
            INSERT INTO core.user_entities
                (user_id, legal_entity_id, role, is_default)
            VALUES (%s, 1, %s, 1)
            """,
            (row["id"], row["role"]),
        )

    for row in sqlite_rows(sqlite_conn, "auth_sessions", COPY_TABLES["auth_sessions"][1]):
        conn.execute(
            """
            INSERT INTO core.sessions
                (id, user_id, login, role, legal_entity_id, login_at, logout_at)
            VALUES (%s, %s, %s, %s, 1, %s, %s)
            """,
            (
                row["id"],
                row["user_id"],
                row["login"],
                row["role"],
                normalize_value("login_at", row.get("login_at")),
                normalize_value("logout_at", row.get("logout_at")),
            ),
        )


def migrate_monitoring(conn, sqlite_conn) -> None:
    address_columns = COPY_TABLES["addresses"][1]
    for row in sqlite_rows(sqlite_conn, "addresses", address_columns):
        conn.execute(
            """
            INSERT INTO monitoring.addresses
                (id, legal_entity_id, shared, city, address_name, manufacturer,
                 filter1, filter2, filter3, active, updated_at)
            VALUES (%s, 1, 1, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["city"],
                row["address_name"],
                row.get("manufacturer"),
                row.get("filter1"),
                row.get("filter2"),
                row.get("filter3"),
                normalize_value("active", row.get("active", 1)),
                normalize_value("updated_at", row.get("updated_at")),
            ),
        )

    task_columns = COPY_TABLES["tasks"][1]
    for row in sqlite_rows(sqlite_conn, "tasks", task_columns):
        workflow_status = row.get("workflow_status") or (
            "completed" if row.get("status") == "done" else "open"
        )
        conn.execute(
            """
            INSERT INTO monitoring.tasks
                (id, legal_entity_id, address_id, created_by, created_by_role,
                 description, status, workflow_status, is_important, close_reason,
                 created_at, closed_at, closed_by, hidden)
            VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["address_id"],
                row["created_by"],
                row.get("created_by_role"),
                row["description"],
                row.get("status") or "open",
                workflow_status,
                normalize_value("is_important", row.get("is_important", 0)),
                row.get("close_reason"),
                normalize_value("created_at", row.get("created_at")),
                normalize_value("closed_at", row.get("closed_at")),
                row.get("closed_by"),
                normalize_value("hidden", row.get("hidden", 0)),
            ),
        )

    report_columns = COPY_TABLES["reports"][1]
    for row in sqlite_rows(sqlite_conn, "reports", report_columns):
        conn.execute(
            """
            INSERT INTO monitoring.reports
                (id, legal_entity_id, address_id, user_id, task_id,
                 created_at, text, hidden)
            VALUES (%s, 1, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["address_id"],
                row["user_id"],
                row.get("task_id"),
                normalize_value("created_at", row.get("created_at")),
                row["text"],
                normalize_value("hidden", row.get("hidden", 0)),
            ),
        )

    media_columns = COPY_TABLES["media"][1]
    for row in sqlite_rows(sqlite_conn, "media", media_columns):
        conn.execute(
            """
            INSERT INTO monitoring.media
                (id, legal_entity_id, report_id, task_id, filename,
                 original_filename, media_type, storage, created_at)
            VALUES (%s, 1, %s, %s, %s, %s, %s, 'local', %s)
            """,
            (
                row["id"],
                row.get("report_id"),
                row.get("task_id"),
                row["filename"],
                row.get("original_filename"),
                row["media_type"],
                normalize_value("created_at", row.get("created_at")),
            ),
        )

    crew_columns = COPY_TABLES["crew_monitorings"][1]
    for row in sqlite_rows(sqlite_conn, "crew_monitorings", crew_columns):
        conn.execute(
            """
            INSERT INTO monitoring.crew_monitorings
                (id, legal_entity_id, address_id, user_id, created_at, text, hidden)
            VALUES (%s, 1, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["address_id"],
                row["user_id"],
                normalize_value("created_at", row.get("created_at")),
                row["text"],
                normalize_value("hidden", row.get("hidden", 0)),
            ),
        )

    crew_media_columns = COPY_TABLES["crew_monitoring_media"][1]
    for row in sqlite_rows(sqlite_conn, "crew_monitoring_media", crew_media_columns):
        conn.execute(
            """
            INSERT INTO monitoring.crew_monitoring_media
                (id, legal_entity_id, crew_monitoring_id, filename,
                 original_filename, media_type, created_at)
            VALUES (%s, 1, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["crew_monitoring_id"],
                row["filename"],
                row.get("original_filename"),
                row["media_type"],
                normalize_value("created_at", row.get("created_at")),
            ),
        )

    crew_comment_columns = COPY_TABLES["crew_report_comments"][1]
    for row in sqlite_rows(sqlite_conn, "crew_report_comments", crew_comment_columns):
        conn.execute(
            """
            INSERT INTO monitoring.crew_report_comments
                (id, legal_entity_id, crew_monitoring_id, user_id, text, created_at)
            VALUES (%s, 1, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["crew_monitoring_id"],
                row["user_id"],
                row["text"],
                normalize_value("created_at", row.get("created_at")),
            ),
        )

    crew_comment_media_columns = COPY_TABLES["crew_report_comment_media"][1]
    for row in sqlite_rows(sqlite_conn, "crew_report_comment_media", crew_comment_media_columns):
        conn.execute(
            """
            INSERT INTO monitoring.crew_report_comment_media
                (id, legal_entity_id, comment_id, filename,
                 original_filename, media_type, created_at)
            VALUES (%s, 1, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["comment_id"],
                row["filename"],
                row.get("original_filename"),
                row["media_type"],
                normalize_value("created_at", row.get("created_at")),
            ),
        )

    comment_columns = COPY_TABLES["comments"][1]
    for row in sqlite_rows(sqlite_conn, "comments", comment_columns):
        conn.execute(
            """
            INSERT INTO monitoring.comments
                (id, legal_entity_id, task_id, user_id, text, media_json, created_at)
            VALUES (%s, 1, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["task_id"],
                row["user_id"],
                row["text"],
                normalize_value("media_json", row.get("media_json")),
                normalize_value("created_at", row.get("created_at")),
            ),
        )

    director_columns = COPY_TABLES["director_comments"][1]
    for row in sqlite_rows(sqlite_conn, "director_comments", director_columns):
        conn.execute(
            """
            INSERT INTO monitoring.director_comments
                (id, legal_entity_id, item_type, item_id, report_id, user_id,
                 text, media_json, created_at)
            VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["item_type"],
                row["item_id"],
                row.get("report_id"),
                row["user_id"],
                row["text"],
                normalize_value("media_json", row.get("media_json")),
                normalize_value("created_at", row.get("created_at")),
            ),
        )

    history_columns = COPY_TABLES["task_status_history"][1]
    for row in sqlite_rows(sqlite_conn, "task_status_history", history_columns):
        conn.execute(
            """
            INSERT INTO monitoring.task_status_history
                (id, legal_entity_id, task_id, status, changed_by, changed_at, comment)
            VALUES (%s, 1, %s, %s, %s, %s, %s)
            """,
            (
                row["id"],
                row["task_id"],
                row["status"],
                row["changed_by"],
                normalize_value("changed_at", row.get("changed_at")),
                row.get("comment"),
            ),
        )

    settings_columns = COPY_TABLES["settings"][1]
    for row in sqlite_rows(sqlite_conn, "settings", settings_columns):
        conn.execute(
            """
            INSERT INTO monitoring.settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (
                row["key"],
                row["value"],
                normalize_value("updated_at", row.get("updated_at")),
            ),
        )


SEQUENCES = [
    "core.legal_entities",
    "core.users",
    "core.sessions",
    "core.roles",
    "core.capabilities",
    "core.role_scopes",
    "core.user_roles",
    "core.audit_log",
    "core.domains",
    "core.task_types",
    "core.workflow_definitions",
    "monitoring.addresses",
    "monitoring.tasks",
    "monitoring.reports",
    "monitoring.media",
    "monitoring.task_status_history",
    "monitoring.comments",
    "monitoring.director_comments",
    "monitoring.crew_monitorings",
    "monitoring.crew_monitoring_media",
    "monitoring.crew_report_comments",
    "monitoring.crew_report_comment_media",
]


def reset_sequences(conn) -> None:
    for table in SEQUENCES:
        conn.execute(
            """
            SELECT setval(
                pg_get_serial_sequence(%s, 'id'),
                GREATEST(COALESCE(MAX(id), 0), 1),
                COALESCE(MAX(id), 0) > 0
            )
            FROM {}
            """.format(table),
            (table,),
        )


def verify(conn, sqlite_conn, report: dict, dry_run: bool = False) -> dict:
    for source_table, (pg_table, columns) in COPY_TABLES.items():
        sqlite_data = sqlite_rows(sqlite_conn, source_table, columns)
        sqlite_count = len(sqlite_data)

        if dry_run:
            pg_count = conn.execute(f"SELECT count(*) FROM {pg_table}").fetchone()[0]
            report["tables"][source_table] = {
                "sqlite": sqlite_count, "postgres": pg_count, "dry_run": True,
            }
            continue

        pg_count = conn.execute(f"SELECT count(*) FROM {pg_table}").fetchone()[0]
        report["tables"][source_table] = {
            "sqlite": sqlite_count, "postgres": pg_count,
            "match": sqlite_count == pg_count,
        }

        checksum_columns = CHECKSUM_COLUMNS.get(source_table)
        if not checksum_columns or sqlite_count == 0:
            continue

        order_column = "id" if "id" in checksum_columns else checksum_columns[0]
        pg_rows = [
            dict(zip(checksum_columns, row))
            for row in conn.execute(
                f"SELECT {', '.join(checksum_columns)} FROM {pg_table} ORDER BY {order_column}"
            ).fetchall()
        ]
        source_rows = sqlite_rows(sqlite_conn, source_table, checksum_columns)
        source_checksum = table_checksum(source_rows, checksum_columns)
        target_checksum = table_checksum(pg_rows, checksum_columns)
        report["checksums"][source_table] = {
            "sqlite": source_checksum,
            "postgres": target_checksum,
            "match": source_checksum == target_checksum,
        }

    mismatches = [
        name for name, result in report["tables"].items()
        if result.get("match") is False
    ]
    checksum_mismatches = [
        name for name, result in report["checksums"].items()
        if result.get("match") is False
    ]
    report["ok"] = not mismatches and not checksum_mismatches
    report["mismatches"] = mismatches
    report["checksum_mismatches"] = checksum_mismatches
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--legal-entity-name", default=DEFAULT_LEGAL_ENTITY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--truncate", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if not args.database_url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 2

    report = migrate(
        args.sqlite,
        args.database_url,
        args.legal_entity_name,
        dry_run=args.dry_run,
        truncate=args.truncate,
        verify_only=args.verify_only,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
