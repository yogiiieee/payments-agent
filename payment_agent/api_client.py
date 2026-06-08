"""HTTP client for the lookup and payment endpoints."""

import os
from decimal import Decimal
from typing import Any, Protocol

import requests
from pydantic import BaseModel, ValidationError, field_validator


class Account(BaseModel):
    """Validated lookup-account response: typed access, malformed payloads fail at the edge."""

    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: Decimal

    @field_validator("balance", mode="before")
    @classmethod
    def _balance_to_decimal(cls, v: object) -> Decimal:
        return Decimal(str(v))  # via str() so a JSON float keeps its exact value


class PaymentApi(Protocol):
    """The API surface the state machine needs; satisfied structurally, mocks included."""

    def lookup_account(self, account_id: str) -> Account: ...

    def process_payment(self, account_id: str, amount: Decimal, card: dict,
                        idempotency_key: str) -> str: ...


class AccountNotFound(Exception):
    pass


class PaymentDeclined(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


class PaymentOutcomeUnknown(Exception):
    """Timed out after sending — the charge may or may not have happened."""


class APIUnavailable(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        resolved = base_url or os.getenv("PAYMENT_API_BASE_URL")
        if not resolved:
            # fail at construction with a pointer, not mid-conversation with an apology
            raise ValueError("PAYMENT_API_BASE_URL is not set — copy .env.sample and export it")
        self.base_url = resolved.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def lookup_account(self, account_id: str) -> Account:
        last_exc: Exception | None = None
        for _ in range(3):  # lookup is read-only, safe to retry
            try:
                resp = self.session.post(
                    f"{self.base_url}/api/lookup-account",
                    json={"account_id": account_id},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                continue
            if resp.status_code == 404:
                raise AccountNotFound(account_id)
            if resp.status_code == 200:
                try:
                    return Account.model_validate(resp.json())
                except (ValueError, ValidationError) as exc:
                    raise APIUnavailable("malformed lookup response") from exc
            if resp.status_code < 500:
                raise APIUnavailable(f"unexpected status {resp.status_code}")
            last_exc = APIUnavailable(f"server error {resp.status_code}")
        raise APIUnavailable("lookup failed") from last_exc

    def process_payment(self, account_id: str, amount: Decimal, card: dict,
                        idempotency_key: str) -> str:
        payload: dict[str, Any] = {
            "account_id": account_id,
            "amount": float(amount.quantize(Decimal("0.01"))),
            "payment_method": {
                "type": "card",
                "card": {
                    "cardholder_name": card["holder"],
                    "card_number": card["number"],
                    "cvv": card["cvv"],
                    "expiry_month": card["expiry_month"],
                    "expiry_year": card["expiry_year"],
                },
            },
        }
        # Idempotency key so a retry (e.g. after a timeout) can't double-charge.
        # Will work if it is addressed by the server, but an ideal payment route must send
        headers = {"Idempotency-Key": idempotency_key}
        last_timeout: Exception | None = None
        for _ in range(2):
            try:
                resp = self.session.post(
                    f"{self.base_url}/api/process-payment",
                    json=payload, headers=headers, timeout=self.timeout,
                )
            except requests.Timeout as exc:
                last_timeout = exc
                continue
            except requests.RequestException as exc:
                raise APIUnavailable("payment request failed") from exc
            return self._read_payment(resp)
        raise PaymentOutcomeUnknown() from last_timeout

    @staticmethod
    def _read_payment(resp: requests.Response) -> str:
        if resp.status_code == 200:
            try:
                return resp.json()["transaction_id"]
            except (ValueError, KeyError) as exc:
                raise APIUnavailable("malformed response") from exc
        if resp.status_code == 422:
            try:
                code = resp.json().get("error_code", "unknown")
            except ValueError:
                code = "unknown"
            raise PaymentDeclined(code)
        raise APIUnavailable(f"unexpected status {resp.status_code}")
