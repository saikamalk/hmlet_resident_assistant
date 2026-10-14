import unittest

from app.assistant.claim_grounding import audit_claim_support
from app.schemas import Evidence


class ClaimGroundingTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [Evidence("listings.json:LST-101",
                                  "Property: Riverside House | Room type: Private single room | Monthly rent: 1450 SGD | Deposit: 1450 SGD",
                                  0.9)]

    def test_supported_claim_passes_heuristic(self):
        result = audit_claim_support("The monthly rent is 1450 SGD.", self.evidence)
        self.assertTrue(result.passed)

    def test_unsupported_number_is_flagged(self):
        result = audit_claim_support("The monthly rent is 9999 SGD.", self.evidence)
        self.assertFalse(result.passed)
        self.assertEqual(result.claims[0].reason, "unsupported_numeric_fact")

    def test_unrelated_claim_is_flagged(self):
        result = audit_claim_support("The building has a rooftop swimming pool.", self.evidence)
        self.assertFalse(result.passed)

    def test_citation_line_is_not_treated_as_a_claim(self):
        result = audit_claim_support("The monthly rent is 1450 SGD.\nSources: listings.json:LST-101", self.evidence)
        self.assertTrue(result.passed)

    def test_empty_answer_fails_audit(self):
        self.assertFalse(audit_claim_support("", self.evidence).passed)


if __name__ == "__main__":
    unittest.main()
