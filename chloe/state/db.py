import sqlite3
from pathlib import Path

_connection: sqlite3.Connection | None = None


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    global _connection
    if _connection is None:
        path = db_path or Path("chloe.db")
        _connection = sqlite3.connect(str(path), check_same_thread=False)
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        _connection.execute("PRAGMA synchronous=NORMAL")
        _connection.row_factory = sqlite3.Row
    return _connection


def migrate(db_path: Path | None = None, migrations_dir: Path | None = None) -> int:
    conn = get_connection(db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            filename   TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    mdir = migrations_dir or Path(__file__).parent / "migrations"
    sql_files = sorted(mdir.glob("*.sql"))

    applied = 0
    for f in sql_files:
        row = conn.execute(
            "SELECT 1 FROM _migrations WHERE filename = ?", (f.name,)
        ).fetchone()
        if row:
            continue

        sql = f.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (f.name,))
        conn.commit()
        applied += 1

    return applied


def close() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
