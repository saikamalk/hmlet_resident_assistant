import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.actions.service import ActionResult
from app.api import SessionStore, create_app
from app.core.rate_limit import RateLimiter


class FakeAssistant:
    def respond(self, message):
        from app.schemas import AssistantResponse
        return AssistantResponse(answer="Grounded test response.", status="answered")


class FakeActions:
    def __init__(self): self.confirmed = []; self.declined = []

    def confirm(self, actor_id, action_id):
        self.confirmed.append((actor_id, action_id))
        return ActionResult(action_id, "maintenance", "completed", {"message": "Submitted."})

    def decline(self, actor_id, action_id): self.declined.append((actor_id, action_id))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sessions = SessionStore(str(Path(self.tmp.name) / "sessions.sqlite3"));
        self.actions = FakeActions()
        self.app = create_app(assistant=FakeAssistant(), action_service=self.actions, session_store=self.sessions,
                              rate_limiter=RateLimiter(limit=20, window_seconds=60))
        from app.core.auth import get_current_actor
        self.app.dependency_overrides[get_current_actor] = lambda: {"actor_id": "resident-1", "claims": {}}
        self.client = TestClient(self.app);
        self.session_id = self.client.post("/v1/sessions").json()["session_id"]

    def tearDown(self): self.client.close(); self.tmp.cleanup()

    def test_create_session_and_chat(self):
        r = self.client.post(f"/v1/sessions/{self.session_id}/messages", json={"message": "What is rent?"})
        self.assertEqual(r.status_code, 200);
        self.assertEqual(r.json()["answer"], "Grounded test response.");
        self.assertTrue(r.json()["request_id"])

    def test_session_not_found_for_other_actor(self):
        from app.core.auth import get_current_actor
        self.app.dependency_overrides[get_current_actor] = lambda: {"actor_id": "resident-2", "claims": {}}
        r = self.client.post(f"/v1/sessions/{self.session_id}/messages", json={"message": "hello"});
        self.assertEqual(r.status_code, 404)

    def test_rate_limit_returns_429(self):
        self.app.state.rate_limiter = RateLimiter(limit=1, window_seconds=60)
        self.client.post("/v1/sessions");
        r = self.client.post("/v1/sessions")
        self.assertEqual(r.status_code, 429);
        self.assertIn("Retry-After", r.headers)

    def test_confirmation_uses_authenticated_actor(self):
        r = self.client.post(f"/v1/sessions/{self.session_id}/actions/act-123/confirm")
        self.assertEqual(r.status_code, 200);
        self.assertEqual(self.actions.confirmed, [("resident-1", "act-123")])

    def test_session_draft_persists_without_storing_messages(self):
        from app.actions.intake import Draft
        self.sessions.save_draft(self.session_id, "resident-1",
                                 Draft("maintenance", {"description": "tap leak"}, "property_name"))
        row = self.sessions.get(self.session_id, "resident-1");
        draft = self.sessions.load_draft(row)
        self.assertEqual(draft.fields["description"], "tap leak");
        self.assertNotIn("message", row)

    def test_health_live(self): self.assertEqual(self.client.get("/health/live").status_code, 200)


if __name__ == "__main__": unittest.main()
