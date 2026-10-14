"""Concurrency and expiry checks for the persisted action state machine."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.actions.service import ActionExpired, ActionService, InvalidAction


class ActionConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ActionService(Path(self.tmp.name) / "actions.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_expired_action_is_rejected_with_specific_exception(self):
        preview = self.service.prepare_maintenance(
            "resident-A", "Orchard Residence", "12-04", "Leaking sink", "normal"
        )
        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        with patch.object(self.service, "_now", return_value=future):
            with self.assertRaises(ActionExpired):
                self.service.confirm("resident-A", preview.action_id)

    def test_concurrent_confirmations_execute_once_and_replay(self):
        preview = self.service.prepare_maintenance(
            "resident-A", "Orchard Residence", "12-04", "Leaking sink", "normal"
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: self.service.confirm("resident-A", preview.action_id), range(8)
            ))
        self.assertTrue(all(result.status == "completed" for result in results))
        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(sum(result.replayed for result in results), 7)

    def test_declined_action_cannot_be_confirmed(self):
        preview = self.service.prepare_maintenance(
            "resident-A", "Orchard Residence", "12-04", "Leaking sink", "normal"
        )
        self.service.decline("resident-A", preview.action_id)
        with self.assertRaises(InvalidAction):
            self.service.confirm("resident-A", preview.action_id)


if __name__ == "__main__":
    unittest.main()
