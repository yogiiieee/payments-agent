"""In-memory stand-in for the payment API.

Unlike the real test server, the mock persists balances — required to test
post-payment state (double charge, pay-the-rest), which the live API cannot.
"""

from decimal import Decimal

from payment_agent.api_client import (
    Account,
    AccountNotFound,
    APIUnavailable,
    PaymentDeclined,
    PaymentOutcomeUnknown,
)

ACCOUNTS = [
    {"account_id": "ACC1001", "full_name": "Nithin Jain", "dob": "1990-05-14",
     "aadhaar_last4": "4321", "pincode": "400001", "balance": 1250.75},
    {"account_id": "ACC1002", "full_name": "Rajarajeswari Balasubramaniam", "dob": "1985-11-23",
     "aadhaar_last4": "9876", "pincode": "400002", "balance": 540.00},
    {"account_id": "ACC1003", "full_name": "Priya Agarwal", "dob": "1992-08-10",
     "aadhaar_last4": "2468", "pincode": "400003", "balance": 0.00},
    {"account_id": "ACC1004", "full_name": "Rahul Mehta", "dob": "1988-02-29",
     "aadhaar_last4": "1357", "pincode": "400004", "balance": 3200.50},
]


class MockApi:
    def __init__(self) -> None:
        self.accounts = {a["account_id"]: dict(a) for a in ACCOUNTS}
        self.lookups: list[str] = []
        self.payments: list[dict] = []          # successful payloads, checked by the runner
        self.payment_keys: list[str] = []        # every attempt's key, for idempotency checks
        self.fail_next_lookup: bool = False
        self.fail_next_payment: str | None = None  # an error_code, or "timeout"

    def lookup_account(self, account_id: str) -> Account:
        self.lookups.append(account_id)
        if self.fail_next_lookup:
            self.fail_next_lookup = False
            raise APIUnavailable("injected outage")
        if account_id not in self.accounts:
            raise AccountNotFound(account_id)
        return Account.model_validate(self.accounts[account_id])

    def process_payment(self, account_id: str, amount: Decimal, card: dict,
                        idempotency_key: str) -> str:
        self.payment_keys.append(idempotency_key)  # recorded even on failure, to check reuse
        if self.fail_next_payment == "timeout":
            self.fail_next_payment = None
            raise PaymentOutcomeUnknown()
        if self.fail_next_payment:
            code, self.fail_next_payment = self.fail_next_payment, None
            raise PaymentDeclined(code)
        account = self.accounts[account_id]
        balance = Decimal(str(account["balance"]))
        amt = Decimal(str(amount))
        if amt <= 0 or amt != amt.quantize(Decimal("0.01")):
            raise PaymentDeclined("invalid_amount")
        if amt > balance:
            raise PaymentDeclined("insufficient_balance")
        self.payments.append({"account_id": account_id, "amount": amt, "card": dict(card),
                              "idempotency_key": idempotency_key})
        account["balance"] = float(balance - amt)
        return f"txn_mock_{len(self.payments):03d}"
