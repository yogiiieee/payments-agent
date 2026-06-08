"""The required Agent interface and the per-turn pipeline."""

import logging
import re
from collections import deque

from .api_client import ApiClient, PaymentApi
from .extraction import Extraction, Extractor, LLMExtractor, fast_extract, ground
from .state_machine import Session, handle, prompt_for
from .states import State
from .templates import TEMPLATES, Msg, render
from .validators import digits_only

logger = logging.getLogger(__name__)

_PAN_RE = re.compile(r"(?:\d[ \-]?){12,18}\d")
_CVV_RE = re.compile(r"(?i)\b(?:cvv|cvc|security code)\D{0,5}(\d{3,4})\b")

# Extraction fields safe to name in logs: the field name only, never its value (which may be PII).
_FIELD_NAMES = ("account_id", "full_name", "dob_text", "aadhaar_last4", "pincode",
                "amount", "card_number", "cvv", "expiry_text", "cardholder_name")


def _present_fields(ext: Extraction) -> list[str]:
    return [f for f in _FIELD_NAMES if getattr(ext, f, None) not in (None, "")]


def _mask_card_data(text: str) -> tuple[str, str | None, str | None]:
    """Capture and mask PAN/CVV before the text is stored or sent to the LLM."""
    pan = None

    def _sub(m):
        nonlocal pan
        d = digits_only(m.group())
        if 13 <= len(d) <= 19:
            pan = d
            return f"[card ending {d[-4:]}]"
        return m.group()

    masked = _PAN_RE.sub(_sub, text)
    cvv = None
    m = _CVV_RE.search(masked)
    if m:
        cvv = m.group(1)
        masked = masked[: m.start(1)] + "[cvv]" + masked[m.end(1):]
    return masked, pan, cvv


class Agent:
    def __init__(self, api_client: PaymentApi | None = None,
                 extractor: Extractor | None = None):
        self.api: PaymentApi = api_client or ApiClient()
        self.extractor: Extractor = extractor or LLMExtractor()
        self.session = Session()
        # last 3 masked exchanges, used as extractor context; account PII never appears here
        self._history: deque[str] = deque(maxlen=6)
        self._last_masked = ""
        self._turns = 0

    def next(self, user_input: str) -> dict:
        self._turns += 1
        try:
            message = self._turn(str(user_input))
        except Exception:
            logger.exception("turn %d crashed; returning the internal-error reply", self._turns)
            message = TEMPLATES[Msg.INTERNAL_ERROR]  # this interface must never raise
        self._history.append(f"user: {self._last_masked}")
        self._history.append(f"agent: {message}")
        return {"message": message}

    def _turn(self, text: str) -> str:
        s = self.session
        masked, pan, cvv = _mask_card_data(text)
        self._last_masked = masked

        if s.state == State.CLOSED:
            return TEMPLATES[Msg.CLOSED_FOLLOWUP]
        if s.state == State.LOCKED:
            return TEMPLATES[Msg.LOCKED_FOLLOWUP]

        ext = fast_extract(masked, s.state)
        source = "regex"
        if ext is None:
            context = ""
            if self._history:
                context = ("Recent conversation (context only — never a source of values):\n"
                           + "\n".join(self._history))
            ext = self.extractor.extract(masked, context)
            if ext is None:
                if not (pan or cvv):
                    logger.info("turn=%d state=%s source=none -> fallback",
                                self._turns, s.state.name)
                    return render(Msg.FALLBACK, next_prompt=prompt_for(s))
                ext = Extraction()
                source = "card-prepass"
            else:
                ext = ground(ext, masked)
                source = "llm"
        # pre-pass values are literal; they outrank anything the LLM produced
        if pan:
            ext.card_number = pan
        if cvv:
            ext.cvv = cvv

        logger.info("turn=%d state=%s source=%s intent=%s fields=%s",
                    self._turns, s.state.name, source, ext.intent, _present_fields(ext))
        key, kwargs = handle(s, ext, self.api)
        logger.info("turn=%d -> state=%s reply=%s", self._turns, s.state.name, key.value)
        return render(key, **kwargs)
