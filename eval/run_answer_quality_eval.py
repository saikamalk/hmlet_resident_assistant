"""Offline retrieval and claim-grounding diagnostics; does not call an LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import settings  # noqa: E402
from app.assistant.guardrails import classify_input  # noqa: E402
from app.knowledge.retrieval import KnowledgeBase  # noqa: E402
from app.assistant.claim_grounding import audit_claim_support  # noqa: E402


def main() -> int:
    cases = json.loads((ROOT / "data/evaluation_cases.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase(ROOT / "data")
    answerable = 0
    retrieval_hits = 0
    rr_sum = 0.0
    routing_correct = 0
    unknown_false_positive = 0
    print(f"Evaluating {len(cases)} assignment cases (offline)\n")
    for case in cases:
        q = case["question"]
        gate = classify_input(q)
        if not gate.allowed:
            status, hits = "refused", []
        elif gate.action_intent:
            status, hits = "action_required", []
        else:
            hits = [x for x in kb.retrieve(q, top_k=8) if x.score >= settings.min_retrieval_score]
            status = "answerable" if hits else "unknown"
        routing_correct += status == case["expected"]
        required = [x for x in case.get("must_contain", "").split(";") if x]
        if case["expected"] == "answerable":
            answerable += 1
            ranks = []
            for needle in required:
                rank = next((i for i, e in enumerate(hits, 1) if needle in e.source or needle in e.text), None)
                if rank is not None:
                    ranks.append(rank)
            hit = bool(required) and len(ranks) == len(required)
            retrieval_hits += hit
            rr_sum += (1 / max(ranks)) if hit else 0.0
        elif case["expected"] == "unknown" and hits:
            unknown_false_positive += 1
        print(f"{case['id']} expected={case['expected']:<14} actual={status:<14} retrieved={len(hits)}")
    print("\nMetrics")
    print(f"Routing accuracy: {routing_correct}/{len(cases)} = {routing_correct / len(cases):.3f}")
    print(f"Required-evidence hit rate: {retrieval_hits}/{answerable} = {retrieval_hits / max(1, answerable):.3f}")
    print(f"MRR-like reciprocal rank (all required evidence present): {rr_sum / max(1, answerable):.3f}")
    print(f"Unknown-case false-positive retrievals: {unknown_false_positive}")

    # Smoke-check claim auditor with grounded and deliberately unsupported examples.
    sample = kb.retrieve("monthly rent for a private single room at Riverside House", top_k=8)
    grounded = audit_claim_support("The monthly rent is 1450 SGD.", sample)
    hallucinated = audit_claim_support("The monthly rent is 9999 SGD.", sample)
    print(f"Claim audit grounded example: {'PASS' if grounded.passed else 'FAIL'}")
    print(f"Claim audit unsupported-number example: {'PASS' if not hallucinated.passed else 'FAIL'}")
    return 0 if grounded.passed and not hallucinated.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
