"""Which user created each run (thread_id).

A small table next to the LangGraph checkpoints (same SQLite file in
production), written when a thread_id is first minted. Every thread route
checks it, so a valid token alone is not enough to read, stream, resume or
list someone else's run. Runs created before this table existed have no
row; they belong to OWNER_USER_ID.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from app.auth import User, owner_id


class ThreadOwners:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "create table if not exists thread_owners (thread_id text primary key, user_id text not null)"
            )
            self._conn.commit()

    def claim(self, thread_id: str, user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "insert or ignore into thread_owners (thread_id, user_id) values (?, ?)", (thread_id, user_id)
            )
            self._conn.commit()

    def owner_of(self, thread_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("select user_id from thread_owners where thread_id = ?", (thread_id,)).fetchone()
        return row[0] if row else None

    def can_access(self, thread_id: str, user: User) -> bool:
        owner = self.owner_of(thread_id)
        return user.id == (owner if owner is not None else owner_id())

    def close(self) -> None:
        self._conn.close()
