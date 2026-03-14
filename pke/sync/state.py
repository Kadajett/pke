"""SQLite-based sync state tracking for incremental ingestion."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pke.config import settings


class SyncState:
    """Track sync cursors per source for incremental ingestion."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.sync_db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    source_type TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    cursor_value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source_type, source_key)
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def get_cursor(self, source_type: str, source_key: str) -> str | None:
        """Get the last sync cursor for a source."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cursor_value FROM sync_state WHERE source_type = ? AND source_key = ?",
                (source_type, source_key),
            ).fetchone()
            return row[0] if row else None

    def set_cursor(self, source_type: str, source_key: str, cursor_value: str) -> None:
        """Set the sync cursor for a source."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sync_state (source_type, source_key, cursor_value, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(source_type, source_key)
                   DO UPDATE SET cursor_value = excluded.cursor_value,
                                 updated_at = excluded.updated_at""",
                (source_type, source_key, cursor_value),
            )

    def get_all(self, source_type: str) -> dict[str, str]:
        """Get all cursors for a source type."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source_key, cursor_value FROM sync_state WHERE source_type = ?",
                (source_type,),
            ).fetchall()
            return dict(rows)

    def delete(self, source_type: str, source_key: str) -> None:
        """Delete a sync cursor."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sync_state WHERE source_type = ? AND source_key = ?",
                (source_type, source_key),
            )

    def clear(self, source_type: str) -> None:
        """Clear all cursors for a source type."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sync_state WHERE source_type = ?",
                (source_type,),
            )
