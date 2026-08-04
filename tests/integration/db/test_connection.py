from __future__ import annotations

import sqlite3

import pytest

from src.infrastructure.db import connection as connection_module


def test_connect_closes_opened_connection_when_pragma_initialization_fails(
    monkeypatch,
) -> None:
    """Catches a post-open PRAGMA error leaking the SQLite connection handle."""

    class FailingConnection:
        row_factory = None
        closed = False

        def execute(self, statement):
            raise sqlite3.OperationalError(f"pragma failed: {statement}")

        def close(self):
            self.closed = True

    opened = FailingConnection()
    monkeypatch.setattr(connection_module.sqlite3, "connect", lambda path: opened)

    with pytest.raises(sqlite3.OperationalError, match="pragma failed"):
        connection_module.connect("ignored.db")

    assert opened.closed is True
