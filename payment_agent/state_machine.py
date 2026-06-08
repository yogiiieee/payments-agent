"""States, transitions, verification, and the payment ledger."""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import uuid4

from .api_client import (
    Account,
    AccountNotFound,
    APIUnavailable,
    PaymentApi,
    PaymentDeclined,
    PaymentOutcomeUnknown,
)
from .extraction import Extraction
from .states import State
from .templates import Msg, inr, mask_card, render
from .validators import (
    canonical_name,
    clean_name_claim,
    cvv_length_for,
    digits_only,
    expiry_in_past,
    luhn_ok,
    parse_amount,
    parse_dob,
    parse_expiry,
    plausible_name,
    words_to_digits,
)

MAX_VERIFY_ATTEMPTS = abs(int(os.environ["MAX_VERIFY_ATTEMPTS"]))
MAX_CARD_ATTEMPTS = abs(int(os.environ["MAX_CARD_ATTEMPTS"]))
MAX_API_FAILURES = abs(int(os.environ["MAX_API_FAILURES"]))

logger = logging.getLogger(__name__)


@dataclass
class Claims:
    full_name: str | None = None
    dob: date | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None

    def clear(self):
        self.full_name = self.dob = self.aadhaar_last4 = self.pincode = None


@dataclass
class Card:
    number: str | None = None
    cvv: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None
    holder: str | None = None

    def wipe(self):
        self.number = self.cvv = self.expiry_month = self.expiry_year = self.holder = None

    def missing(self) -> list[str]:
        out = []
        if not self.number:
            out.append("card number")
        if self.expiry_month is None:
            out.append("expiry (MM/YY)")
        if not self.cvv:
            out.append("CVV")
        if not self.holder:
            out.append("name on the card")
        return out


@dataclass
class Session:
    state: State = State.AWAIT_ACCOUNT_ID
    greeted: bool = False
    account: Account | None = None   # validated lookup response; never placed in any LLM prompt
    claims: Claims = field(default_factory=Claims)
    verify_attempts: int = 0
    card_attempts: int = 0
    api_failures: int = 0
    verified: bool = False
    amount: Decimal | None = None
    early_amount: Decimal | None = None
    card: Card = field(default_factory=Card)
    idempotency_key: str | None = None  # stable across retries of one charge; cleared when it ends
    ledger: list = field(default_factory=list)  # (amount, txn_id) — server forgets payments

    @property
    def remaining(self) -> Decimal:
        assert self.account is not None  # meaningful only after a successful lookup
        return self.account.balance - sum((a for a, _ in self.ledger), Decimal("0"))


@dataclass
class _Turn:
    """Values extracted this turn but not yet accepted into the session."""
    notes: set
    account_id: str | None = None
    amount: Decimal | None = None
    pay_full: bool = False


_CARD_STATES = (State.AWAIT_AMOUNT, State.AWAIT_CARD, State.AWAIT_CONFIRM)


def _ingest(s: Session, ext: Extraction) -> _Turn:
    turn = _Turn(notes=set())

    if ext.account_id:
        cleaned = re.sub(r"[\s\-]", "", ext.account_id).upper()
        if re.fullmatch(r"ACC\d+", cleaned):
            turn.account_id = cleaned

    if not s.verified:
        if ext.full_name:
            cleaned = clean_name_claim(ext.full_name)
            if plausible_name(cleaned):  # misextractions must not become claims
                s.claims.full_name = cleaned
        if ext.dob_text:
            try:
                s.claims.dob = parse_dob(ext.dob_text)
            except ValueError:
                turn.notes.add("invalid_date")
        # assign by length: guards against the LLM swapping aadhaar/pincode fields
        for raw in (ext.aadhaar_last4, ext.pincode):
            if raw:
                d = digits_only(raw)
                if len(d) == 4:
                    s.claims.aadhaar_last4 = d
                elif len(d) == 6:
                    s.claims.pincode = d

    if ext.intent == "pay_full":
        turn.pay_full = True
    if ext.amount is not None:
        try:
            turn.amount = parse_amount(ext.amount)
        except ValueError:
            turn.notes.add("invalid_amount")
    if turn.amount is not None and not s.verified:
        s.early_amount = turn.amount  # remembered, applied after verification

    has_card = any([ext.card_number, ext.cvv, ext.expiry_text, ext.cardholder_name])
    if has_card and s.verified and s.state in _CARD_STATES:
        _ingest_card(s, ext, turn.notes)
    elif has_card:
        # never stored before the payment step; tell the user only if that's all they sent
        has_other = any([turn.account_id, ext.full_name, ext.dob_text,
                         ext.aadhaar_last4, ext.pincode, ext.amount is not None, turn.pay_full])
        if not has_other:
            turn.notes.add("card_early")

    return turn


