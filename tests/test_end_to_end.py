"""Offline end-to-end acceptance tests for the resident-to-action workflow.

Uses the real FastAPI routes, SQLite action/session stores, and action-intake
state machine. The LLM is replaced with a deterministic fake to avoid API cost.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.actions.service import ActionService
from app.api import SessionStore, create_app
from app.core.rate_limit import RateLimiter


class FakeAssistant:
    def respond(self, message: str):
        from app.schemas import AssistantResponse
        return AssistantResponse(
            answer="I do not have that information.",
            status="unknown",
            evidence=(),
        )


class EndToEndAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.action_db = root / "actions.sqlite3"
        self.session_db = root / "sessions.sqlite3"
        self.actions = ActionService(db_path=self.action_db)
        self.sessions = SessionStore(db_path=str(self.session_db))
        self.app = create_app(
            assistant=FakeAssistant(),
            action_service=self.actions,
            session_store=self.sessions,
            rate_limiter=RateLimiter(limit=100, window_seconds=60),
        )
        # Authentication is isolated to this test; production auth is not bypassed.
        from app.core.auth import get_current_actor
        self.app.dependency_overrides[get_current_actor] = lambda: {
            "actor_id": "resident-test-1",
            "claims": {"sub": "resident-test-1"},
        }
        self.client = TestClient(self.app)
        response = self.client.post("/v1/sessions")
        self.assertEqual(response.status_code, 201, response.text)
        self.session_id = response.json()["session_id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def send(self, message: str):
        return self.client.post(
            f"/v1/sessions/{self.session_id}/messages",
            json={"message": message},
        )

    def test_maintenance_intake_preview_confirmation_and_single_execution(self):
        r1 = self.send("My bathroom tap is leaking, can you arrange a repair")
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["status"], "action_collecting_details")
        self.assertIn("property", r1.json()["answer"].lower())

        r2 = self.send("Riverside House")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIn("room", r2.json()["answer"].lower())

        r3 = self.send("Room 4")
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertIn("urgent", r3.json()["answer"].lower())

        r4 = self.send("urgent")
        self.assertEqual(r4.status_code, 200, r4.text)
        body = r4.json()
        self.assertEqual(body["status"], "action_pending")
        action_id = body["action"]["action_id"]
        self.assertIn("Riverside House", body["action"]["summary"])
        self.assertIn("room 4", body["action"]["summary"].lower())

        # Preview alone must not create a submitted maintenance request.
        with closing(sqlite3.connect(self.action_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM maintenance_requests").fetchone()[0]
        self.assertEqual(count, 0)

        confirmed = self.client.post(
            f"/v1/sessions/{self.session_id}/actions/{action_id}/confirm"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["status"], "completed")

        # Repeat confirmation must be idempotent and not create a second request.
        replay = self.client.post(
            f"/v1/sessions/{self.session_id}/actions/{action_id}/confirm"
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["action"]["replayed"])
        with closing(sqlite3.connect(self.action_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM maintenance_requests").fetchone()[0]
        self.assertEqual(count, 1)

    def test_other_actor_cannot_use_session(self):
        from app.core.auth import get_current_actor
        self.app.dependency_overrides[get_current_actor] = lambda: {
            "actor_id": "resident-test-2",
            "claims": {"sub": "resident-test-2"},
        }
        response = self.send("What is the rent?")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Session not found.")

    def test_health_routes_are_public_and_documented(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/health", schema["paths"])
        self.assertIn("/ready", schema["paths"])

    def test_empty_message_is_rejected(self):
        response = self.client.post(
            f"/v1/sessions/{self.session_id}/messages", json={"message": ""}
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
