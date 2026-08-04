from __future__ import annotations

import sqlite3
from contextlib import suppress
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with the documented durability settings."""
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except BaseException:
        with suppress(Exception):
            connection.close()
        raise
