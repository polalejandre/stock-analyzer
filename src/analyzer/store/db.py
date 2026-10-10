"""SQLite connection, numbered SQL migrations and small write helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migration_files() -> list[tuple[int, str, str]]:
    folder = resources.files("analyzer.store") / "migrations"
    found = []
    for entry in folder.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.name, entry.read_text()))
    return sorted(found)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply migrations not yet recorded in schema_migrations. Each runs in one transaction."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = 0
    for version, name, sql in _migration_files():
        if version in done:
            continue
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        conn.executescript(
            f"BEGIN;\n{sql}\n"
            f"INSERT INTO schema_migrations VALUES ({version}, '{name}', '{stamp}');\nCOMMIT;"
        )
        applied += 1
    return applied


def upsert_rows(
    conn: sqlite3.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence]
) -> None:
    placeholders = ", ".join("?" * len(columns))
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    with conn:
        conn.executemany(sql, rows)


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    upsert_rows(conn, "sync_state", ["key", "value"], [(key, value)])
