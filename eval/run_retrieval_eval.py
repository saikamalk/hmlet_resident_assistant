"""Run deterministic retrieval/guardrail checks; does not call an LLM or use API credits."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import settings  # noqa: E402
from app.assistant.guardrails import classify_input  # noqa: E402
from app.knowledge.retrieval import KnowledgeBase  # noqa: E402


def classify(question: str, kb: KnowledgeBase) -> tuple[str, list[str]]:
    gate = classify_input(question)
    if not gate.allowed:
        return "refused", []
    if gate.action_intent:
        return "action_required", []
    evidence = [e for e in kb.retrieve(question, top_k=8) if e.score >= settings.min_retrieval_score]
    if not evidence:
        return "unknown", []
    return "answerable", [f"{e.source} {e.text}" for e in evidence]


def main() -> int:
    cases = json.loads((ROOT / "data/evaluation_cases.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase(ROOT / "data")
    failures = []
    print(f"Running {len(cases)} offline retrieval/guardrail evaluations\n")
    for case in cases:
        actual, evidence = classify(case["question"], kb)
        ok = actual == case["expected"]
        required = case.get("must_contain", "").split(";") if case.get("must_contain") else []
        evidence_blob = " ".join(evidence)
        missing = [needle for needle in required if needle not in evidence_blob]
        ok = ok and not missing
        print(f"{'PASS' if ok else 'FAIL'} {case['id']} expected={case['expected']} actual={actual}" +
              (f" missing={missing}" if missing else ""))
        if not ok:
            failures.append(case["id"])
    print(f"\nResult: {len(cases) - len(failures)}/{len(cases)} passed")
    if failures:
        print("Failures:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
