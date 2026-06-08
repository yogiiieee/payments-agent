"""File logging for the agent.

Safe by construction: callers log states, intents, attempt counts, masked card
last-4, amounts, transaction IDs, and error codes only. Raw user messages, names,
DOB, Aadhaar, pincode, and full card / CVV / expiry are never written, per the
assignment's rule against logging raw card data or exposing account PII.

Always on: logs go to payment_agent.log in the repo root, at INFO level.
"""

import logging
from pathlib import Path

_LOG_FILE = Path(__file__).parent.parent / "payment_agent.log"
_LOG_LEVEL = "INFO"
_configured = False


def configure_logging() -> None:
    """Attach one file handler to the `payment_agent` logger; safe to call repeatedly."""
    global _configured
    if _configured:
        return

    logger = logging.getLogger("payment_agent")
    logger.setLevel(_LOG_LEVEL)
    logger.propagate = False  # the file handler is the only sink; don't echo to the root logger

    handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    _configured = True
