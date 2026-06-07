"""Conversation steps, shared by the state machine and the extraction fast-path."""

from enum import Enum, auto


class State(Enum):
    AWAIT_ACCOUNT_ID = auto()
    AWAIT_NAME = auto()
    AWAIT_FACTOR = auto()
    AWAIT_AMOUNT = auto()
    AWAIT_CARD = auto()
    AWAIT_CONFIRM = auto()
    POST_PAYMENT = auto()
    CLOSED = auto()
    LOCKED = auto()
