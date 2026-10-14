import unittest
from pathlib import Path

from app.assistant.service import ResidentAssistant
from app.knowledge.retrieval import KnowledgeBase

DATA = Path(__file__).resolve().parents[1] / "data"


class NeverCallModel:
    def __init__(self):
        self.calls = 0

    def answer(self, question, evidence):
        self.calls += 1
        raise AssertionError("Broad inventory questions should use structured listing data.")


class AvailableHousingTests(unittest.TestCase):
    def setUp(self):
        self.model = NeverCallModel()
        self.assistant = ResidentAssistant(KnowledgeBase(DATA), self.model, min_retrieval_score=0.2)

    def test_broad_housing_question_returns_all_available_listing_details(self):
        result = self.assistant.respond("What are available housings and their details?")
        self.assertEqual(result.status, "answered")
        self.assertIn("Riverside House", result.answer)
        self.assertIn("Maple Court", result.answer)
        self.assertIn("Harbour View Residences", result.answer)
        self.assertIn("Sakura Terrace", result.answer)
        self.assertIn("Orchid Gardens", result.answer)
        self.assertIn("1,450 SGD/month", result.answer)
        self.assertIn("Available from:", result.answer)
        self.assertIn("Minimum stay:", result.answer)
        self.assertIn("Amenities:", result.answer)
        self.assertNotIn("LST-104", result.answer)
        self.assertNotIn("LST-108", result.answer)
        self.assertEqual(self.model.calls, 0)

    def test_broad_inventory_variants_route_to_structured_listing_data(self):
        for prompt in (
                "Show me all available properties",
                "What housing options are available?",
                "List available rooms and their details",
                "What are the available accommodations?",
        ):
            with self.subTest(prompt=prompt):
                result = self.assistant.respond(prompt)
                self.assertEqual(result.status, "answered")
                self.assertIn("Listing ID:", result.answer)
        self.assertEqual(self.model.calls, 0)

    def test_response_evidence_has_listing_ids_for_traceability(self):
        result = self.assistant.respond("What are available housings and their details?")
        sources = {item.source for item in result.evidence}
        self.assertIn("listings.json:LST-101", sources)
        self.assertNotIn("listings.json:LST-104", sources)


if __name__ == "__main__":
    unittest.main()
