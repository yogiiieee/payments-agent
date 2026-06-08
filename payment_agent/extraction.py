"""Turn parsing: a regex fast-path, then an LLM extractor (LangChain + Pydantic)."""

import os
import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from .states import State
from .validators import (
    digits_only,
    grounded_digits,
    grounded_text,
    plausible_name,
    words_to_digits,
)


class Extraction(BaseModel):
    intent: Literal[
        "provide_info", "question", "confirm_yes", "confirm_no",
        "pay_full", "smalltalk", "quit", "other",
    ] = "other"
    account_id: str | None = None
    full_name: str | None = None
    dob_text: str | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None
    amount: float | None = None
    card_number: str | None = None
    cvv: str | None = None
    expiry_text: str | None = None
    cardholder_name: str | None = None


# --- layer 1: regexes; on match the LLM is skipped ---

_GREET = re.compile(r"^(hi+|hello|hey|good (morning|afternoon|evening))[!. ]*$", re.I)
_QUIT = re.compile(
    r"^(bye|goodbye|quit|exit|no thanks|that'?s (all|it)|thanks?,? ?bye|thank you,? ?bye"
    r"|(let'?s )?wrap (it )?up|i'?m done|all done|done)[!. ]*$", re.I)
_ACC = re.compile(r"\bacc[\s\-]*(\d{3,})\b", re.I)
_DOB_PREFIX = re.compile(r"^(?:my\s+)?(?:dob|date of birth)\s*(?:is|:)?\s*(.+?)[.! ]*$", re.I)
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})$")
_AMOUNT = re.compile(r"^(?:₹|rs\.?\s*)?(\d+(?:\.\d{1,2})?)(?:\s*(?:rs|rupees))?[.! ]*$", re.I)
_FULL = re.compile(r"^(?:the\s+)?(?:full|entire|whole)(?:\s+(?:amount|balance))?[.! ]*$", re.I)
_YES = re.compile(r"^(y|yes|yeah|yep|sure|ok|okay|confirm|go ahead|do it|proceed)[!. ]*$", re.I)
_NO = re.compile(r"^(n|no|nope|cancel|stop|don'?t)[!. ]*$", re.I)
_NAME = re.compile(r"^[A-Za-z]+(?:\s+[A-Za-z]+){1,3}$")


def fast_extract(text: str, state: State) -> Extraction | None:
    t = text.strip()
    if not t:
        return Extraction(intent="other")
    if _QUIT.match(t):
        return Extraction(intent="quit")
    if _GREET.match(t):
        return Extraction(intent="smalltalk")
    m = _ACC.search(t)
    # short message whose only digits belong to the ID — "its acc 1001", "account id: acc1001"
    if m and len(t.split()) <= 8 and digits_only(t) == m.group(1):
        return Extraction(intent="provide_info", account_id="ACC" + m.group(1))

    if state is State.AWAIT_NAME and _NAME.match(t) and plausible_name(t):
        return Extraction(intent="provide_info", full_name=t)

    if state is State.AWAIT_FACTOR:
        m = _DOB_PREFIX.match(t)
        if m and _DATE.match(m.group(1).strip()):
            return Extraction(intent="provide_info", dob_text=m.group(1).strip())
        if _DATE.match(t):
            return Extraction(intent="provide_info", dob_text=t)
        # bare, spaced, or spoken digits — "4321", "4 0 0 0 0 1", "four three two one"
        m = re.fullmatch(r"(?:it'?s\s+)?([\d\s]+)", words_to_digits(t), re.I)
        if m:
            d = digits_only(m.group(1))
            if len(d) == 6:
                return Extraction(intent="provide_info", pincode=d)
            if len(d) == 4:
                return Extraction(intent="provide_info", aadhaar_last4=d)

    if state in (State.AWAIT_AMOUNT, State.POST_PAYMENT):
        m = _AMOUNT.match(t)
        if m:
            return Extraction(intent="provide_info", amount=float(m.group(1)))
        if _FULL.match(t):
            return Extraction(intent="pay_full")

    if state is State.AWAIT_CARD:
        if re.fullmatch(r"\d{1,2}\s*/\s*\d{2,4}", t):
            return Extraction(intent="provide_info", expiry_text=t)
        spoken = digits_only(words_to_digits(t))
        # a bare 3-4 digit answer (typed or spoken) while collecting card fields is the CVV
        if re.fullmatch(r"[\d\s]+", words_to_digits(t)) and len(spoken) in (3, 4):
            return Extraction(intent="provide_info", cvv=spoken)

    if state is State.AWAIT_CONFIRM:
        # recognize agreement or refusal even inside a longer sentence
        neg = re.search(r"\b(no|nope|cancel|stop|wait|don'?t|hold on|different|change)\b", t, re.I)
        aff = re.search(r"\b(yes|yeah|yep|yup|sure|okay|ok|confirm|go ahead|proceed|do it|charge)\b",
                        t, re.I)
        if neg and not aff:
            return Extraction(intent="confirm_no")
        if aff and not neg:
            return Extraction(intent="confirm_yes")
        if _YES.match(t):  # bare "y"
            return Extraction(intent="confirm_yes")
        if _NO.match(t):  # bare "n"
            return Extraction(intent="confirm_no")

    return None


