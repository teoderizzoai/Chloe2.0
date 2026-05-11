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
        try:
            conn.executescript(sql)
        except Exception:
            try:
                from chloe.observability.metrics import chloe_db_migration_failures_total
                chloe_db_migration_failures_total.inc()
            except Exception:
                pass
            raise
        conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (f.name,))
        conn.commit()
        applied += 1

    return applied


def seed_primary_persons(conn: "sqlite3.Connection | None" = None) -> None:
    """Ensure Teo is seeded as a primary-class person with gen_level=3.

    Safe to call multiple times — only updates if the columns exist (i.e., after
    migration 0015_social_graph.sql) and the values differ.
    """
    c = conn or get_connection()
    try:
        # Check whether the social-graph columns exist yet
        c.execute("SELECT relationship_class FROM persons LIMIT 0")
    except Exception:
        return  # migration not yet applied

    row = c.execute("SELECT id FROM persons WHERE id=1").fetchone()
    if row:
        c.execute(
            "UPDATE persons SET relationship_class='primary', gen_level=3 "
            "WHERE id=1 AND (relationship_class != 'primary' OR gen_level < 3)"
        )
    else:
        try:
            c.execute(
                "INSERT OR IGNORE INTO persons "
                "(id, name, is_active, attachment_depth, relationship_class, gen_level) "
                "VALUES (1, 'Teo', 1, 0.9, 'primary', 3)"
            )
        except Exception:
            pass
    c.commit()


def close() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
