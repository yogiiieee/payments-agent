"""Tier-1: scripted scenarios driven by pytest against a mocked API.

Deterministic and key-free. Each entry in scenarios.py becomes one parametrized test;
composite inputs use scripted extractions, so the LLM is out of the loop. Run: pytest
"""

from decimal import Decimal

import pytest

from payment_agent.agent import Agent
from payment_agent.extraction import Extraction
from payment_agent.templates import TEMPLATES, Msg

from .mock_api import ACCOUNTS, MockApi
from .scenarios import SCENARIOS, Scenario

# values that must never appear in any agent reply
PII_VALUES = [str(a[k]) for a in ACCOUNTS for k in ("dob", "aadhaar_last4", "pincode")]
CRASH_TEXT = TEMPLATES[Msg.INTERNAL_ERROR]


class ScriptedExtractor:
    """Deterministic LLM stand-in: exact (masked) input text -> Extraction."""

    def __init__(self, script: dict[str, Extraction]):
        self.script = script

    def extract(self, text: str, context: str) -> Extraction | None:
        ext = self.script.get(text)
        return ext.model_copy() if ext else None  # copy: ground() mutates


def _run(sc: Scenario) -> list[str]:
    """Drive one scenario and return the list of correctness failures (empty == pass)."""
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

        if CRASH_TEXT in reply:  # next() must never surface an internal error
            failures.append(f"turn {i}: internal error surfaced for {turn.user!r}")
        for value in PII_VALUES:  # no on-file factor may ever be echoed
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
        if not p["idempotency_key"]:
            failures.append("payment sent without an idempotency key")
    if sc.custom:
        failures.extend(sc.custom(agent, mock))

    return failures


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.name)
def test_scenario(sc: Scenario) -> None:
    failures = _run(sc)
    assert not failures, "\n".join(failures)
