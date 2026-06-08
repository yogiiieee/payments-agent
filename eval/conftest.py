"""The retry limits are required env vars (no defaults in code). Supply them before
payment_agent is imported so the scripted suite stays key-free and runs with no .env."""

import os

for _name in ("MAX_VERIFY_ATTEMPTS", "MAX_CARD_ATTEMPTS", "MAX_API_FAILURES"):
    os.environ.setdefault(_name, "3")
