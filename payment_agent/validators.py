"""Normalization, parsing, and validation helpers. All pure functions."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

_WORD_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def words_to_digits(text: str) -> str:
    """Replace spoken digit words so 'one two three' grounds as '123'."""
    return re.sub(
        r"\b(" + "|".join(_WORD_DIGITS) + r")\b",
        lambda m: _WORD_DIGITS[m.group(1).lower()],
        text,
        flags=re.IGNORECASE,
    )


def canonical_name(name: str) -> str:
    # Collapse only surrounding and repeated whitespace. Case is significant: the
    # assignment forbids case-insensitive name matching, so the compare stays case-exact.
    return " ".join(name.split())


_NAME_LEADIN = re.compile(
    r"^(?:it'?s|i'?m|i am|my (?:full )?name is|this is|call me|name[:\-])\s+", re.IGNORECASE
)

# words that never appear in a stated name; a claim containing one is a misextraction
NAME_STOPWORDS = {
    "yes", "no", "yeah", "ok", "okay", "hi", "hello", "hey", "what", "why", "how",
    "wait", "huh", "sure", "thanks", "thank", "bye", "stop", "cancel", "help", "dont",
    "good", "umm", "hmm", "i", "im", "its", "not", "quit", "exit", "done", "finish",
    "finished", "wrap", "up", "lets", "please", "sorry", "card", "account", "balance",
    "pay", "payment", "amount", "full", "name", "on", "is", "the", "my", "me", "a",
}


def plausible_name(text: str) -> bool:
    """A name claim must look like a name before it may reach the attempt counter."""
    words = text.split()
    if not 1 <= len(words) <= 6:
        return False
    for word in words:
        if not re.fullmatch(r"[A-Za-z][A-Za-z'.\-]*", word):
            return False
        if word.strip("'.-").lower() in NAME_STOPWORDS:
            return False
    return True


def clean_name_claim(text: str) -> str:
    """Strip presentation the LLM sometimes leaves on a name ("it's X", nickname commas).

    Surfaced by live evals: the extractor occasionally returns the whole utterance as
    full_name. Cleanup is deterministic and touches only the user's claim — never the
    on-file value — so strict matching is preserved.
    """
    t = text.strip().strip(".!")
    prev = None
    while prev != t:
        prev = t
        t = _NAME_LEADIN.sub("", t).strip()
    if "," in t:
        t = max((seg.strip() for seg in t.split(",")), key=len)
    return t


def parse_dob(text: str) -> date:
    """Parse a date of birth. Raises ValueError on anything invalid (e.g. 1989-02-29)."""
    text = text.strip().rstrip(".,!")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        parsed = dateparser.parse(text, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"unparseable date: {text!r}") from exc
    if parsed.year > date.today().year:  # two-digit DOB years pivot to the past
        parsed = parsed.replace(year=parsed.year - 100)
    return parsed


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
_MONTHS.update({m[:3]: i for m, i in _MONTHS.items()})


def parse_expiry(text: str) -> tuple[int, int]:
    """'12/27', '12/2027', 'December 2027' -> (12, 2027). Raises ValueError."""
    text = text.strip().rstrip(".,!").lower()  # the LLM sometimes keeps trailing punctuation
    m = re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{2}|\d{4})", text)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"([a-z]+)\.?,?\s+(\d{2}|\d{4})", text)
        if not m or m.group(1) not in _MONTHS:
            raise ValueError(f"unparseable expiry: {text!r}")
        month, year = _MONTHS[m.group(1)], int(m.group(2))
    if year < 100:
        year += 2000  # expiry years pivot forward, unlike DOB
    if not 1 <= month <= 12:
        raise ValueError(f"invalid expiry month: {month}")
    return month, year


def expiry_in_past(month: int, year: int) -> bool:
    today = date.today()
    return (year, month) < (today.year, today.month)  # valid through end of month


def luhn_ok(pan: str) -> bool:
    digits = [int(d) for d in pan]
    odd = digits[-1::-2]
    even = [sum(divmod(d * 2, 10)) for d in digits[-2::-2]]
    return (sum(odd) + sum(even)) % 10 == 0


def cvv_length_for(pan: str) -> int:
    return 4 if pan.startswith(("34", "37")) else 3  # Amex


def parse_amount(value) -> Decimal:
    """Positive, at most 2 decimal places. Raises ValueError."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"unparseable amount: {value!r}") from exc
    if amount <= 0:
        raise ValueError("amount must be positive")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("amount has more than 2 decimal places")
    return amount.quantize(Decimal("0.01"))


def grounded_digits(value: str, message: str) -> bool:
    """Anti-hallucination check: the digits the LLM extracted must occur in the message."""
    return digits_only(value) in digits_only(words_to_digits(message))


def grounded_text(value: str, message: str) -> bool:
    return value.casefold().strip() in message.casefold()
