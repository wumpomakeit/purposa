"""FastAPI route definitions for Purposa."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from src.agents.pipeline import run_analysis_pipeline
from src.api.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    TraceResponse,
    VoteRequest,
    VoteResponse,
)
from src.config import get_settings
from src.payments.x402 import payment_gate
from src.snapshot.client import fetch_proposal
from src.wallet.okx import (
    build_snapshot_vote_typed_data,
    get_evm_address,
    is_wallet_logged_in,
    sign_eip712_vote,
)

router = APIRouter()
log = structlog.get_logger(__name__)


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    """
    Service health check.

    Notes on wallet_connected:
    - False does NOT affect POST /analyze (analysis never uses the wallet).
    - False only prevents POST /vote (EIP-712 signing requires an active session).
    - In fresh deployments the onchainos session is established automatically at
      startup via AK-mode login. If it shows False after startup, check that
      ONCHAINOS_BIN points to the installed binary and OKX credentials are set.
    """
    settings = get_settings()

    # Check onchainos binary presence + version
    try:
        result = subprocess.run(
            [settings.onchainos_bin, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        onchainos_version = result.stdout.strip() if result.returncode == 0 else "error"
    except FileNotFoundError:
        onchainos_version = f"not found at {settings.onchainos_bin}"
    except Exception:
        onchainos_version = "unavailable"

    # wallet_connected = onchainos CLI reachable AND wallet session active
    wallet_connected = is_wallet_logged_in()

    return HealthResponse(
        status="ok",
        version="0.1.0",
        wallet_connected=wallet_connected,
        okx_credentials=settings.has_okx_credentials,
        llm_available=settings.has_llm_credentials,
        onchainos_version=onchainos_version,
    )


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    tags=["Analysis"],
    responses={
        200: {"description": "Analysis complete — verdict ready"},
        402: {
            "description": "Payment required. Use the PAYMENT-REQUIRED header value with onchainos payment pay.",
            "content": {
                "application/json": {
                    "example": {
                        "error": "Payment Required",
                        "x402_payload": "<base64>",
                        "instructions": "onchainos payment pay --payload <x402_payload>",
                    }
                }
            },
        },
    },
)
async def analyze(request: Request, body: AnalyzeRequest) -> Response:
    """
    Analyze a Snapshot governance proposal.

    **Payment required** (x402 exact scheme). When called without a valid
    Authorization header, returns HTTP 402 with a PAYMENT-REQUIRED header
    containing the payment challenge. Use `onchainos payment pay` to complete
    payment, then replay this request with the returned `authorization_header`.

    On success, returns a multi-agent verdict including summary, pros/cons,
    risk flags, a recommendation (For/Against/Abstain), confidence score,
    and a full reasoning trace.
    """
    settings = get_settings()

    # Payment gate
    gate_result = await payment_gate(request, "/analyze")
    if isinstance(gate_result, Response):
        return gate_result

    # Fetch proposal from Snapshot
    try:
        proposal = await fetch_proposal(body.proposal_url)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error("snapshot.fetch_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Failed to fetch proposal: {e}")

    # Run multi-agent pipeline
    try:
        result = await run_analysis_pipeline(proposal, body.proposal_url)
    except RuntimeError as e:
        if "No LLM API key" in str(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    "LLM provider not configured. "
                    "Set OPENAI_API_KEY or ANTHROPIC_API_KEY in your .env file."
                ),
            )
        raise HTTPException(status_code=500, detail=f"Analysis pipeline failed: {e}")

    return JSONResponse(content=result.to_dict())


@router.post("/vote", response_model=VoteResponse, tags=["Voting"])
async def vote(body: VoteRequest) -> VoteResponse:
    """
    Submit a vote on a Snapshot proposal using the OKX Agentic Wallet.

    This endpoint requires the user to have previously:
    1. Called /analyze to understand the proposal
    2. Explicitly chosen a vote choice (confirmed by the `choice_index` parameter)

    The vote is signed inside the OKX TEE using the logged-in Agentic Wallet.
    No private key is exposed. The signature is submitted to Snapshot Hub.

    **This action is final — votes on Snapshot cannot be retracted.**

    **Security model note (see README's "Security Model" section for full
    detail):** unlike /analyze, this endpoint is currently single-tenant —
    every call signs and submits using *this deployment's own* onchainos
    session, never the calling agent/user's own wallet or voting power.
    onchainos's CLI has no per-call auth context to do otherwise (verified,
    not assumed). Every response's `self_serve_instructions` field gives a
    technical caller the exact commands to sign and submit this same vote
    with their own wallet directly against Snapshot Hub instead.
    """
    # Check wallet is connected
    if not is_wallet_logged_in():
        raise HTTPException(
            status_code=503,
            detail=(
                "Agentic Wallet not connected. "
                "Run: onchainos wallet login"
            ),
        )

    # Fetch proposal to validate choice index and get space ID
    try:
        proposal = await fetch_proposal(body.proposal_url)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if body.choice_index >= len(proposal.choices):
        raise HTTPException(
            status_code=400,
            detail=(
                f"choice_index {body.choice_index} is out of range. "
                f"Proposal has {len(proposal.choices)} choices (0-indexed): "
                + ", ".join(f"{i}={c}" for i, c in enumerate(proposal.choices))
            ),
        )

    if proposal.state != "active":
        raise HTTPException(
            status_code=400,
            detail=f"Proposal is not active (current state: {proposal.state}). Cannot vote.",
        )

    choice_label = proposal.choices[body.choice_index]
    voter_address = get_evm_address()

    if not voter_address:
        raise HTTPException(
            status_code=503,
            detail="Could not retrieve wallet address. Is the Agentic Wallet logged in?",
        )

    # Fixed once, reused for both signing and Hub submission below — the two
    # MUST match exactly, since Snapshot Hub recovers the signer from the
    # full signed message (including this timestamp).
    vote_timestamp = int(time.time())
    snapshot_choice = body.choice_index + 1  # Snapshot uses 1-based choice

    # Sign the vote
    try:
        signature = sign_eip712_vote(
            space_id=proposal.space.id,
            proposal_id=proposal.id,
            choice=snapshot_choice,
            voter_address=voter_address,
            reason=body.reason,
            timestamp=vote_timestamp,
        )
    except Exception as e:
        log.error("vote.sign_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to sign vote: {e}")

    # Submit to Snapshot Hub
    try:
        from src.snapshot.client import submit_vote

        receipt = await submit_vote(
            proposal_id=proposal.id,
            choice=snapshot_choice,
            from_address=voter_address,
            sig=signature,
            space=proposal.space.id,
            timestamp=vote_timestamp,
            reason=body.reason,
        )
    except Exception as e:
        log.error("vote.submit_failed", error=str(e))
        raise HTTPException(
            status_code=502,
            detail=f"Vote signed successfully but submission to Snapshot failed: {e}",
        )

    log.info(
        "vote.submitted",
        proposal_id=proposal.id,
        choice=choice_label,
        voter=voter_address,
        trace_id=body.trace_id,
    )

    return VoteResponse(
        success=True,
        proposal_id=proposal.id,
        choice_label=choice_label,
        voter_address=voter_address,
        signature=signature,
        snapshot_receipt=receipt,
        message=(
            f"Vote cast: {choice_label} on '{proposal.title}'. "
            "Signature submitted to Snapshot Hub."
        ),
        signed_by="operator_wallet",
        self_serve_instructions=_build_self_serve_vote_instructions(
            proposal_space_id=proposal.space.id,
            proposal_id=proposal.id,
            choice=snapshot_choice,
            reason=body.reason,
            timestamp=vote_timestamp,
        ),
    )


def _build_self_serve_vote_instructions(
    proposal_space_id: str,
    proposal_id: str,
    choice: int,
    reason: str,
    timestamp: int,
) -> str:
    """
    Build a ready-to-run, self-serve alternative for a caller who wants
    their OWN wallet's voting power reflected, instead of the operator's.

    This deployment's onchainos CLI exposes a single global session per
    machine — there is no per-call auth context (no CLI flag, env var, or
    session token scopes one command to a different account; verified
    empirically, see README's Security Model section). So rather than
    accepting credentials Purposa has no safe way to actually use per
    request, this hands back the exact EIP-712 payload that was just
    signed (minus the address) so a technical caller can sign it with
    ANY EIP-712-capable wallet of their own and submit it directly to
    Snapshot Hub — never sending their credentials to Purposa at all.
    """
    # Full typed data (incl. EIP712Domain type + primaryType) — what actually
    # gets signed, matching sign_eip712_vote()'s CLI invocation exactly.
    sign_payload = build_snapshot_vote_typed_data(
        space_id=proposal_space_id,
        proposal_id=proposal_id,
        choice=choice,
        voter_address="<YOUR_WALLET_ADDRESS>",
        reason=reason,
        timestamp=timestamp,
    )
    sign_payload_json = json.dumps(sign_payload)

    # Hub's /api/msg "data" shape omits EIP712Domain/primaryType — matching
    # submit_vote()'s payload exactly, so this curl example actually works.
    hub_data = {
        "domain": sign_payload["domain"],
        "types": {"Vote": sign_payload["types"]["Vote"]},
        "message": sign_payload["message"],
    }
    hub_body_json = json.dumps(
        {"address": "<YOUR_WALLET_ADDRESS>", "sig": "<signature from step 2>", "data": hub_data}
    )

    return (
        "This vote was signed and submitted using Purposa's OPERATOR wallet, "
        "not yours — this deployment's onchainos session is single-account "
        "per machine, so it cannot sign on a per-caller basis. To vote with "
        "your OWN wallet and have YOUR OWN voting power reflected instead, "
        "run this yourself (nothing here is sent to Purposa):\n\n"
        "1) Log in to your own onchainos wallet if you haven't already:\n"
        "   onchainos wallet login\n\n"
        "2) Sign this exact vote payload (replace <YOUR_WALLET_ADDRESS> with "
        "your logged-in wallet's address, both in --from and inside --message):\n"
        f"   onchainos wallet sign-message --type eip712 --chain ethereum "
        f"--from <YOUR_WALLET_ADDRESS> --message '{sign_payload_json}'\n\n"
        "3) Submit the resulting signature straight to Snapshot Hub yourself "
        "(no Purposa involvement, no API key needed):\n"
        f"   curl -X POST https://hub.snapshot.org/api/msg -H 'Content-Type: "
        f"application/json' -d '{hub_body_json}'\n\n"
        "Note the timestamp above is specific to this response — request a "
        "fresh /vote call if you wait more than a few minutes before signing."
    )


@router.get("/trace/{trace_id}", response_model=TraceResponse, tags=["Audit"])
async def get_trace(trace_id: str) -> TraceResponse:
    """
    Retrieve the full reasoning trace for a prior analysis.

    Allows voters and other agents to audit the multi-agent verdict —
    seeing what each agent said and why the final recommendation was reached.
    """
    settings = get_settings()

    # Validate trace_id format (UUID)
    import re
    if not re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        trace_id,
    ):
        raise HTTPException(status_code=400, detail="Invalid trace_id format")

    if settings.trace_backend == "local":
        trace_file = Path(settings.trace_dir) / f"{trace_id}.json"
        if not trace_file.exists():
            raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found")
        data = json.loads(trace_file.read_text())
        return TraceResponse(trace_id=trace_id, data=data)

    raise HTTPException(
        status_code=501, detail=f"Trace backend '{settings.trace_backend}' not implemented"
    )


@router.get("/api", tags=["System"])
async def root() -> dict[str, str]:
    """Purposa API status."""
    return {
        "service": "Purposa",
        "description": "Multi-Agent DAO Governance Analysis & Voting Assistant",
        "docs": "/docs",
        "health": "/health",
        "version": "0.1.0",
    }
