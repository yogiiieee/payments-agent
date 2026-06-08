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
transcripts/                  sample conversations captured from live runs
DESIGN.md                     architecture, key decisions, assumptions
EVALUATION.md                 test cases, metrics, observations
```

## Setup

Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # requirements-dev.txt adds lint and type tooling
cp .env.sample .env                    # fill in OPENAI_API_KEY; PAYMENT_API_BASE_URL is pre-filled
```

`.env` loads automatically (python-dotenv) whenever the `payment_agent` package is imported, so the CLI, both evals, and `from agent import Agent` all pick it up. Real environment variables take precedence over `.env`.

The `Agent` constructor takes no arguments, as the assignment fixes, so all config comes from the environment. See `.env.sample` for the full list. `PAYMENT_API_BASE_URL` is required and the client refuses to construct without it. `OPENAI_API_KEY` powers LLM extraction. The extraction model defaults to `gpt-5-mini`; override it with `EXTRACTOR_MODEL`. The three retry limits are required and read from the environment (`MAX_VERIFY_ATTEMPTS`, `MAX_CARD_ATTEMPTS`, `MAX_API_FAILURES`); `.env.sample` ships them at 3. Clean, well-formatted inputs like the sample dialogue are handled by a regex fast-path and need no OpenAI key.

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

Short version. The reasoning behind each is in [DESIGN.md](DESIGN.md):

- The test server never persists balance updates, so the agent keeps an in-session ledger (payments made, remaining balance) to prevent double charges and to compute "pay the rest". Payments are not recoverable across sessions.
- Verification matching is case-sensitive exact equality after normalizing spacing and date format. No edit distance and no case folding, since the assignment forbids case-insensitive name matching, so a name in the wrong case is treated as a mismatch. A user is verified by the full name plus at least one matching secondary factor (DOB, Aadhaar last 4, or pincode).
- Three failed verification attempts end the session, as do three rejected cards or three consecutive API outages. An unrecognized payment decline is treated as terminal and closes the session cleanly. Further input gets a polite refusal.
- Each payment carries an idempotency key, reused across a retry, so a timed-out charge can be retried on the user's confirmation without a double charge. The interactive CLI exits once the conversation is closed or locked.
- Ambiguous numeric dates are read as DD-MM.
- Rejection messages never reveal which factor mismatched, and account data (DOB, Aadhaar, pincode) is never placed in the LLM context or echoed to the user.
- Raw card data is masked in conversation history and held only until the payment call completes.

## Evaluation

[EVALUATION.md](EVALUATION.md) covers the test cases, the two-tier harness, the metrics, and what running it found:

```bash
python -m eval.run_scripted   # Tier 1: scripted scenarios against a mocked API, no keys needed
python -m eval.run_live       # Tier 2: an LLM plays the user against the live API
```

Sample conversations in [transcripts/](transcripts/) were captured from live runs.
