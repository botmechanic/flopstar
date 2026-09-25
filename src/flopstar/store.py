"""Append-only SQLite store for technocore messages."""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MessageStore:
    """Append-only SQLite store for messages, keyed by room:seq."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Initialize the database schema."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                room TEXT NOT NULL,
                seq INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (room, seq)
            )
        """)

        # Index for querying by room
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_room
            ON messages(room, seq)
        """)

        self.conn.commit()

    def store_message(self, room: str, seq: int, raw_record: dict[str, Any]):
        """
        Store a message exactly as received.

        Args:
            room: Room name
            seq: Sequence number
            raw_record: The raw message record from technocore
        """
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO messages (room, seq, raw_json) VALUES (?, ?, ?)",
                (room, seq, json.dumps(raw_record))
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            # Already exists, which is fine (append-only)
            pass

    def store_messages(self, room: str, messages: list[dict[str, Any]]):
        """Store multiple messages from a room."""
        for msg in messages:
            seq = msg.get("seq")
            if seq is not None:
                self.store_message(room, seq, msg)

    def get_latest_seq(self, room: str) -> int | None:
        """Get the highest sequence number stored for a room."""
        cursor = self.conn.execute(
            "SELECT MAX(seq) as max_seq FROM messages WHERE room = ?",
            (room,)
        )
        result = cursor.fetchone()
        return result["max_seq"] if result and result["max_seq"] is not None else None

    def get_message(self, room: str, seq: int) -> dict[str, Any] | None:
        """Retrieve a specific message."""
        cursor = self.conn.execute(
            "SELECT raw_json FROM messages WHERE room = ? AND seq = ?",
            (room, seq)
        )
        result = cursor.fetchone()
        if result:
            return json.loads(result["raw_json"])
        return None

    def get_messages(
        self,
        room: str,
        since: int | None = None,
        limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve messages from a room."""
        query = "SELECT raw_json FROM messages WHERE room = ?"
        params = [room]

        if since is not None:
            query += " AND seq > ?"
            params.append(since)

        query += " ORDER BY seq ASC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.execute(query, params)
        return [json.loads(row["raw_json"]) for row in cursor.fetchall()]

    def has_gap(self, room: str, expected_next: int, first_new: int) -> bool:
        """Check if there's a gap in the sequence."""
        return first_new > expected_next

    def close(self):
        """Close the database connection."""
        self.conn.close()
