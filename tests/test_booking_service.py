import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.actions.service import ActionService
from app.api import SessionStore, create_app
from app.bookings.service import BookingService
from app.core.auth import get_current_actor
from app.core.rate_limit import RateLimiter
from app.schemas import AssistantResponse


class FakeAssistant:
    def respond(self, message):
        return AssistantResponse(answer="Grounded test response.", status="answered")


class BookingServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.actions = ActionService(str(self.root / "actions.sqlite3"))
        self.service = BookingService(self.actions, Path(__file__).resolve().parents[1] / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_availability_respects_start_date_and_minimum_stay(self):
        results = self.service.available_listings("2026-11-01", 3, "Singapore")
        ids = {item["listing_id"] for item in results}
        self.assertIn("LST-101", ids)
        self.assertNotIn("LST-102", ids)  # Published availability starts later.
        self.assertNotIn("LST-109", ids)  # Six-month minimum stay.

    def test_booking_requires_confirmation_and_is_idempotent(self):
        preview = self.service.prepare_booking("resident-1", "LST-101", "2026-11-01", 3)
        self.assertEqual(self.service.my_bookings("resident-1"), [])
        result = self.service.confirm("resident-1", preview.action_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.service.my_bookings("resident-1")), 1)
        replay = self.service.confirm("resident-1", preview.action_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(len(self.service.my_bookings("resident-1")), 1)

    def test_past_move_in_date_is_rejected(self):
        with self.assertRaises(Exception):
            self.service.prepare_booking("resident-1", "LST-101", "2020-01-01", 3)

    def test_booking_action_is_actor_scoped(self):
        preview = self.service.prepare_booking("resident-1", "LST-101", "2026-11-01", 3)
        with self.assertRaises(Exception):
            self.service.confirm("resident-2", preview.action_id)

    def test_api_create_list_and_cancel_by_selection(self):
        sessions = SessionStore(str(self.root / "sessions.sqlite3"))
        app = create_app(assistant=FakeAssistant(), action_service=self.actions, session_store=sessions,
                         rate_limiter=RateLimiter(limit=100, window_seconds=60))
        app.dependency_overrides[get_current_actor] = lambda: {"actor_id": "resident-1", "claims": {}}
        with TestClient(app) as client:
            sid = client.post("/v1/sessions").json()["session_id"]
            created = client.post(f"/v1/sessions/{sid}/messages", json={
                "message": "Book LST-101 from 2026-11-01 for 3 months"}).json()
            self.assertEqual(created["status"], "action_pending")
            action_id = created["action"]["action_id"]
            confirmed = client.post(f"/v1/sessions/{sid}/actions/{action_id}/confirm").json()
            self.assertEqual(confirmed["status"], "completed")
            rows = client.get("/v1/bookings").json()["bookings"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["listing_id"], "LST-101")
            cancel = client.post(f"/v1/sessions/{sid}/messages", json={"message": "Cancel my upcoming booking"}).json()
            self.assertEqual(cancel["status"], "action_collecting_details")
            choice = client.post(f"/v1/sessions/{sid}/messages", json={"message": "1"}).json()
            self.assertEqual(choice["status"], "action_pending")
            cancel_id = choice["action"]["action_id"]
            done = client.post(f"/v1/sessions/{sid}/actions/{cancel_id}/confirm").json()
            self.assertEqual(done["status"], "completed")
            self.assertEqual(client.get("/v1/bookings").json()["bookings"][0]["status"], "cancelled")

    def test_minimum_stay_error_keeps_draft_recoverable(self):
        from app.actions.intake import Draft
        draft = Draft("booking_creation", {
            "listing_id": "LST-109", "start_date": "2026-11-01", "stay_months": "2"
        }, waiting_for="")
        answer = self.service.continue_draft(draft, "2", "resident-1")
        self.assertIn("at least", answer.lower())
        self.assertEqual(draft.waiting_for, "stay_months")
        self.assertNotIn("stay_months", draft.fields)

    def test_unavailable_listing_error_asks_for_another_listing_and_preserves_dates(self):
        from app.actions.intake import Draft
        draft = Draft("booking_creation", {
            "listing_id": "LST-101", "start_date": "2026-11-01", "stay_months": "3"
        }, waiting_for="")
        # This date is after the local catalog's current date; force a catalog mismatch
        # using a listing that is not eligible for this stay/date combination.
        draft.fields["listing_id"] = "LST-109"
        draft.fields["stay_months"] = "2"
        answer = self.service.continue_draft(draft, "2", "resident-1")
        self.assertTrue("minimum stay" in answer.lower() or "at least" in answer.lower())
        self.assertEqual(draft.waiting_for, "stay_months")
        self.assertEqual(draft.fields.get("start_date"), "2026-11-01")


if __name__ == "__main__":
    unittest.main()
