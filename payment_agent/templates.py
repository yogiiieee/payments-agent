"""Response templates keyed by message type. The LLM never writes user-facing text."""

from decimal import Decimal
from enum import StrEnum, auto


def inr(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def mask_card(last4: str) -> str:
    return f"card ending {last4}"


class Msg(StrEnum):
    GREET = auto()
    ASK_ACCOUNT_ID = auto()
    ACCOUNT_NOT_FOUND = auto()
    ASK_NAME = auto()
    ASK_FACTOR = auto()
    VERIFY_FAILED = auto()
    LOCKED = auto()
    LOCKED_FOLLOWUP = auto()
    INVALID_DATE = auto()
    VERIFIED_BALANCE = auto()
    VERIFIED_ZERO = auto()
    VERIFIED_WITH_AMOUNT = auto()
    AMOUNT_INVALID = auto()
    AMOUNT_EXCEEDS = auto()
    ASK_CARD = auto()
    CARD_PARTIAL = auto()
    CARD_INVALID_NUMBER = auto()
    CARD_INVALID_CVV = auto()
    CARD_INVALID_EXPIRY = auto()
    CARD_EXPIRED = auto()
    CARD_TOO_EARLY = auto()
    CARD_NOTED_NEED_AMOUNT = auto()
    CARD_ATTEMPTS_EXHAUSTED = auto()
    API_GIVE_UP = auto()
    CONFIRM = auto()
    PAYMENT_CANCELLED = auto()
    PAYMENT_SUCCESS = auto()
    PAYMENT_SUCCESS_CLEARED = auto()
    DECLINED_INVALID_CARD = auto()
    DECLINED_INVALID_CVV = auto()
    DECLINED_INVALID_EXPIRY = auto()
    DECLINED_INSUFFICIENT_BALANCE = auto()
    DECLINED_INVALID_AMOUNT = auto()
    DECLINED_TERMINAL = auto()
    PAYMENT_UNKNOWN = auto()
    API_UNAVAILABLE = auto()
    NOTHING_DUE = auto()
    POST_PAYMENT = auto()
    RECAP_CLOSE = auto()
    CLOSE_NO_PAYMENT = auto()
    CLOSED_FOLLOWUP = auto()
    BALANCE_NOT_VERIFIED = auto()
    BALANCE_INFO = auto()
    OFF_TOPIC = auto()
    FALLBACK = auto()
    INTERNAL_ERROR = auto()


TEMPLATES = {
    Msg.GREET: "Hello! I can help you pay your outstanding balance. Please share your account ID to get started.",
    Msg.ASK_ACCOUNT_ID: "I'll need your account ID first. It looks like ACC followed by digits.",
    Msg.ACCOUNT_NOT_FOUND: "I couldn't find an account with that ID. Could you double-check it and share it again?",
    Msg.ASK_NAME: "Got it. Could you please confirm your full name?",
    Msg.ASK_FACTOR: "Thanks. Could you verify one of: your date of birth, the last 4 digits of your Aadhaar, or your pincode?",
    Msg.VERIFY_FAILED: "Those details don't match. You have {attempts_left} attempt(s) left. Please share your full name and one of: date of birth, Aadhaar last 4, or pincode.",
    Msg.LOCKED: "I'm sorry, but verification has failed too many times, so I can't proceed with this session. Please contact support for help with your account.",
    Msg.LOCKED_FOLLOWUP: "This session is closed because verification was unsuccessful. Please contact support.",
    Msg.INVALID_DATE: "That doesn't look like a valid date. Could you share your date of birth again, e.g. 14 May 1990?",
    Msg.VERIFIED_BALANCE: "Identity verified. Your outstanding balance is {balance}. How much would you like to pay today?",
    Msg.VERIFIED_ZERO: "Identity verified. Your outstanding balance is {balance}, so there's nothing to pay. Have a great day!",
    Msg.VERIFIED_WITH_AMOUNT: "Identity verified. Your outstanding balance is {balance}, and you'd mentioned paying {amount}, so we'll go with that. Please share your card number, expiry (MM/YY), CVV, and the name on the card.",
    Msg.AMOUNT_INVALID: "That amount doesn't look right. It needs to be a positive number with at most 2 decimal places. How much would you like to pay?",
    Msg.AMOUNT_EXCEEDS: "That's more than your outstanding balance of {remaining}. How much would you like to pay?",
    Msg.ASK_CARD: "You're paying {amount}. Please share your card number, expiry (MM/YY), CVV, and the name on the card.",
    Msg.CARD_PARTIAL: "Noted. I still need: {missing}.",
    Msg.CARD_INVALID_NUMBER: "That card number doesn't look valid. Could you re-check it and share it again?",
    Msg.CARD_INVALID_CVV: "That CVV doesn't look right. It should be {expected} digits for this card. Could you share it again?",
    Msg.CARD_INVALID_EXPIRY: "I couldn't read that expiry date. Could you share it as MM/YY, e.g. 12/27?",
    Msg.CARD_EXPIRED: "That card appears to be expired. Could you use a different card?",
    Msg.CARD_TOO_EARLY: "I can take card details only at the payment step, after verification. I haven't stored what you sent. {next_prompt}",
    Msg.CARD_NOTED_NEED_AMOUNT: "Got your card details. How much would you like to pay?",
    Msg.CARD_ATTEMPTS_EXHAUSTED: "I wasn't able to take a valid card after several attempts, so I'll stop here, no charge has been made. Please double-check your card details and start a new session, or contact support.",
    Msg.API_GIVE_UP: "I'm having persistent trouble reaching the payment system, so I'll stop here. Please try again a little later. Sorry about that.",
    Msg.CONFIRM: "To confirm: paying {amount} with {card} ({holder}). Shall I go ahead? (yes/no)",
    Msg.PAYMENT_CANCELLED: "No problem, I've cancelled that. How much would you like to pay?",
    Msg.PAYMENT_SUCCESS: "Payment successful! Transaction ID: {txn_id}. You paid {amount}; your remaining balance is {remaining}. Would you like to pay more, or shall we wrap up?",
    Msg.PAYMENT_SUCCESS_CLEARED: "Payment successful! Transaction ID: {txn_id}. You paid {amount} and your balance is fully cleared. Anything else?",
    Msg.DECLINED_INVALID_CARD: "The payment was declined: the card number was rejected. Could you re-check it and share the card details again?",
    Msg.DECLINED_INVALID_CVV: "The payment was declined: the CVV was rejected. Could you share your card details again?",
    Msg.DECLINED_INVALID_EXPIRY: "The payment was declined: the expiry was rejected or the card has expired. Could you share your card details again?",
    Msg.DECLINED_INSUFFICIENT_BALANCE: "The payment was declined: the amount exceeds your outstanding balance of {remaining}. How much would you like to pay?",
    Msg.DECLINED_INVALID_AMOUNT: "The payment was declined: the amount wasn't accepted. How much would you like to pay?",
    Msg.DECLINED_TERMINAL: "The payment couldn't be processed ({code}), and that's not something we can resolve here, so I'll stop. No further charge has been made. Please contact support. Goodbye!",
    Msg.PAYMENT_UNKNOWN: "I couldn't confirm whether that payment went through; the connection timed out. Say \"confirm\" to safely retry (the payment is protected against a double charge), or \"no\" to cancel.",
    Msg.API_UNAVAILABLE: "I'm having trouble reaching the payment system right now. Please try again in a moment.",
    Msg.NOTHING_DUE: "Your balance is fully cleared, so there's nothing left to pay. Anything else?",
    Msg.POST_PAYMENT: "Your remaining balance is {remaining}. Would you like to pay more, or shall we wrap up?",
    Msg.RECAP_CLOSE: "To recap: you paid {total} today ({details}); remaining balance {remaining}. Thanks for your payment. Goodbye!",
    Msg.CLOSE_NO_PAYMENT: "Alright, ending here, no payment was made today. Goodbye!",
    Msg.CLOSED_FOLLOWUP: "This conversation has ended. Please start a new session if you need anything else.",
    Msg.BALANCE_NOT_VERIFIED: "I can share the balance only after verifying your identity. {next_prompt}",
    Msg.BALANCE_INFO: "Your outstanding balance is {remaining}.",
    Msg.OFF_TOPIC: "I can only help with verifying your identity and collecting a payment on your account. {next_prompt}",
    Msg.FALLBACK: "Sorry, I didn't catch that. {next_prompt}",
    Msg.INTERNAL_ERROR: "Sorry, something went wrong on my end. Could you say that again?",
}

assert set(TEMPLATES) == set(Msg), "every Msg member needs a template and vice versa"


def render(key: Msg, **kwargs) -> str:
    return TEMPLATES[key].format(**kwargs)
