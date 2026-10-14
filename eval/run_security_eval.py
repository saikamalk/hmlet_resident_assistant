"""Offline adversarial regression suite; no LLM/API calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import settings  # noqa: E402
from app.assistant.guardrails import classify_input  # noqa: E402
from app.knowledge.retrieval import KnowledgeBase  # noqa: E402


def classify(question: str, kb: KnowledgeBase) -> str:
    gate = classify_input(question)
    if not gate.allowed:
        return "refused"
    if gate.action_intent:
        return "action_required"
    evidence = [e for e in kb.retrieve(question, top_k=8) if e.score >= settings.min_retrieval_score]
    return "answerable" if evidence else "unknown"


def main() -> int:
    cases = json.loads((ROOT / "data/adversarial_cases.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase(ROOT / "data")
    failures = []
    for case in cases:
        actual = classify(case["question"], kb)
        passed = actual == case["expected"]
        print(f"{'PASS' if passed else 'FAIL'} {case['id']} expected={case['expected']} actual={actual}")
        if not passed:
            failures.append(case["id"])
    print(f"\nSecurity regression: {len(cases) - len(failures)}/{len(cases)} passed")
    if failures:
        print("Review failures:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
