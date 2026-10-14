"""API-boundary authorization regressions using the real SQLite action services."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.actions.service import ActionNotFound, ActionService
from app.api import SessionStore, create_app
from app.bookings.service import BookingService
from app.core.auth import get_current_actor
from app.core.rate_limit import RateLimiter
from app.schemas import AssistantResponse


class FakeAssistant:
    # This test exercises action routes, not LLM readiness.
    def respond(self, message):
        return AssistantResponse(answer="Grounded test response.", status="answered")


class ActionAuthorizationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._clock_patch = patch.object(
            BookingService,
            "_now",
            return_value=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        self._clock_patch.start()
        root = Path(self.tmp.name)
        self.actions = ActionService(root / "actions.sqlite3")
        self.sessions = SessionStore(str(root / "sessions.sqlite3"))
        self.bookings = BookingService(self.actions, Path(__file__).resolve().parents[1] / "data")
        self.actor_id = {"value": "resident-A"}
        self.app = create_app(
            assistant=FakeAssistant(), action_service=self.actions, session_store=self.sessions,
            rate_limiter=RateLimiter(limit=100, window_seconds=60),
        )
        # Explicitly bind the exact stores used to seed the fixtures. This also
        # keeps the test robust if create_app dependency initialization changes.
        self.app.state.session_store = self.sessions
        self.app.state.action_service = self.actions
        self.app.state.booking_service = self.bookings
        self.app.dependency_overrides[get_current_actor] = lambda: {
            "actor_id": self.actor_id["value"], "claims": {"sub": self.actor_id["value"]}
        }
        self.client = TestClient(self.app)
        # API versioning is part of this application's public contract. Do not
        # infer it by inspecting app.routes: newer FastAPI versions may represent
        # included routers differently, causing this check to choose "" and every
        # request to hit a non-existent, unversioned path (404).
        self.api_prefix = "/v1"
        # Session creation is not the behavior under test here. Create owned
        # sessions directly so these tests isolate the HTTP action authorization
        # path and do not fail because of unrelated session-route/auth setup.
        self.session_a = self.sessions.create("resident-A")["session_id"]
        self.session_b = self.sessions.create("resident-B")["session_id"]

    def tearDown(self):
        self.client.close()
        self._clock_patch.stop()
        self.tmp.cleanup()

    def _maintenance_action(self):
        return self.actions.prepare_maintenance(
            "resident-A", "Orchard Residence", "12-04", "Leaking sink", "normal"
        )

    def _booking_action(self):
        return self.bookings.prepare_booking("resident-A", "LST-101", "2026-11-01", 3)

    def test_other_resident_cannot_confirm_maintenance_via_endpoint(self):
        preview = self._maintenance_action()
        self.actor_id["value"] = "resident-B"
        response = self.client.post(
            f"{self.api_prefix}/sessions/{self.session_b}/actions/{preview.action_id}/confirm"
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("not accessible", response.json()["detail"].lower())
        with self.assertRaises(ActionNotFound):
            self.actions.get_action("resident-B", preview.action_id)

    def test_other_resident_cannot_confirm_maintenance_via_chat_command(self):
        preview = self._maintenance_action()
        self.actor_id["value"] = "resident-B"
        response = self.client.post(
            f"{self.api_prefix}/sessions/{self.session_b}/messages",
            json={"message": f"/confirm {preview.action_id}"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.actions.get_action("resident-A", preview.action_id).status, "pending")

    def test_other_resident_cannot_decline_booking_via_endpoint(self):
        preview = self._booking_action()
        self.actor_id["value"] = "resident-B"
        response = self.client.post(
            f"{self.api_prefix}/sessions/{self.session_b}/actions/{preview.action_id}/decline"
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(self.bookings.is_booking_action("resident-A", preview.action_id))
        self.assertFalse(self.bookings.is_booking_action("resident-B", preview.action_id))

    def test_other_resident_cannot_decline_booking_via_chat_command(self):
        preview = self._booking_action()
        self.actor_id["value"] = "resident-B"
        response = self.client.post(
            f"{self.api_prefix}/sessions/{self.session_b}/messages",
            json={"message": f"/decline {preview.action_id}"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(self.bookings.is_booking_action("resident-A", preview.action_id))


if __name__ == "__main__":
    unittest.main()
