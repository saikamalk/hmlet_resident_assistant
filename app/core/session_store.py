"""Persistence for session metadata and action-intake drafts (not message bodies)."""
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.actions.intake import Draft


class SessionStore:
    """Persistent session metadata and action-intake draft; message bodies are not stored."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("RESIDENT_SESSION_DB", "data/sessions.sqlite3")
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, created_at TEXT NOT NULL, last_active_at TEXT NOT NULL, draft_json TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_actor ON sessions(actor_id, last_active_at)")
            conn.commit()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def create(self, actor_id: str):
        sid, now = f"ses_{uuid.uuid4().hex}", self._now()
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?,NULL)", (sid, actor_id, now, now))
            conn.commit()
        return {"session_id": sid, "created_at": now}

    def get(self, session_id: str, actor_id: str):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=? AND actor_id=?",
                               (session_id, actor_id)).fetchone()
            if row:
                conn.execute("UPDATE sessions SET last_active_at=? WHERE session_id=?", (self._now(), session_id))
                conn.commit()
            return dict(row) if row else None

    def save_draft(self, session_id: str, actor_id: str, draft: Draft | None):
        payload = None if draft is None else json.dumps(
            {"action_type": draft.action_type, "fields": draft.fields, "waiting_for": draft.waiting_for},
            sort_keys=True)
        with closing(self._connect()) as conn:
            updated = conn.execute(
                "UPDATE sessions SET draft_json=?, last_active_at=? WHERE session_id=? AND actor_id=?",
                (payload, self._now(), session_id, actor_id)).rowcount
            conn.commit()
        if updated != 1:
            raise HTTPException(status_code=404, detail="Session not found.")

    @staticmethod
    def load_draft(row: dict[str, Any]) -> Draft | None:
        if not row.get("draft_json"):
            return None
        try:
            data = json.loads(row["draft_json"])
            if data.get("action_type") not in {"maintenance", "booking_cancellation", "booking_creation"}:
                return None
            return Draft(action_type=data["action_type"], fields=dict(data["fields"]), waiting_for=data["waiting_for"])
        except (ValueError, KeyError, TypeError):
            return None
