"""Narrative store — prose observations from the witness pass and weekly consolidation.

Entries are indexed in the 'narrative' Chroma collection for semantic retrieval.
The DB is authoritative; Chroma is the search index.
"""
from __future__ import annotations

from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("memory.narrative_store")


def add_entry(text: str, source: str = "witness", salience: float = 0.5) -> int:
    """Store a narrative entry and index it in Chroma. Returns the new row id."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO narrative_entries (source, text, salience) VALUES (?, ?, ?)",
        (source, text, salience),
    )
    entry_id = cur.lastrowid
    conn.commit()
    _index_entry(entry_id, text, source)
    return entry_id


def query(q: str, n: int = 3) -> list[str]:
    """Semantic query over non-archived narrative entries. Returns text strings."""
    if not q or not q.strip():
        return []
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection("narrative")
        total = collection.count()
        if total == 0:
            return []
        resp = collection.query(
            query_texts=[q],
            n_results=min(n, total),
            include=["documents"],
        )
        return resp.get("documents", [[]])[0]
    except Exception as exc:
        log.warning("narrative_query_failed", error=str(exc))
        return []


def get_recent(n: int = 5, source: str | None = None) -> list[str]:
    """Return most recent non-archived entries, optionally filtered by source."""
    conn = get_connection()
    if source:
        rows = conn.execute(
            "SELECT text FROM narrative_entries WHERE archived=0 AND source=? ORDER BY id DESC LIMIT ?",
            (source, n),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT text FROM narrative_entries WHERE archived=0 ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [r["text"] for r in rows]


def collect_for_consolidation(window: int = 25) -> tuple[list[int], list[str]]:
    """
    Return IDs and texts of the last `window` unarchived witness entries.
    Returns empty if fewer than 5 exist (not worth consolidating).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, text FROM narrative_entries WHERE source='witness' AND archived=0 "
        "ORDER BY id DESC LIMIT ?",
        (window,),
    ).fetchall()
    if len(rows) < 5:
        return [], []
    ids = [r["id"] for r in rows]
    texts = [r["text"] for r in reversed(rows)]
    return ids, texts


def archive_entries(ids: list[int]) -> None:
    """Mark entries as archived (after consolidation)."""
    if not ids:
        return
    conn = get_connection()
    conn.execute(
        f"UPDATE narrative_entries SET archived=1 WHERE id IN ({','.join('?'*len(ids))})",
        ids,
    )
    conn.commit()
    log.info("narrative_entries_archived", count=len(ids))


def _index_entry(entry_id: int, text: str, source: str) -> None:
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection("narrative")
        collection.upsert(
            ids=[f"n{entry_id}"],
            documents=[text],
            metadatas=[{"source": source}],
        )
    except Exception as exc:
        log.warning("narrative_index_failed", entry_id=entry_id, error=str(exc))
