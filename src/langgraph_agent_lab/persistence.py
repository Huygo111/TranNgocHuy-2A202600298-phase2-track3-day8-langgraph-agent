"""Checkpointer adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _sqlite_path(database_url: str | None) -> str:
    if not database_url:
        return "checkpoints.db"
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    return database_url


def _build_sqlite_checkpointer(database_url: str | None) -> object:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        message = "SQLite checkpointer requires: pip install -e '.[sqlite]'"
        raise RuntimeError(message) from exc

    db_path = _sqlite_path(database_url)
    if db_path not in {":memory:", ""}:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return SqliteSaver(conn=conn)


def _build_postgres_checkpointer(database_url: str | None) -> object:
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        message = "Postgres checkpointer requires: pip install -e '.[postgres]'"
        raise RuntimeError(message) from exc

    if not database_url:
        raise ValueError("Postgres checkpointer requires a database_url")
    return PostgresSaver.from_conn_string(database_url)


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> object | None:
    """Return a LangGraph checkpointer for the configured persistence backend."""
    normalized_kind = kind.strip().lower()
    if normalized_kind == "none":
        return None
    if normalized_kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if normalized_kind == "sqlite":
        return _build_sqlite_checkpointer(database_url)
    if normalized_kind == "postgres":
        return _build_postgres_checkpointer(database_url)
    raise ValueError(f"Unknown checkpointer kind: {kind}")
