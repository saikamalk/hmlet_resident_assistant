"""Readiness regression tests for provider and SQLite health checks."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.actions.service import ActionService
from app.api import SessionStore, create_app
from app.core.rate_limit import RateLimiter
from app.routes.health import _check_sqlite_store


class HealthyModel:
    def health_check(self):
        return True


class UnhealthyModel:
    def health_check(self):
        return False


class FakeAssistant:
    def __init__(self, model):
        self.model = model


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.actions = ActionService(root / "actions.sqlite3")
        self.sessions = SessionStore(str(root / "sessions.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self, model):
        app = create_app(
            assistant=FakeAssistant(model),
            action_service=self.actions,
            session_store=self.sessions,
            rate_limiter=RateLimiter(limit=100, window_seconds=60, redis_url=""),
        )
        return TestClient(app)

    def test_ready_when_model_and_initialized_stores_are_healthy(self):
        with self._client(HealthyModel()) as client:
            response = client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_not_ready_when_model_is_unhealthy(self):
        with self._client(UnhealthyModel()) as client:
            response = client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["llm"], "unhealthy")

    def test_not_ready_when_production_oidc_is_not_configured(self):
        with self._client(HealthyModel()) as client:
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "production",
                    "AUTH_JWKS_URL": "",
                    "AUTH_ISSUER": "",
                    "AUTH_AUDIENCE": "",
                },
            ):
                response = client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["auth"], "not_configured")

    def test_in_memory_sqlite_store_is_not_treated_as_filesystem_path(self):
        class InMemoryStore:
            db_path = ":memory:"

        self.assertIsNone(_check_sqlite_store(InMemoryStore(), "Test"))


if __name__ == "__main__":
    unittest.main()
