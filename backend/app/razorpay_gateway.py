"""Secure server-side Razorpay API adapter for RealIQ.

This module never commits database transactions and never stores card,
UPI, or banking credentials. Financial outcomes must still be recorded
through payment_service after authoritative verification.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx


RAZORPAY_PROVIDER = "razorpay"
DEFAULT_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    """Safe, provider-related integration error."""


class RazorpayConfigurationError(RazorpayError):
    """Missing or invalid gateway configuration."""


class RazorpayVerificationError(RazorpayError):
    """Provider response or signature could not be verified."""


@dataclass(frozen=True)
class RazorpayConfig:
    key_id: str
    key_secret: str
    webhook_secret: str
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "RazorpayConfig":
        from . import config

        key_id = config.RAZORPAY_KEY_ID
        key_secret = config.RAZORPAY_KEY_SECRET
        webhook_secret = config.RAZORPAY_WEBHOOK_SECRET
        base_url = config.RAZORPAY_BASE_URL or DEFAULT_BASE_URL

        if not key_id or not key_secret or not webhook_secret:
            raise RazorpayConfigurationError(
                "Razorpay credentials are not fully configured."
            )
        if not base_url.startswith("https://"):
            raise RazorpayConfigurationError(
                "Razorpay API base URL must use HTTPS."
            )

        return cls(
            key_id=key_id,
            key_secret=key_secret,
            webhook_secret=webhook_secret,
            base_url=base_url,
        )


def _required_id(value: str, field: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 128:
        raise ValueError(f"{field} is required and must be at most 128 characters.")
    return result


def _minor_units(amount: Decimal | str | int | float, currency: str) -> int:
    """Convert a major-unit amount into Razorpay's smallest currency unit."""
    code = str(currency or "").strip().upper()
    if code != "INR":
        raise ValueError("RealIQ Razorpay integration currently supports INR only.")

    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid payment amount.") from exc

    if not value.is_finite() or value <= 0:
        raise ValueError("Payment amount must be positive and finite.")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError("Payment amount cannot have more than two decimal places.")

    return int(value * 100)