def _ingest_card(s: Session, ext: Extraction, notes: set):
    if ext.card_number:
        d = digits_only(words_to_digits(ext.card_number))
        if 13 <= len(d) <= 19 and luhn_ok(d):
            s.card.number = d
        elif len(d) >= 12:
            notes.add("bad_card_number")  # a real but invalid card attempt
        # fewer than 12 digits is a reference ("card ending 0366") or partial input,
        # not a wrong card; ignore it so it never burns a card attempt
    if ext.cvv:
        d = digits_only(words_to_digits(ext.cvv))
        if len(d) in (3, 4):
            s.card.cvv = d
        else:
            notes.add("bad_cvv")
    if ext.expiry_text:
        try:
            month, year = parse_expiry(ext.expiry_text)
            if expiry_in_past(month, year):
                notes.add("card_expired")
            else:
                s.card.expiry_month, s.card.expiry_year = month, year
        except ValueError:
            notes.add("bad_expiry")
    if ext.cardholder_name:
        s.card.holder = ext.cardholder_name


def prompt_for(s: Session) -> str:
    """The re-ask text for the current state (used as a suffix in generic replies)."""
    if s.state == State.AWAIT_ACCOUNT_ID:
        return render(Msg.ASK_ACCOUNT_ID)
    if s.state == State.AWAIT_NAME:
        return render(Msg.PROMPT_NAME)
    if s.state == State.AWAIT_FACTOR:
        return render(Msg.ASK_FACTOR)
    if s.state == State.AWAIT_AMOUNT:
        return render(Msg.PROMPT_AMOUNT)
    if s.state == State.AWAIT_CARD:
        assert s.amount is not None
        return render(Msg.ASK_CARD, amount=inr(s.amount))
    if s.state == State.AWAIT_CONFIRM:
        assert s.amount is not None and s.card.number is not None
        return render(Msg.CONFIRM, amount=inr(s.amount),
                      card=mask_card(s.card.number[-4:]), holder=s.card.holder)
    if s.state == State.POST_PAYMENT:
        return render(Msg.POST_PAYMENT, remaining=inr(s.remaining))
    return ""


def handle(s: Session, ext: Extraction, api: PaymentApi) -> tuple[Msg, dict]:
    if ext.intent == "quit":
        return _close(s)

    turn = _ingest(s, ext)

    if "invalid_date" in turn.notes and not s.verified:
        return Msg.INVALID_DATE, {}  # parse failure, not a mismatch; no attempt consumed
    if "card_early" in turn.notes:
        return Msg.CARD_TOO_EARLY, {"next_prompt": prompt_for(s)}
    if turn.account_id and (s.account is None or turn.account_id != s.account.account_id):
        return _lookup(s, turn.account_id, api)

    # data in the turn outranks question phrasing ("can I do 500 for now?")
    actionable = (turn.amount is not None or turn.pay_full or any([
        ext.full_name, ext.dob_text, ext.aadhaar_last4, ext.pincode,
        ext.card_number, ext.cvv, ext.expiry_text, ext.cardholder_name,
    ]))
    if ext.intent == "question" and not actionable:
        if s.verified:
            return Msg.BALANCE_INFO, {"remaining": inr(s.remaining)}
        return Msg.BALANCE_NOT_VERIFIED, {"next_prompt": prompt_for(s)}

    # One message may satisfy several steps (amount and card together): a step returns
    # None to advance to the next state's step. Bounded by the state count so it can't hang.
    for _ in range(len(State)):
        result = _STEPS[s.state](s, turn, ext, api)
        if result is not None:
            return result
    return Msg.FALLBACK, {"next_prompt": prompt_for(s)}