# --- LLM extractor ---

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "extraction_system.txt").read_text(encoding="utf-8")

# Per-provider default; EXTRACTOR_MODEL overrides. Both are cheap structured-output models.
_DEFAULT_MODEL = {"openai": "gpt-5-mini", "anthropic": "claude-haiku-4-5"}


class Extractor(Protocol):
    """Turn parser surface; satisfied structurally by LLMExtractor and test fakes."""

    def extract(self, text: str, context: str) -> Extraction | None: ...


class LLMExtractor:
    """Structured extraction via LangChain, on OpenAI or Anthropic.

    EXTRACTOR_PROVIDER selects the backend (openai | anthropic); EXTRACTOR_MODEL
    overrides the model. Both backends expose with_structured_output(Extraction),
    so only the client construction differs; the rest of the pipeline is identical.
    """

    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = (provider or os.getenv("EXTRACTOR_PROVIDER") or "openai").lower()
        if self.provider not in _DEFAULT_MODEL:
            self.provider = "openai"
        self.model = model or os.getenv("EXTRACTOR_MODEL") or _DEFAULT_MODEL[self.provider]
        self._runnable: Any | None = None  # lazy, so Agent() constructs without an API key

    def _structured_llm(self):
        # bounded timeout so a stalled call degrades to a re-ask, never a hang
        if self.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(model=self.model, timeout=20, max_retries=2)
        else:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=self.model, timeout=20, max_retries=2)
        return llm.with_structured_output(Extraction)

    def extract(self, text: str, context: str) -> Extraction | None:
        try:
            if self._runnable is None:
                self._runnable = self._structured_llm()
            prompt = f"{context}\n\nUser message: {text}" if context else f"User message: {text}"
            result = self._runnable.invoke([("system", _SYSTEM_PROMPT), ("human", prompt)])
            return result if isinstance(result, Extraction) else None
        except Exception:
            return None  # the agent degrades to a re-ask; it must never crash


def ground(ext: Extraction, message: str) -> Extraction:
    """Drop any extracted value whose content doesn't actually occur in the message."""
    # spoken numbers ("one hundred twenty three") have no digits to match against
    has_digits = any(ch.isdigit() for ch in message)
    if has_digits and ext.account_id and not grounded_digits(ext.account_id, message):
        ext.account_id = None
    for field in ("aadhaar_last4", "pincode", "cvv", "card_number"):
        value = getattr(ext, field)
        if has_digits and value and not grounded_digits(value, message):
            setattr(ext, field, None)
    for field in ("full_name", "cardholder_name", "dob_text", "expiry_text"):
        value = getattr(ext, field)
        if value and not grounded_text(value, message):
            setattr(ext, field, None)
    return ext