def _safe_provider_object(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise RazorpayError("Razorpay returned an invalid response.")
    return payload


class RazorpayGateway:
    def __init__(
        self,
        config: RazorpayConfig | None = None,
        *,
        client: Any = None,
    ):
        self.config = config or RazorpayConfig.from_env()
        self._client = client

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> dict:
        url = f"{self.config.base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            if self._client is not None:
                response = self._client.request(
                    method,
                    url,
                    json=json,
                    headers=headers,
                    auth=(self.config.key_id, self.config.key_secret),
                    timeout=self.config.timeout_seconds,
                )
            else:
                response = httpx.request(
                    method,
                    url,
                    json=json,
                    headers=headers,
                    auth=(self.config.key_id, self.config.key_secret),
                    timeout=self.config.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise RazorpayError("Razorpay request timed out.") from exc
        except httpx.RequestError as exc:
            raise RazorpayError("Unable to connect to Razorpay.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RazorpayError("Razorpay returned an invalid response.") from exc

        if response.is_error:
            # Do not expose provider response bodies, which may contain
            # operational details, identifiers, or sensitive information.
            raise RazorpayError(
                f"Razorpay request failed with HTTP {response.status_code}."
            )

        return _safe_provider_object(payload)

    def create_order(
        self,
        *,
        payment_attempt_id: int,
        amount: Decimal | str | int | float,
        currency: str = "INR",
        notes: dict | None = None,
    ) -> dict:
        """Create an order for a persisted RealIQ payment attempt."""
        if not isinstance(payment_attempt_id, int) or payment_attempt_id <= 0:
            raise ValueError("A valid payment attempt ID is required.")

        paise = _minor_units(amount, currency)
        receipt = f"realiq-payment-{payment_attempt_id}"

        payload = self._request(
            "POST",
            "/orders",
            json={
                "amount": paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {
                    **(notes or {}),
                    "realiq_payment_attempt_id": str(payment_attempt_id),
                },
            },
        )

        order_id = _required_id(payload.get("id"), "Razorpay order ID")
        if payload.get("amount") != paise or payload.get("currency") != "INR":
            raise RazorpayVerificationError(
                "Razorpay order amount or currency did not match the request."
            )

        return {
            "id": order_id,
            "amount": paise,
            "currency": "INR",
            "receipt": payload.get("receipt"),
            "status": payload.get("status"),
        }

    def fetch_payment(self, payment_id: str) -> dict:
        payment_id = _required_id(payment_id, "Razorpay payment ID")
        payload = self._request(
            "GET",
            f"/payments/{quote(payment_id, safe='')}",
        )
        return payload

    def verify_checkout_signature(
        self,
        *,
        order_id: str,
        payment_id: str,
        signature: str,
    ) -> bool:
        """Verify the Razorpay Checkout order_id|payment_id HMAC."""
        order_id = _required_id(order_id, "Razorpay order ID")
        payment_id = _required_id(payment_id, "Razorpay payment ID")
        supplied = str(signature or "").strip().lower()

        if not re.fullmatch(r"[0-9a-f]{64}", supplied):
            return False

        message = f"{order_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(
            self.config.key_secret.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, supplied)

    def verify_webhook_signature(
        self,
        *,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Verify the webhook against the exact raw request body."""
        if not isinstance(raw_body, bytes):
            return False

        supplied = str(signature or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", supplied):
            return False

        expected = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, supplied)

    def verify_captured_payment(
        self,
        *,
        payment_id: str,
        expected_order_id: str,
        expected_amount: Decimal | str | int | float,
        expected_currency: str = "INR",
    ) -> dict:
        """Fetch payment directly from Razorpay and verify captured details."""
        payment_id = _required_id(payment_id, "Razorpay payment ID")
        expected_order_id = _required_id(
            expected_order_id, "Razorpay order ID"
        )
        expected_paise = _minor_units(expected_amount, expected_currency)

        payment = self.fetch_payment(payment_id)

        if payment.get("order_id") != expected_order_id:
            raise RazorpayVerificationError("Payment belongs to another order.")
        if payment.get("amount") != expected_paise:
            raise RazorpayVerificationError("Payment amount does not match.")
        if str(payment.get("currency", "")).upper() != expected_currency.upper():
            raise RazorpayVerificationError("Payment currency does not match.")
        if payment.get("status") != "captured" or payment.get("captured") is not True:
            raise RazorpayVerificationError("Razorpay has not confirmed a captured payment.")

        return {
            "payment_id": payment_id,
            "order_id": expected_order_id,
            "amount": expected_paise,
            "currency": expected_currency.upper(),
            "status": "captured",
        }

    def create_refund(
        self,
        *,
        payment_id: str,
        amount: Decimal | str | int | float,
        currency: str = "INR",
        receipt: str | None = None,
        notes: dict | None = None,
    ) -> dict:
        """Request a full or partial refund; this does not update the ledger."""
        payment_id = _required_id(payment_id, "Razorpay payment ID")
        paise = _minor_units(amount, currency)

        body = {"amount": paise}
        if receipt:
            body["receipt"] = _required_id(receipt, "Refund receipt")
        if notes:
            body["notes"] = notes

        payload = self._request(
            "POST",
            f"/payments/{quote(payment_id, safe='')}/refund",
            json=body,
        )

        refund_id = _required_id(payload.get("id"), "Razorpay refund ID")
        if payload.get("amount") != paise:
            raise RazorpayVerificationError("Refund amount did not match the request.")

        return {
            "id": refund_id,
            "payment_id": payload.get("payment_id"),
            "amount": paise,
            "currency": str(payload.get("currency", currency)).upper(),
            "status": payload.get("status"),
        }

    def fetch_payment_refunds(
        self,
        payment_id: str,
        *,
        count: int = 100,
        skip: int = 0,
    ) -> list[dict]:
        """Fetch one page of refunds associated with a Razorpay payment."""
        payment_id = _required_id(payment_id, "Razorpay payment ID")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
            raise ValueError("Refund page count must be between 1 and 100.")
        if isinstance(skip, bool) or not isinstance(skip, int) or skip < 0:
            raise ValueError("Refund page offset must be non-negative.")

        payload = self._request(
            "GET",
            f"/payments/{quote(payment_id, safe='')}/refunds?count={count}&skip={skip}",
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise RazorpayVerificationError("Razorpay returned an invalid refunds collection.")
        return items

    def fetch_refund(self, refund_id: str) -> dict:
        refund_id = _required_id(refund_id, "Razorpay refund ID")
        return self._request(
            "GET",
            f"/refunds/{quote(refund_id, safe='')}",
        )

    def verify_refund(
        self,
        *,
        refund_id: str,
        expected_payment_id: str,
        expected_amount: Decimal | str | int | float,
        expected_currency: str = "INR",
    ) -> dict:
        """Fetch and validate authoritative provider refund state."""
        payload = self.fetch_refund(refund_id)
        expected_paise = _minor_units(expected_amount, expected_currency)

        if payload.get("id") != refund_id:
            raise RazorpayVerificationError("Refund ID does not match.")
        if payload.get("payment_id") != expected_payment_id:
            raise RazorpayVerificationError("Refund payment ID does not match.")
        if payload.get("amount") != expected_paise:
            raise RazorpayVerificationError("Refund amount does not match.")
        if str(payload.get("currency", "")).upper() != expected_currency.upper():
            raise RazorpayVerificationError("Refund currency does not match.")

        status = str(payload.get("status") or "").strip().lower()
        if status not in {"pending", "processed", "failed"}:
            raise RazorpayVerificationError("Razorpay returned an unknown refund status.")

        return {
            "id": refund_id,
            "payment_id": expected_payment_id,
            "amount": Decimal(expected_paise) / Decimal(100),
            "currency": expected_currency.upper(),
            "status": status,
        }
