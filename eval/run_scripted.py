"""Tier-1 runner: scripted dialogues against the mocked API. Deterministic, key-free.

Usage: python -m eval.run_scripted
"""

import sys
from decimal import Decimal

from payment_agent.agent import Agent
from payment_agent.extraction import Extraction
from payment_agent.templates import TEMPLATES, Msg

from .mock_api import ACCOUNTS, MockApi
from .scenarios import SCENARIOS, Scenario

# values that must never appear in any agent reply
PII_VALUES: list[str] = [str(a[k]) for a in ACCOUNTS for k in ("dob", "aadhaar_last4", "pincode")]
CRASH_TEXT = TEMPLATES[Msg.INTERNAL_ERROR]


class ScriptedExtractor:
    """Deterministic LLM stand-in: exact (masked) input text -> Extraction."""

    def __init__(self, script: dict[str, Extraction]):
        self.script = script

    def extract(self, text: str, context: str) -> Extraction | None:
        ext = self.script.get(text)
        return ext.model_copy() if ext else None  # copy: ground() mutates


def run_scenario(sc: Scenario) -> tuple[list[str], int]:
    mock = MockApi()
    agent = Agent(api_client=mock, extractor=ScriptedExtractor(sc.llm))
    failures: list[str] = []

    for i, turn in enumerate(sc.turns, 1):
        if turn.before:
            turn.before(mock)
        payments_before = len(mock.payments)
        out = agent.next(turn.user)

        if not (isinstance(out, dict) and set(out) == {"message"} and isinstance(out["message"], str)):
            failures.append(f"turn {i}: bad return shape {out!r}")
            continue
        reply = out["message"]

        if CRASH_TEXT in reply:
            failures.append(f"turn {i}: internal error surfaced for {turn.user!r}")
        for value in PII_VALUES:
            if value in reply:
                failures.append(f"turn {i}: PII {value!r} leaked in reply")
        if len(mock.payments) > payments_before and not agent.session.verified:
            failures.append(f"turn {i}: payment processed before verification")
        for needle in turn.expect:
            if needle not in reply:
                failures.append(f"turn {i}: expected {needle!r} in {reply!r}")
        for needle in turn.reject:
            if needle in reply:
                failures.append(f"turn {i}: forbidden {needle!r} in {reply!r}")

    if sc.final_state is not None and agent.session.state is not sc.final_state:
        failures.append(f"final state {agent.session.state.name}, expected {sc.final_state.name}")
    if len(mock.payments) != sc.payments:
        failures.append(f"{len(mock.payments)} payments processed, expected {sc.payments}")
    for p in mock.payments:  # tool-call correctness: payload completeness
        if any(v is None for v in p["card"].values()):
            failures.append(f"payment payload had a missing card field: {p['card']}")
        if p["amount"] != p["amount"].quantize(Decimal("0.01")):
            failures.append(f"payment amount not 2dp: {p['amount']}")
    if sc.custom:
        failures.extend(sc.custom(agent, mock))

    return failures, len(sc.turns)


def main() -> int:
    passed = failed = total_turns = 0
    for sc in SCENARIOS:
        failures, turns = run_scenario(sc)
        total_turns += turns
        status = "PASS" if not failures else "FAIL"
        print(f"  {status}  {sc.name}")
        for f in failures:
            print(f"        - {f}")
        passed += not failures
        failed += bool(failures)
    print(f"\n  {passed}/{len(SCENARIOS)} scenarios passed ({total_turns} turns exercised)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
