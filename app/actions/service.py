"""Durable, actor-scoped action workflows for resident support.

This SQLite implementation is a runnable reference adapter for the assignment.
In production, replace the operations writes with authenticated APIs from the
resident platform; preserve the state-machine, authorization, confirmation,
expiry, idempotency, and audit contracts.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ActionError(Exception):
    """Base class for safe, user-presentable action errors."""


class ActionNotFound(ActionError):
    """The action does not exist or is not accessible to this actor."""


class InvalidAction(ActionError):
    """The action request is invalid or no longer executable."""


class ActionExpired(ActionError):
    """The confirmation window has expired."""


@dataclass(frozen=True)
class ActionPreview:
    action_id: str
    action_type: str
    status: str
    summary: str
    expires_at: str


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    action_type: str
    status: str
    result: dict[str, Any]
    replayed: bool = False


class ActionService:
    """SQLite-backed action state machine with transactional execution.

    actor_id must come from trusted authentication middleware, never from the
    resident's free-text message. The default DB path can be overridden with
    RESIDENT_ACTION_DB. SQLite is suitable for this take-home/demo; production
    deployments should use the platform's transactional database/service.
    """

    def __init__(self, db_path: str | Path | None = None, confirmation_ttl_minutes: int = 10):
        if confirmation_ttl_minutes < 1:
            raise ValueError("confirmation_ttl_minutes must be positive")
        self.db_path = str(db_path or os.getenv("RESIDENT_ACTION_DB", "data/actions.sqlite3"))
        self.confirmation_ttl = timedelta(minutes=confirmation_ttl_minutes)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_actions (
                    action_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    action_type TEXT NOT NULL CHECK(action_type IN ('maintenance', 'booking_cancellation')),
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'declined', 'expired', 'failed')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    expires_epoch REAL NOT NULL,
                    result_json TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_actions_actor_status
                    ON pending_actions(actor_id, status, created_at);
                CREATE TABLE IF NOT EXISTS maintenance_requests (
                    request_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL UNIQUE REFERENCES pending_actions(action_id),
                    resident_id TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    room_number TEXT NOT NULL,
                    description TEXT NOT NULL,
                    urgency TEXT NOT NULL CHECK(urgency IN ('normal', 'urgent')),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id TEXT PRIMARY KEY,
                    resident_id TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'cancelled')),
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bookings_resident
                    ON bookings(resident_id, booking_id);
                CREATE TABLE IF NOT EXISTS action_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _payload_hash(payload_json: str) -> str:
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_text(value: str, field: str, max_length: int) -> str:
        cleaned = " ".join((value or "").split())
        if not cleaned:
            raise InvalidAction(f"{field} is required.")
        if len(cleaned) > max_length:
            raise InvalidAction(f"{field} must be {max_length} characters or fewer.")
        return cleaned

    def _audit(self, conn: sqlite3.Connection, action_id: str, actor_id: str,
               event: str, detail: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO action_audit(action_id, actor_id, event, detail_json, occurred_at) VALUES(?,?,?,?,?)",
            (action_id, actor_id, event, json.dumps(detail, sort_keys=True), self._iso(self._now())),
        )

    def _prepare(self, actor_id: str, action_type: str, payload: dict[str, Any], summary: str) -> ActionPreview:
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        now = self._now()
        expires = now + self.confirmation_ttl
        action_id = f"act_{uuid.uuid4().hex}"
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        idem_key = str(uuid.uuid4())
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO pending_actions
                   (action_id, actor_id, action_type, payload_json, payload_sha256, status,
                    created_at, expires_at, expires_epoch, idempotency_key)
                   VALUES(?,?,?,?,?,'pending',?,?,?,?)""",
                (action_id, actor, action_type, payload_json, self._payload_hash(payload_json),
                 self._iso(now), self._iso(expires), expires.timestamp(), idem_key),
            )
            self._audit(conn, action_id, actor, "prepared", {"action_type": action_type, "summary": summary})
            conn.commit()
        return ActionPreview(action_id, action_type, "pending", summary, self._iso(expires))

    def prepare_maintenance(self, actor_id: str, property_name: str, room_number: str,
                            description: str, urgency: str = "normal") -> ActionPreview:
        """Prepare, but do not submit, a maintenance request."""
        prop = self._require_text(property_name, "Property name", 160)
        room = self._require_text(room_number, "Room number", 40)
        desc = self._require_text(description, "Issue description", 1000)
        level = urgency.strip().lower()
        if level not in {"normal", "urgent"}:
            raise InvalidAction("Urgency must be 'normal' or 'urgent'.")
        payload = {"property_name": prop, "room_number": room, "description": desc, "urgency": level}
        article = "an" if level == "urgent" else "a"
        summary = f"Create {article} {level} maintenance request for {prop}, room {room}: {desc}"
        return self._prepare(actor_id, "maintenance", payload, summary)

    def prepare_cancellation(self, actor_id: str, booking_id: str) -> ActionPreview:
        """Prepare cancellation only if the booking belongs to this actor."""
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        booking = self._require_text(booking_id, "Booking ID", 128)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT booking_id, property_name, status FROM bookings WHERE booking_id=? AND resident_id=?",
                (booking, actor),
            ).fetchone()
        # Same response for unknown and unauthorized booking IDs to avoid enumeration.
        if row is None:
            raise ActionNotFound("Booking not found or not accessible.")
        if row["status"] != "active":
            raise InvalidAction("This booking is not active and cannot be cancelled again.")
        payload = {"booking_id": booking, "property_name": row["property_name"]}
        summary = f"Cancel booking {booking} at {row['property_name']}"
        return self._prepare(actor, "booking_cancellation", payload, summary)

    def confirm(self, actor_id: str, action_id: str) -> ActionResult:
        """Execute a previously previewed action after explicit actor confirmation.

        The exact payload is persisted and hash-checked. A repeat confirmation by
        the same actor returns the stored result without repeating the operation.
        """
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        aid = self._require_text(action_id, "Action ID", 128)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM pending_actions WHERE action_id=? AND actor_id=?",
                               (aid, actor)).fetchone()
            if row is None:
                conn.rollback()
                raise ActionNotFound("Action not found or not accessible.")
            if row["status"] == "completed":
                result = json.loads(row["result_json"] or "{}")
                conn.commit()
                return ActionResult(aid, row["action_type"], "completed", result, replayed=True)
            if row["status"] != "pending":
                conn.rollback()
                raise InvalidAction(f"Action is {row['status']} and cannot be confirmed.")
            if self._now().timestamp() >= row["expires_epoch"]:
                conn.execute("UPDATE pending_actions SET status='expired' WHERE action_id=?", (aid,))
                self._audit(conn, aid, actor, "expired", {})
                conn.commit()
                raise ActionExpired("Confirmation expired. Please prepare the request again.")
            payload_json = row["payload_json"]
            if self._payload_hash(payload_json) != row["payload_sha256"]:
                conn.execute("UPDATE pending_actions SET status='failed' WHERE action_id=?", (aid,))
                self._audit(conn, aid, actor, "integrity_check_failed", {})
                conn.commit()
                raise InvalidAction("Action integrity check failed; no operation was performed.")
            payload = json.loads(payload_json)
            now = self._iso(self._now())
            if row["action_type"] == "maintenance":
                request_id = f"mnt_{uuid.uuid4().hex}"
                conn.execute(
                    """INSERT INTO maintenance_requests
                       (request_id, action_id, resident_id, property_name, room_number, description,
                        urgency, status, created_at) VALUES(?,?,?,?,?,?,?,'submitted',?)""",
                    (request_id, aid, actor, payload["property_name"], payload["room_number"],
                     payload["description"], payload["urgency"], now),
                )
                result = {"request_id": request_id, "status": "submitted", "message": "Maintenance request submitted."}
            elif row["action_type"] == "booking_cancellation":
                booking = conn.execute(
                    "SELECT status FROM bookings WHERE booking_id=? AND resident_id=?",
                    (payload["booking_id"], actor),
                ).fetchone()
                if booking is None:
                    conn.rollback()
                    raise ActionNotFound("Booking not found or not accessible.")
                if booking["status"] != "active":
                    conn.rollback()
                    raise InvalidAction("Booking is no longer active; no cancellation was performed.")
                updated = conn.execute(
                    "UPDATE bookings SET status='cancelled', updated_at=? WHERE booking_id=? AND resident_id=? AND status='active'",
                    (now, payload["booking_id"], actor),
                ).rowcount
                if updated != 1:
                    conn.rollback()
                    raise InvalidAction("Booking changed before cancellation; please refresh and try again.")
                result = {"booking_id": payload["booking_id"], "status": "cancelled",
                          "message": "Booking cancellation recorded."}
            else:  # Defensive; schema CHECK should make this unreachable.
                conn.rollback()
                raise InvalidAction("Unsupported action type.")
            conn.execute(
                "UPDATE pending_actions SET status='completed', result_json=? WHERE action_id=?",
                (json.dumps(result, sort_keys=True), aid),
            )
            self._audit(conn, aid, actor, "completed", {"action_type": row["action_type"], "result": result})
            conn.commit()
            return ActionResult(aid, row["action_type"], "completed", result)

    def decline(self, actor_id: str, action_id: str) -> None:
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        aid = self._require_text(action_id, "Action ID", 128)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM pending_actions WHERE action_id=? AND actor_id=?",
                               (aid, actor)).fetchone()
            if row is None:
                conn.rollback()
                raise ActionNotFound("Action not found or not accessible.")
            if row["status"] != "pending":
                conn.rollback()
                raise InvalidAction(f"Action is {row['status']} and cannot be declined.")
            conn.execute("UPDATE pending_actions SET status='declined' WHERE action_id=?", (aid,))
            self._audit(conn, aid, actor, "declined", {})
            conn.commit()

    def get_action(self, actor_id: str, action_id: str) -> ActionPreview:
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM pending_actions WHERE action_id=? AND actor_id=?",
                               (action_id, actor)).fetchone()
        if row is None:
            raise ActionNotFound("Action not found or not accessible.")
        payload = json.loads(row["payload_json"])
        if row["action_type"] == "maintenance":
            summary = (f"Create a {payload['urgency']} maintenance request for {payload['property_name']}, "
                       f"room {payload['room_number']}: {payload['description']}")
        else:
            summary = f"Cancel booking {payload['booking_id']} at {payload['property_name']}"
        return ActionPreview(row["action_id"], row["action_type"], row["status"], summary, row["expires_at"])

    # Demo/test helper only. Production booking data must come from the authorized
    # resident platform, not from user input or this local SQLite adapter.
    def register_demo_booking(self, booking_id: str, resident_id: str, property_name: str,
                              status: str = "active") -> None:
        if status not in {"active", "cancelled"}:
            raise ValueError("status must be active or cancelled")
        now = self._iso(self._now())
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bookings(booking_id,resident_id,property_name,status,updated_at) VALUES(?,?,?,?,?)",
                (self._require_text(booking_id, "Booking ID", 128),
                 self._require_text(resident_id, "Resident ID", 128),
                 self._require_text(property_name, "Property name", 160), status, now),
            )

    def list_bookings(self, actor_id: str) -> list[dict[str, Any]]:
        """Return only bookings owned by the authenticated actor."""
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT booking_id, property_name, status, updated_at FROM bookings WHERE resident_id=? ORDER BY updated_at DESC",
                (actor,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_maintenance_requests(self, actor_id: str) -> list[dict[str, Any]]:
        """Actor-scoped view for tests/demo; do not expose without auth in a public API."""
        actor = self._require_text(actor_id, "Authenticated actor ID", 128)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT request_id, property_name, room_number, description, urgency, status, created_at "
                "FROM maintenance_requests WHERE resident_id=? ORDER BY created_at", (actor,),
            ).fetchall()
        return [dict(row) for row in rows]
