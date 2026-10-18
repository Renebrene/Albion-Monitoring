"""Dry-run-first cleanup for expired, unreferenced staged upload files.

Usage requires explicit --db and --uploads paths. No files are removed unless
--apply is supplied. This tool deliberately does not initialize/migrate schema.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

REQUIRED_COLUMNS = {
    "media": {"filename"},
    "crew_monitoring_media": {"filename"},
    "crew_report_comment_media": {"filename"},
    "comments": {"media_json"},
    "director_comments": {"media_json"},
}
TEMP_PREFIX = "temp_"
LOCK_NAME = ".quota.lock"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _collect_json_filenames(value: object, references: set[str]) -> None:
    if value is None:
        return
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Invalid media_json; refusing cleanup") from exc

    def visit(item):
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            candidate = item.get("filename") or item.get("url")
        else:
            candidate = None
        if not isinstance(candidate, str):
            if isinstance(item, list):
                for nested in item:
                    visit(nested)
            elif isinstance(item, dict):
                for nested in item.values():
                    visit(nested)
            return
        parsed = urlparse(candidate)
        path = unquote(parsed.path)
        if path.startswith("/api/media/"):
            candidate = path[len("/api/media/"):]
        elif path.startswith("/uploads/"):
            candidate = path[len("/uploads/"):]
        else:
            candidate = Path(path).name
        if candidate and Path(candidate).name == candidate and "/" not in candidate and "\\" not in candidate:
            references.add(candidate)

    visit(data)


def referenced_filenames(db_path: Path) -> set[str]:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(REQUIRED_COLUMNS) - tables
        if missing:
            raise RuntimeError(f"Missing reference tables {sorted(missing)}; refusing cleanup")
        for table, expected in REQUIRED_COLUMNS.items():
            if not expected.issubset(_columns(conn, table)):
                raise RuntimeError(f"Missing columns in {table}; refusing cleanup")

        references: set[str] = set()
        for table in ("media", "crew_monitoring_media", "crew_report_comment_media"):
            references.update(row[0] for row in conn.execute(f'SELECT filename FROM "{table}"') if row[0])
        for table in ("comments", "director_comments"):
            for row in conn.execute(f'SELECT media_json FROM "{table}"'):
                _collect_json_filenames(row[0], references)
        return references
    finally:
        conn.close()


def cleanup_candidates(db_path: Path, uploads_dir: Path, *, older_than_days: int = 7, now: float | None = None):
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least one day")
    root = uploads_dir.resolve(strict=True)
    references = referenced_filenames(db_path)
    cutoff = (time.time() if now is None else now) - older_than_days * 24 * 60 * 60
    candidates = []
    for entry in root.iterdir():
        try:
            if entry.name != Path(entry.name).name or not entry.name.startswith(TEMP_PREFIX):
                continue
            if entry.is_symlink() or not entry.is_file() or entry.resolve(strict=True).parent != root:
                continue
            stat = entry.stat(follow_symlinks=False)
            if stat.st_mtime >= cutoff or entry.name in references:
                continue
            candidates.append({"path": entry, "size": stat.st_size, "mtime": stat.st_mtime})
        except OSError:
            continue
    return candidates


def apply_cleanup(db_path: Path, uploads_dir: Path, candidates, *, older_than_days: int = 7, now: float | None = None):
    lock_path = uploads_dir / LOCK_NAME
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        # Re-scan all DB references while holding the upload admission lock. Do
        # not delete anything if metadata is incomplete or malformed.
        references = referenced_filenames(db_path)
        cutoff = (time.time() if now is None else now) - older_than_days * 24 * 60 * 60
        removed = []
        root = uploads_dir.resolve(strict=True)
        for candidate in candidates:
            path = candidate["path"]
            try:
                if path.parent.resolve() != root or path.is_symlink() or not path.is_file():
                    continue
                if path.name in references or path.stat(follow_symlinks=False).st_mtime >= cutoff:
                    continue
                path.unlink()
                removed.append(path.name)
            except OSError:
                continue
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run-first cleanup of expired unreferenced temp uploads")
    parser.add_argument("--db", required=True, type=Path, help="SQLite DB file (explicit path required)")
    parser.add_argument("--uploads", required=True, type=Path, help="Uploads directory (explicit path required)")
    parser.add_argument("--older-than-days", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="Delete the displayed eligible files")
    args = parser.parse_args(argv)
    candidates = cleanup_candidates(args.db, args.uploads, older_than_days=args.older_than_days)
    total = sum(row["size"] for row in candidates)
    print(f"eligible={len(candidates)} bytes={total} mode={'apply' if args.apply else 'dry-run'}")
    for row in candidates:
        print(f"{row['path'].name}\t{row['size']} bytes")
    if args.apply:
        removed = apply_cleanup(args.db, args.uploads, candidates, older_than_days=args.older_than_days)
        print(f"removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
