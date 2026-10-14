import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.actions.service import (
    ActionExpired,
    ActionNotFound,
    ActionService,
    InvalidAction,
)


class ActionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "actions.sqlite3"
        self.service = ActionService(self.db, confirmation_ttl_minutes=10)
        self.service.register_demo_booking("BKG-1001", "RES-1", "Riverside House")

    def tearDown(self):
        self.tmp.cleanup()

    def test_maintenance_is_not_submitted_before_confirmation(self):
        preview = self.service.prepare_maintenance(
            "RES-1", "Riverside House", "4", "Leaking bathroom tap", "urgent"
        )
        self.assertEqual(preview.status, "pending")
        self.assertEqual(self.service.list_maintenance_requests("RES-1"), [])

    def test_confirm_maintenance_submits_once(self):
        preview = self.service.prepare_maintenance(
            "RES-1", "Riverside House", "4", "Leaking bathroom tap", "urgent"
        )
        first = self.service.confirm("RES-1", preview.action_id)
        second = self.service.confirm("RES-1", preview.action_id)
        self.assertEqual(first.status, "completed")
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.result["request_id"], second.result["request_id"])
        self.assertEqual(len(self.service.list_maintenance_requests("RES-1")), 1)

    def test_action_cannot_be_confirmed_by_another_actor(self):
        preview = self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking")
        with self.assertRaises(ActionNotFound):
            self.service.confirm("RES-2", preview.action_id)
        self.assertEqual(self.service.list_maintenance_requests("RES-1"), [])

    def test_booking_cancellation_requires_ownership(self):
        with self.assertRaises(ActionNotFound):
            self.service.prepare_cancellation("RES-2", "BKG-1001")

    def test_cancellation_requires_confirmation_and_is_idempotent(self):
        preview = self.service.prepare_cancellation("RES-1", "BKG-1001")
        self.assertEqual(preview.status, "pending")
        first = self.service.confirm("RES-1", preview.action_id)
        second = self.service.confirm("RES-1", preview.action_id)
        self.assertEqual(first.result["status"], "cancelled")
        self.assertTrue(second.replayed)

    def test_declined_action_cannot_be_confirmed(self):
        preview = self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking")
        self.service.decline("RES-1", preview.action_id)
        with self.assertRaises(InvalidAction):
            self.service.confirm("RES-1", preview.action_id)

    def test_cancelled_booking_cannot_be_cancelled_twice(self):
        preview = self.service.prepare_cancellation("RES-1", "BKG-1001")
        self.service.confirm("RES-1", preview.action_id)
        with self.assertRaises(InvalidAction):
            self.service.prepare_cancellation("RES-1", "BKG-1001")

    def test_required_fields_are_validated(self):
        with self.assertRaises(InvalidAction):
            self.service.prepare_maintenance("RES-1", "", "4", "Tap leaking")
        with self.assertRaises(InvalidAction):
            self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking", "critical")

    def test_action_persists_across_service_restart(self):
        preview = self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking")
        restarted = ActionService(self.db)
        self.assertEqual(restarted.get_action("RES-1", preview.action_id).status, "pending")
        result = restarted.confirm("RES-1", preview.action_id)
        self.assertEqual(result.status, "completed")

    def test_unknown_action_does_not_leak_other_actor_details(self):
        preview = self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking")
        with self.assertRaises(ActionNotFound) as caught:
            self.service.get_action("RES-2", preview.action_id)
        self.assertEqual(str(caught.exception), "Action not found or not accessible.")

    def test_expired_action_cannot_be_confirmed(self):
        preview = self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking")
        conn = sqlite3.connect(self.db)
        try:
            with conn:
                conn.execute(
                    "UPDATE pending_actions SET expires_epoch=0 WHERE action_id=?",
                    (preview.action_id,),
                )
        finally:
            conn.close()
        with self.assertRaises(ActionExpired):
            self.service.confirm("RES-1", preview.action_id)
        self.assertEqual(self.service.get_action("RES-1", preview.action_id).status, "expired")

    def test_payload_tampering_fails_closed(self):
        preview = self.service.prepare_maintenance("RES-1", "Riverside House", "4", "Tap leaking")
        conn = sqlite3.connect(self.db)
        try:
            with conn:
                conn.execute(
                    "UPDATE pending_actions SET payload_json=? WHERE action_id=?",
                    (
                        '{"description":"change the action after preview",'
                        '"property_name":"Riverside House","room_number":"4",'
                        '"urgency":"normal"}',
                        preview.action_id,
                    ),
                )
        finally:
            conn.close()
        with self.assertRaises(InvalidAction):
            self.service.confirm("RES-1", preview.action_id)
        self.assertEqual(self.service.list_maintenance_requests("RES-1"), [])


if __name__ == "__main__":
    unittest.main()
