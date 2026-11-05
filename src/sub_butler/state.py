"""本地状态：记录已处理过的邮件和上次同步时间（SQLite）。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path


class State:
    def __init__(self, path: str | Path):
        p = Path(path)
        if str(p) != ":memory:":
            p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p))
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed (
                message_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL,
                outcome TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )

    def seen(self, message_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM processed WHERE message_id = ?", (message_id,)).fetchone() is not None

    def mark(self, message_id: str, outcome: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO processed VALUES (?, ?, ?)",
            (message_id, datetime.now(timezone.utc).isoformat(), outcome),
        )
        self.db.commit()

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    @property
    def last_sync(self) -> date | None:
        v = self.get("last_sync")
        return date.fromisoformat(v) if v else None

    @last_sync.setter
    def last_sync(self, d: date) -> None:
        self.set("last_sync", d.isoformat())
