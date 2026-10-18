"""Measure feed SQL counts/timing on a disposable synthetic SQLite database.

Run from app/: python benchmarks/benchmark_feeds.py --sizes 10 50 200 --repeats 5
This never selects the project database or uploads directory.
"""
from __future__ import annotations

import argparse
import os
import statistics
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="albion-feed-bench-")
TEMP_ROOT = Path(TEMP.name).resolve()
os.environ["DB_PATH"] = str(TEMP_ROOT / "bench.db")
os.environ["UPLOADS_DIR"] = str(TEMP_ROOT / "uploads")
os.environ["JWT_SECRET"] = "bench-only-random-secret-0123456789abcdef"

import sys
sys.path.insert(0, str(ROOT))
import app as application
from auth import create_token
from database import get_db
from fastapi.testclient import TestClient

queries: list[str] = []
_original_get_db = application.get_db


@contextmanager
def traced_get_db():
    with _original_get_db() as conn:
        conn.set_trace_callback(queries.append)
        yield conn


application.get_db = traced_get_db
client = TestClient(application.app)


def clear_fixture():
    with _original_get_db() as conn:
        for table in (
            "crew_report_comment_media", "crew_report_comments", "crew_monitoring_media",
            "crew_monitorings", "director_comments", "comments", "task_status_history",
            "media", "reports", "tasks", "auth_sessions", "addresses", "users",
        ):
            conn.execute(f'DELETE FROM "{table}"')


def seed_fixture(size: int):
    clear_fixture()
    with _original_get_db() as conn:
        conn.executemany(
            "INSERT INTO users(id, login, password_hash, role) VALUES (?, ?, ?, ?)",
            [(1, "bench-admin", "unused", "admin"), (2, "bench-service", "unused", "service"),
             (3, "bench-director", "unused", "director"), (4, "bench-crew", "unused", "crew"),
             (5, "bench-head", "unused", "head")],
        )
        conn.executemany(
            "INSERT INTO addresses(id, city, address_name, manufacturer, active) VALUES (?, ?, ?, ?, 1)",
            [(i, "Bench City", f"Address {i}", "Bench Manufacturer") for i in range(1, size + 1)],
        )
        for i in range(1, size + 1):
            conn.execute(
                "INSERT INTO tasks(id,address_id,created_by,created_by_role,description,status,workflow_status,is_important,hidden) "
                "VALUES (?, ?, 1, 'admin', ?, 'open', 'open', 0, 0)",
                (i, i, f"Task {i}"),
            )
            conn.execute(
                "INSERT INTO task_status_history(task_id,status,changed_by,comment) VALUES (?, 'open', 1, 'seed')",
                (i,),
            )
            conn.execute("INSERT INTO comments(task_id,user_id,text,media_json) VALUES (?,2,?, '[]')", (i, f"Chat {i}"))
            conn.execute(
                "INSERT INTO director_comments(item_type,item_id,user_id,text,media_json) VALUES ('task',?,3,?, '[]')",
                (i, f"Director {i}"),
            )
            conn.execute(
                "INSERT INTO reports(id,address_id,user_id,text,hidden) VALUES (?, ?, 2, ?, 0)",
                (i, i, f"Monitoring {i}"),
            )
            conn.execute(
                "INSERT INTO director_comments(item_type,item_id,report_id,user_id,text,media_json) "
                "VALUES ('monitoring',?,?,3,?, '[]')",
                (i, i, f"Monitoring comment {i}"),
            )
            conn.execute(
                "INSERT INTO crew_monitorings(id,address_id,user_id,text,hidden) VALUES (?, ?, 4, ?, 0)",
                (i, i, f"Crew report {i}"),
            )
            conn.execute(
                "INSERT INTO crew_report_comments(crew_monitoring_id,user_id,text) VALUES (?,5,?)",
                (i, f"Head comment {i}"),
            )
    tokens = {
        "admin": create_token(1, "bench-admin", "admin"),
        "director": create_token(3, "bench-director", "director"),
    }
    return tokens


def measure(endpoint: str, token: str, repeats: int):
    elapsed = []
    counts = []
    response_bytes = 0
    for _ in range(repeats):
        queries.clear()
        start = time.perf_counter()
        response = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
        elapsed.append(time.perf_counter() - start)
        if response.status_code != 200:
            raise RuntimeError(f"{endpoint} failed: {response.status_code} {response.text[:300]}")
        counts.append(len(queries))
        response_bytes = len(response.content)
    return {
        "endpoint": endpoint,
        "sql_count_median": statistics.median(counts),
        "elapsed_ms_median": round(statistics.median(elapsed) * 1000, 2),
        "response_bytes": response_bytes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(f"Disposable benchmark database: {TEMP_ROOT}")
    for size in args.sizes:
        tokens = seed_fixture(size)
        for endpoint, role in (("/api/feed", "admin"), ("/api/director/feed", "director")):
            result = measure(endpoint, tokens[role], args.repeats)
            print({"fixture_rows_per_type": size, **result})
    client.close()
    TEMP.cleanup()


if __name__ == "__main__":
    main()
