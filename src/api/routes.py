"""FastAPI route definitions for Purposa."""
from __future__ import annotations

import json
import subprocess
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
    get_evm_address,
    is_wallet_logged_in,
    sign_eip712_vote,
)

router = APIRouter()
log = structlog.get_logger(__name__)


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    """Service health check — verifies wallet and credential status."""
    settings = get_settings()

    # Check onchainos version
    try:
        result = subprocess.run(
            [settings.onchainos_bin, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        onchainos_version = result.stdout.strip()
    except Exception:
        onchainos_version = "unavailable"

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

    # Sign the vote (1-indexed for Snapshot protocol)
    try:
        signature = sign_eip712_vote(
            space_id=proposal.space.id,
            proposal_id=proposal.id,
            choice=body.choice_index + 1,  # Snapshot uses 1-based choice
            voter_address=voter_address,
            reason=body.reason,
        )
    except Exception as e:
        log.error("vote.sign_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to sign vote: {e}")

    # Submit to Snapshot Hub
    try:
        from src.snapshot.client import submit_vote

        receipt = await submit_vote(
            proposal_id=proposal.id,
            choice=body.choice_index + 1,
            from_address=voter_address,
            sig=signature,
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


@router.get("/", tags=["System"])
async def root() -> dict[str, str]:
    """Purposa service root — API documentation link."""
    return {
        "service": "Purposa",
        "description": "Multi-Agent DAO Governance Analysis & Voting Assistant",
        "docs": "/docs",
        "health": "/health",
        "version": "0.1.0",
    }
