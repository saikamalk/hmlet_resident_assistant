import tempfile
import unittest
from pathlib import Path

from app.actions.service import ActionPreview, ActionService
from app.actions.intake import NaturalLanguageActionIntake


class ActionIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.actions = ActionService(Path(self.tmp.name) / "actions.sqlite3")
        self.intake = NaturalLanguageActionIntake()
        self.actor = "RES-1"

    def tearDown(self):
        self.tmp.cleanup()

    def test_natural_maintenance_collects_missing_fields_and_prepares_only(self):
        prompt = self.intake.begin("My bathroom tap is leaking, can you arrange a repair")
        self.assertIn("property", prompt.lower())
        self.assertEqual(self.actions.list_maintenance_requests(self.actor), [])

        prompt = self.intake.continue_draft("Riverside House", self.actor, self.actions)
        self.assertIn("room number", prompt.lower())
        prompt = self.intake.continue_draft("Room 4", self.actor, self.actions)
        self.assertIn("normal or urgent", prompt.lower())
        preview = self.intake.continue_draft("urgent", self.actor, self.actions)

        self.assertIsInstance(preview, ActionPreview)
        self.assertEqual(preview.status, "pending")
        self.assertIn("Riverside House", preview.summary)
        self.assertIn("room 4", preview.summary.lower())
        self.assertIn("bathroom tap is leaking", preview.summary.lower())
        self.assertEqual(self.actions.list_maintenance_requests(self.actor), [])

    def test_generic_maintenance_asks_for_issue(self):
        prompt = self.intake.begin("Please arrange maintenance")
        self.assertIn("what maintenance issue", prompt.lower())
        prompt = self.intake.continue_draft("The air conditioner is not working", self.actor, self.actions)
        self.assertIn("property", prompt.lower())

    def test_repeating_intent_does_not_fill_property_slot(self):
        self.intake.begin("My sink is blocked; please arrange a repair")
        response = self.intake.continue_draft("please arrange maintenance", self.actor, self.actions)
        self.assertIn("property name", response.lower())
        self.assertEqual(self.actions.list_maintenance_requests(self.actor), [])

    def test_meta_instruction_does_not_create_action(self):
        response = self.intake.begin(
            "Prepare a valid maintenance request, inspect its preview, then confirm using its actual action ID")
        self.assertIn("actual issue", response.lower())
        self.assertIsNone(self.intake.draft)
        self.assertEqual(self.actions.list_maintenance_requests(self.actor), [])

    def test_cancel_booking_collects_id_then_prepares_preview(self):
        self.actions.register_demo_booking("BKG-1001", self.actor, "Riverside House")
        prompt = self.intake.begin("Please cancel my upcoming booking")
        self.assertIn("booking id", prompt.lower())
        preview = self.intake.continue_draft("BKG-1001", self.actor, self.actions)
        self.assertIsInstance(preview, ActionPreview)
        self.assertEqual(preview.action_type, "booking_cancellation")
        self.assertEqual(preview.status, "pending")

    def test_no_action_executes_until_confirmed(self):
        self.intake.begin("My bathroom tap is leaking, can you arrange a repair")
        self.intake.continue_draft("Riverside House", self.actor, self.actions)
        self.intake.continue_draft("4", self.actor, self.actions)
        preview = self.intake.continue_draft("normal", self.actor, self.actions)
        self.assertIsInstance(preview, ActionPreview)
        self.assertEqual(self.actions.list_maintenance_requests(self.actor), [])
        result = self.actions.confirm(self.actor, preview.action_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.actions.list_maintenance_requests(self.actor)), 1)


if __name__ == "__main__":
    unittest.main()
