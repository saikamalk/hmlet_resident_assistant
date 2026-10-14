"""Regression tests for the refactored CLI entry point."""
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest


class CliStartupTests(unittest.TestCase):
    def test_cli_starts_and_exits_without_import_error(self):
        from app.cli import main

        output = StringIO()
        with patch("builtins.input", return_value="exit"), \
             patch("app.assistant.llm.GroqChatModel"), \
             redirect_stdout(output):
            main()

        self.assertIn("Hmlet Resident Support Assistant", output.getvalue())
        self.assertIn("Goodbye.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
