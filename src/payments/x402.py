"""
x402 payment middleware (seller side).

Purposa is a *seller* in the x402 model:
  - Returns HTTP 402 with a PAYMENT-REQUIRED header when no payment is attached.
  - Verifies the payment header on subsequent requests.
  - Uses the OKX onchainos CLI to validate TEE-signed receipts.

Reference: https://github.com/okx/onchainos-skills/blob/main/skills/okx-agent-payments-protocol/SKILL.md
"""
from __future__ import annotations

import base64
import json
import subprocess
from typing import Any

import structlog
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from src.config import get_settings

log = structlog.get_logger(__name__)

# x402 v2 scheme
X402_VERSION = 2
SCHEME_NAME = "exact"


def _build_payment_required_payload(resource_path: str) -> dict[str, Any]:
    """
    Build the x402 v2 PAYMENT-REQUIRED payload.

    Field names match the OKX onchainos CLI's exact-scheme expectations:
      asset             — ERC-20 contract address (not "tokenAddress")
      payTo             — recipient address (not "recipient")
      maxTimeoutSeconds — relative timeout in seconds (not "expiresAt")
      extra.name        — EIP-712 domain name of the token (required by onchainos)
      extra.version     — EIP-712 domain version of the token

    Reference: https://web3.okx.com/onchainos/dev-docs/payments/payment-use-buyer
    """
    settings = get_settings()

    return {
        "x402Version": X402_VERSION,
        "resource": {
            "path": resource_path,
            "method": "POST",
        },
        "accepts": [
            {
                "scheme": SCHEME_NAME,
                "network": settings.payment_network,
                "asset": settings.payment_token_address,
                "amount": str(settings.analysis_price_usdt),
                "payTo": settings.seller_address,
                "maxTimeoutSeconds": settings.payment_timeout_seconds,
                "extra": {
                    "name": settings.payment_token_name,
                    "version": settings.payment_token_version,
                },
            }
        ],
    }


def build_402_response(resource_path: str) -> Response:
    """Return an HTTP 402 response with x402 payment details."""
    payload = _build_payment_required_payload(resource_path)
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    settings = get_settings()

    return JSONResponse(
        status_code=402,
        content={
            "error": "Payment Required",
            "message": (
                f"This endpoint requires payment of "
                f"{settings.analysis_price_usdt} token units "
                f"({settings.analysis_price_usdt / 1_000_000:.4f} USD₮0). "
                "Use the OKX Agentic Wallet (onchainos payment pay) to pay."
            ),
            "x402_payload": encoded,
            "instructions": (
                "Run: onchainos payment pay --payload '<x402_payload>' "
                "then replay this request with header: "
                "Authorization: <returned authorization_header>"
            ),
        },
        headers={
            "PAYMENT-REQUIRED": encoded,
            "X-Payment-Version": str(X402_VERSION),
        },
    )


async def verify_payment(request: Request) -> bool:
    """
    Verify the x402 payment attached to a request.

    Looks for Authorization header produced by:
      onchainos payment pay --payload <PAYMENT-REQUIRED value>

    In development mode payment verification is bypassed (any Authorization
    header is accepted) so the pipeline can be tested without a funded wallet.
    In production, the onchainos CLI verifies the TEE-signed receipt.
    """
    settings = get_settings()

    # Development bypass — any recognised payment header passes
    if not settings.is_production:
        log.warning(
            "payment.bypass",
            reason="Development mode — payment verification skipped",
        )
        return True

    auth_header = _get_payment_header(request)
    if not auth_header:
        return False

    # Production: verify via onchainos CLI
    auth_header = _get_payment_header(request)
    try:
        result = subprocess.run(
            [settings.onchainos_bin, "payment", "verify", "--authorization", auth_header],
            capture_output=True,
            text=True,
            timeout=15,
            env=_build_okx_env(),
        )
        if result.returncode == 0:
            log.info("payment.verified")
            return True
        log.warning("payment.invalid", stderr=result.stderr)
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("payment.verify_error", error=str(e))
        return False


def _build_okx_env() -> dict[str, str]:
    """Build environment dict with OKX credentials for subprocess calls."""
    import os

    settings = get_settings()
    env = os.environ.copy()
    if settings.okx_api_key:
        env["OKX_API_KEY"] = settings.okx_api_key
    if settings.okx_secret_key:
        env["OKX_SECRET_KEY"] = settings.okx_secret_key
    if settings.okx_passphrase:
        env["OKX_PASSPHRASE"] = settings.okx_passphrase
    return env


def _get_payment_header(request: Request) -> str:
    """
    Extract payment token from request headers.

    onchainos payment pay returns header_name = "PAYMENT-SIGNATURE" (x402 v2).
    We also accept "Authorization" (dev/testing) and legacy "X-PAYMENT" (v1).
    """
    return (
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("Authorization")
        or request.headers.get("X-PAYMENT")
        or ""
    )


async def payment_gate(request: Request, resource_path: str) -> bool | Response:
    """
    Gate middleware: check payment header.

    Returns True if payment is valid (or bypassed in dev).
    Returns a 402 Response if payment is missing.
    Raises HTTPException(402) if payment is present but invalid.
    """
    auth = _get_payment_header(request)

    if not auth:
        return build_402_response(resource_path)

    if not await verify_payment(request):
        raise HTTPException(
            status_code=402,
            detail="Payment verification failed. Obtain a fresh payment via onchainos payment pay.",
        )

    return True
