# Payment Collection Agent

A conversational agent that walks a user through paying an outstanding balance: account lookup, identity verification, amount and card collection, payment, and recap. It exposes the interface the assignment requires, `Agent().next(user_input) -> {"message": str}`, and keeps all conversation state inside the instance. No payment step is reachable before verification passes.

## Layout

```
agent.py                      entry point required by the assignment (re-exports Agent)
cli.py                        interactive chat loop for manual testing
payment_agent/
  agent.py                    Agent class and the per-turn pipeline
  state_machine.py            states, transitions, retry counters, payment ledger
  extraction.py               regex fast-path plus LLM extraction (LangChain + Pydantic)
  validators.py               date normalization, Luhn, lengths, grounding check
  api_client.py               lookup-account / process-payment HTTP client
  templates.py                response templates keyed by message type
  states.py                   the conversation State enum
  prompts/
    extraction_system.txt     system prompt for the LLM extractor
eval/                         two-tier evaluation harness (see EVALUATION.md)
test_runs/                    sample conversations captured from live runs
DESIGN.md                     architecture, key decisions, assumptions
EVALUATION.md                 test cases, metrics, observations
```

## Setup

Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env                    # fill in OPENAI_API_KEY; PAYMENT_API_BASE_URL is pre-filled
```

`.env` loads automatically (python-dotenv) whenever the `payment_agent` package is imported, so the CLI, both evals, and `from agent import Agent` all pick it up. Real environment variables take precedence over `.env`.

The `Agent` constructor takes no arguments (fixed by the assignment), so all config comes from the environment; `.env.sample` lists everything.

- `PAYMENT_API_BASE_URL` is required; the client will not start without it.
- Extraction uses OpenAI by default (`OPENAI_API_KEY`). Set `EXTRACTOR_PROVIDER=anthropic` with `ANTHROPIC_API_KEY` to use Claude.
- The model defaults to `gpt-5-mini` or `claude-haiku-4-5` per provider; override with `EXTRACTOR_MODEL`.
- `MAX_VERIFY_ATTEMPTS`, `MAX_CARD_ATTEMPTS`, and `MAX_API_FAILURES` are required; `.env.sample` ships them at 3.

Clean inputs like the sample dialogue use the regex fast-path and need no LLM key.

Logging is always on: the agent writes `payment_agent.log` in the repo root, recording states, API calls, and outcomes only, never card data or account PII.

## Run

Interactive:

```bash
python cli.py
```

Programmatic, matching the assignment's sample usage:

```python
from agent import Agent

agent = Agent()
print(agent.next("My account ID is ACC1001"))
```

## Test accounts


| Account ID | Full name                     | DOB        | Aadhaar last 4 | Pincode | Balance   |
| ---------- | ----------------------------- | ---------- | -------------- | ------- | --------- |
| ACC1001    | Nithin Jain                   | 1990-05-14 | 4321           | 400001  | ₹1,250.75 |
| ACC1002    | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876           | 400002  | ₹540.00   |
| ACC1003    | Priya Agarwal                 | 1992-08-10 | 2468           | 400003  | ₹0.00     |
| ACC1004    | Rahul Mehta                   | 1988-02-29 | 1357           | 400004  | ₹3,200.50 |


Two of these are quiet edge cases. 1988 is a leap year, so ACC1004's DOB 1988-02-29 is valid and the agent accepts it; a nearby invalid date like 1989-02-29 is rejected at parse time and does not count as a verification mismatch. ACC1003 has no balance, so the agent reports nothing is outstanding and closes without collecting card details.

## Assumptions and limitations

Short version; the full reasoning is in [DESIGN.md](DESIGN.md).

**Assumptions** (where the spec is silent or leaves the choice open):

- Ambiguous numeric dates are read as DD-MM; two-digit years pivot (DOB to the past, card expiry to the future).
- Verification needs the full name plus at least one matching secondary factor (DOB, Aadhaar last 4, or pincode); a wrong factor alongside a correct one still verifies.
- An amount step and a confirmation step were inserted between the spec's listed steps.
- Retry limits, read from the environment: three failed verifications, three rejected cards, or three consecutive API outages each close the session, and an unrecognized decline code is treated as terminal.

**Limitations:**

- Verification is case-sensitive exact equality, with no edit distance and no case folding, as the spec requires, so a name in the wrong case is treated as a mismatch.
- Account data (DOB, Aadhaar, pincode) never enters the LLM context or a reply, rejection messages never reveal which factor mismatched, and raw card data is masked in history and dropped after the payment call.

## Evaluation

[EVALUATION.md](EVALUATION.md) covers the test cases, the two-tier harness, the metrics, and what running it found:

```bash
pytest                        # Tier 1: scripted scenarios against a mocked API, no keys needed
python -m eval.run_live       # Tier 2: an LLM plays the user against the live API
```

Sample conversations in [test_runs/](test_runs/) were captured from live runs.