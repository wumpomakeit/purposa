"""
OKX Agentic Wallet integration via onchainos CLI.

Used for:
  1. Checking wallet status / addresses
  2. Signing EIP-712 vote messages for Snapshot
  3. (Future) submitting on-chain governance transactions

The private key never leaves the TEE — we call the onchainos CLI which
signs inside the secure enclave and returns the signature.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any

import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)


def _run_onchainos(*args: str, timeout: int = 30) -> dict[str, Any]:
    """Run an onchainos CLI command and return parsed JSON output."""
    import os

    settings = get_settings()
    env = os.environ.copy()
    if settings.okx_api_key:
        env["OKX_API_KEY"] = settings.okx_api_key
    if settings.okx_secret_key:
        env["OKX_SECRET_KEY"] = settings.okx_secret_key
    if settings.okx_passphrase:
        env["OKX_PASSPHRASE"] = settings.okx_passphrase

    cmd = [settings.onchainos_bin, *args]
    log.debug("onchainos.run", cmd=cmd)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"onchainos {' '.join(args)} failed (exit {result.returncode}): {result.stderr}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"output": result.stdout.strip()}


def get_wallet_status() -> dict[str, Any]:
    """Return current wallet status from onchainos CLI."""
    return _run_onchainos("wallet", "status")


def get_wallet_addresses() -> dict[str, Any]:
    """Return wallet addresses for all supported chains."""
    return _run_onchainos("wallet", "addresses")


def get_evm_address() -> str:
    """Return the primary EVM address of the logged-in wallet.

    onchainos wallet addresses returns:
    {"ok": true, "data": {
        "evm": [{"address": "0x...", "chainIndex": "1", "chainName": "eth"}, ...],
        "xlayer": [{"address": "0x...", ...}],
        "solana": [...]
    }}
    """
    try:
        raw = get_wallet_addresses()
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        if not isinstance(data, dict):
            return ""

        # Primary: first entry in evm array
        evm_list = data.get("evm")
        if isinstance(evm_list, list) and evm_list:
            addr = evm_list[0].get("address", "")
            if addr and addr.startswith("0x"):
                return addr

        # Fallback: xlayer array
        xlayer_list = data.get("xlayer")
        if isinstance(xlayer_list, list) and xlayer_list:
            addr = xlayer_list[0].get("address", "")
            if addr and addr.startswith("0x"):
                return addr

        # Legacy: flat dict with evmAddress / address key
        for key in ("evmAddress", "address"):
            val = data.get(key)
            if val and isinstance(val, str) and val.startswith("0x"):
                return val

        # Last resort: first 0x... string value anywhere
        for v in data.values():
            if isinstance(v, str) and v.startswith("0x") and len(v) == 42:
                return v

        return ""
    except Exception as e:
        log.warning("wallet.get_address_failed", error=str(e))
        return ""


def sign_eip712_vote(
    space_id: str,
    proposal_id: str,
    choice: int,
    voter_address: str,
    reason: str = "Voted via Purposa",
) -> str:
    """
    Sign a Snapshot EIP-712 vote message using the OKX Agentic Wallet.

    Returns the hex signature string.
    Snapshot EIP-712 domain + Vote type are hardcoded per Snapshot v0.1.4 spec.
    """
    timestamp = int(time.time())

    # Build the EIP-712 typed data for Snapshot Vote
    typed_data = {
        "domain": {
            "name": "snapshot",
            "version": "0.1.4",
        },
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
            ],
            "Vote": [
                {"name": "from", "type": "address"},
                {"name": "space", "type": "string"},
                {"name": "timestamp", "type": "uint64"},
                {"name": "proposal", "type": "bytes32"},
                {"name": "choice", "type": "uint32"},
                {"name": "reason", "type": "string"},
                {"name": "app", "type": "string"},
                {"name": "metadata", "type": "string"},
            ],
        },
        "primaryType": "Vote",
        "message": {
            "from": voter_address,
            "space": space_id,
            "timestamp": timestamp,
            "proposal": proposal_id,
            "choice": choice,
            "reason": reason,
            "app": "purposa",
            "metadata": "{}",
        },
    }

    typed_data_json = json.dumps(typed_data)
    log.info("wallet.sign_eip712", proposal_id=proposal_id, choice=choice)

    result = _run_onchainos(
        "wallet",
        "sign-message",
        "--type", "eip712",
        "--message", typed_data_json,
        timeout=60,
    )

    sig = result.get("signature") or result.get("sig") or result.get("output", "")
    if not sig:
        raise ValueError(f"No signature in onchainos output: {result}")
    return str(sig)


def is_wallet_logged_in() -> bool:
    """Check if the agentic wallet is currently logged in."""
    try:
        status = _run_onchainos("wallet", "status")
        # onchainos wallet status returns: {"ok": true, "data": {"loggedIn": true/false, ...}}
        data = status.get("data", status)
        logged_in = data.get("loggedIn") or data.get("logged_in") or data.get("authenticated")
        return bool(logged_in)
    except Exception as e:
        log.warning("wallet.status_check_failed", error=str(e))
        return False