# Per-state handlers, keyed in _STEPS below.
_StepResult = tuple[Msg, dict] | None


def _step_account_id(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    if not s.greeted:
        s.greeted = True
        return Msg.GREET, {}
    return Msg.ASK_ACCOUNT_ID, {}


def _step_verify(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    return _verify_progress(s)


def _step_post_payment(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    if turn.amount is None and not turn.pay_full:
        if ext.intent == "confirm_no":
            return _close(s)
        return Msg.POST_PAYMENT, {"remaining": inr(s.remaining)}
    if s.remaining <= 0:
        return Msg.NOTHING_DUE, {}
    s.state = State.AWAIT_AMOUNT
    return None


def _step_amount(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    if turn.pay_full:
        s.amount = s.remaining
    elif turn.amount is not None:
        if turn.amount > s.remaining:
            return Msg.AMOUNT_EXCEEDS, {"remaining": inr(s.remaining)}
        s.amount = turn.amount
    elif "invalid_amount" in turn.notes:
        return Msg.AMOUNT_INVALID, {}
    elif ext.intent == "confirm_no":
        return _close(s)  # declining to pay before a payment closes cleanly
    elif any([ext.card_number, ext.cvv, ext.expiry_text, ext.cardholder_name]):
        return Msg.CARD_NOTED_NEED_AMOUNT, {}  # card arrived before the amount; it's stored
    else:
        return Msg.FALLBACK, {"next_prompt": prompt_for(s)}
    s.state = State.AWAIT_CARD
    return None  # the same message may already carry card details


def _step_card(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    return _card_progress(s, turn.notes)


def _step_confirm(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    if ext.intent == "confirm_no":
        s.card.wipe()
        s.amount = None
        s.idempotency_key = None
        s.state = State.AWAIT_AMOUNT
        return Msg.PAYMENT_CANCELLED, {}
    if ext.intent == "confirm_yes":
        return _process_payment(s, api)
    if turn.amount is not None and turn.amount <= s.remaining:
        s.amount = turn.amount        # amount changed mid-confirm; re-confirm below
        s.idempotency_key = None      # a different amount is a different charge
    return _card_progress(s, turn.notes)


def _step_terminal(s: Session, turn: "_Turn", ext: Extraction, api: PaymentApi) -> _StepResult:
    # Reached only defensively; the agent short-circuits CLOSED/LOCKED before handle().
    return (Msg.LOCKED_FOLLOWUP if s.state == State.LOCKED else Msg.CLOSED_FOLLOWUP), {}


_STEPS = {
    State.AWAIT_ACCOUNT_ID: _step_account_id,
    State.AWAIT_NAME: _step_verify,
    State.AWAIT_FACTOR: _step_verify,
    State.POST_PAYMENT: _step_post_payment,
    State.AWAIT_AMOUNT: _step_amount,
    State.AWAIT_CARD: _step_card,
    State.AWAIT_CONFIRM: _step_confirm,
    State.CLOSED: _step_terminal,
    State.LOCKED: _step_terminal,
}


def _lookup(s: Session, account_id: str, api: PaymentApi) -> tuple[Msg, dict]:
    try:
        account = api.lookup_account(account_id)
    except AccountNotFound:
        return Msg.ACCOUNT_NOT_FOUND, {}
    except APIUnavailable:
        return _api_failure(s)
    s.api_failures = 0  # the cap is for consecutive failures
    if s.account is not None:
        # reset
        s.claims.clear()
        s.verified = False
        s.amount = s.early_amount = None
        s.card.wipe()
        s.idempotency_key = None
        s.ledger.clear()
    s.account = account
    s.state = State.AWAIT_NAME
    return _verify_progress(s)


def _verify_progress(s: Session) -> tuple[Msg, dict]:
    claims, account = s.claims, s.account
    assert account is not None  # only reachable after a successful lookup
    if not claims.full_name:
        s.state = State.AWAIT_NAME
        return Msg.ASK_NAME, {}
    if canonical_name(claims.full_name) != canonical_name(account.full_name):
        return _fail_attempt(s)
    provided = {
        "dob": claims.dob.isoformat() if claims.dob else None,
        "aadhaar_last4": claims.aadhaar_last4,
        "pincode": claims.pincode,
    }
    provided = {k: v for k, v in provided.items() if v}
    if not provided:
        s.state = State.AWAIT_FACTOR
        return Msg.ASK_FACTOR, {}
    if not any(v == getattr(account, k) for k, v in provided.items()):
        return _fail_attempt(s)
    s.verified = True  # name plus at least one matching factor, per the spec
    logger.info("verification passed for %s", account.account_id)
    return _share_balance(s)


def _fail_attempt(s: Session) -> tuple[Msg, dict]:
    s.verify_attempts += 1
    s.claims.clear()  # wrong values must not linger into the next attempt
    if s.verify_attempts >= MAX_VERIFY_ATTEMPTS:
        s.state = State.LOCKED
        logger.warning("session locked after %d failed verification attempts", s.verify_attempts)
        return Msg.LOCKED, {}
    logger.info("verification failed (attempt %d/%d)", s.verify_attempts, MAX_VERIFY_ATTEMPTS)
    s.state = State.AWAIT_NAME
    return Msg.VERIFY_FAILED, {"attempts_left": MAX_VERIFY_ATTEMPTS - s.verify_attempts}


def _card_failure(s: Session, key: Msg, kwargs: dict | None = None) -> tuple[Msg, dict]:
    """A provided card was rejected (locally or by the server). Missing fields don't count."""
    s.card_attempts += 1
    if s.card_attempts >= MAX_CARD_ATTEMPTS:
        s.state = State.CLOSED
        logger.warning("session closed: %d card attempts exhausted", s.card_attempts)
        return Msg.CARD_ATTEMPTS_EXHAUSTED, {}
    logger.info("card rejected (attempt %d/%d)", s.card_attempts, MAX_CARD_ATTEMPTS)
    return key, kwargs or {}


def _api_failure(s: Session) -> tuple[Msg, dict]:
    s.api_failures += 1
    if s.api_failures >= MAX_API_FAILURES:
        s.state = State.CLOSED
        logger.warning("session closed: API gave up after %d consecutive failures", s.api_failures)
        return Msg.API_GIVE_UP, {}
    logger.warning("API unavailable (failure %d/%d)", s.api_failures, MAX_API_FAILURES)
    return Msg.API_UNAVAILABLE, {}


def _share_balance(s: Session) -> tuple[Msg, dict]:
    if s.remaining <= 0:
        s.state = State.CLOSED
        return Msg.VERIFIED_ZERO, {"balance": inr(s.remaining)}
    if s.early_amount is not None and s.early_amount <= s.remaining:
        s.amount, s.early_amount = s.early_amount, None
        s.state = State.AWAIT_CARD
        return Msg.VERIFIED_WITH_AMOUNT, {"balance": inr(s.remaining), "amount": inr(s.amount)}
    s.early_amount = None
    s.state = State.AWAIT_AMOUNT
    return Msg.VERIFIED_BALANCE, {"balance": inr(s.remaining)}


def _card_progress(s: Session, notes: set) -> tuple[Msg, dict]:
    if "bad_card_number" in notes:
        return _card_failure(s, Msg.CARD_INVALID_NUMBER)
    if "card_expired" in notes:
        return _card_failure(s, Msg.CARD_EXPIRED)
    if "bad_expiry" in notes:
        return _card_failure(s, Msg.CARD_INVALID_EXPIRY)
    if "bad_cvv" in notes:
        expected = cvv_length_for(s.card.number) if s.card.number else "3 or 4"
        return _card_failure(s, Msg.CARD_INVALID_CVV, {"expected": expected})
    card = s.card
    if card.number and card.cvv and len(card.cvv) != cvv_length_for(card.number):
        card.cvv = None
        return _card_failure(s, Msg.CARD_INVALID_CVV, {"expected": cvv_length_for(card.number)})
    assert s.amount is not None
    missing = card.missing()
    if len(missing) == 4:
        return Msg.ASK_CARD, {"amount": inr(s.amount)}
    if missing:
        return Msg.CARD_PARTIAL, {"missing": ", ".join(missing)}
    assert card.number is not None
    s.state = State.AWAIT_CONFIRM
    return Msg.CONFIRM, {"amount": inr(s.amount),
                         "card": mask_card(card.number[-4:]), "holder": card.holder}


def _process_payment(s: Session, api: PaymentApi) -> tuple[Msg, dict]:
    card = s.card
    assert s.account is not None and s.amount is not None
    amount = s.amount
    if s.idempotency_key is None:
        s.idempotency_key = uuid4().hex  # one key per charge, reused across any retry
    try:
        txn_id = api.process_payment(s.account.account_id, amount, {
            "holder": card.holder, "number": card.number, "cvv": card.cvv,
            "expiry_month": card.expiry_month, "expiry_year": card.expiry_year,
        }, s.idempotency_key)
    except PaymentDeclined as exc:
        return _declined(s, exc.error_code)
    except PaymentOutcomeUnknown:
        # keep the key so a confirm-retry can't double-charge a payment that may have landed
        logger.warning("payment outcome unknown (timeout); idempotency key retained")
        return Msg.PAYMENT_UNKNOWN, {}
    except APIUnavailable:
        return _api_failure(s)

    s.ledger.append((amount, txn_id))
    s.card.wipe()  # raw card data lives no longer than the API call
    s.amount = None
    s.idempotency_key = None  # this charge is settled; the next one gets a fresh key
    s.card_attempts = 0  # fresh budget for a possible next payment
    s.api_failures = 0
    s.state = State.POST_PAYMENT
    logger.info("payment settled: %s amount=%s txn=%s", s.account.account_id, amount, txn_id)
    if s.remaining <= 0:
        return Msg.PAYMENT_SUCCESS_CLEARED, {"txn_id": txn_id, "amount": inr(amount)}
    return Msg.PAYMENT_SUCCESS, {"txn_id": txn_id, "amount": inr(amount),
                               "remaining": inr(s.remaining)}


def _declined(s: Session, code: str) -> tuple[Msg, dict]:
    s.idempotency_key = None
    kwargs = {"remaining": inr(s.remaining), "code": code}
    if code in ("invalid_card", "invalid_cvv", "invalid_expiry"):
        s.card.wipe()
        s.state = State.AWAIT_CARD  # user-fixable card problem: re-collect the card
        logger.info("payment declined (%s): re-collecting card", code)
        return _card_failure(s, Msg(f"declined_{code}"), kwargs)
    if code in ("insufficient_balance", "invalid_amount"):
        s.amount = None
        s.state = State.AWAIT_AMOUNT  # user-fixable: re-ask the amount
        logger.info("payment declined (%s): re-asking amount", code)
        return Msg(f"declined_{code}"), kwargs
    # an unrecognized decline is not something the user can fix: close cleanly
    s.state = State.CLOSED
    logger.warning("payment declined (%s): terminal, closing session", code)
    return Msg.DECLINED_TERMINAL, {"code": code}


def _close(s: Session) -> tuple[Msg, dict]:
    s.state = State.CLOSED
    if s.ledger:
        total = sum((a for a, _ in s.ledger), Decimal("0"))
        details = "; ".join(f"{inr(a)} (txn {t})" for a, t in s.ledger)
        logger.info("session closed after %d payment(s)", len(s.ledger))
        return Msg.RECAP_CLOSE, {"total": inr(total), "details": details,
                               "remaining": inr(s.remaining)}
    logger.info("session closed with no payment")
    return Msg.CLOSE_NO_PAYMENT, {}
