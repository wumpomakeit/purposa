"""Pydantic models for Purposa API request/response objects."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class AnalyzeRequest(BaseModel):
    proposal_url: str = Field(
        ...,
        description="Snapshot proposal URL or proposal ID. "
        "E.g. https://snapshot.org/#/uniswap/proposal/0xabc... or just 0xabc...",
        examples=["https://snapshot.org/#/uniswap.eth/proposal/0xabcdef1234567890"],
    )


class RiskFlag(BaseModel):
    category: str
    severity: str
    flag: str
    detail: str


class ProChoice(BaseModel):
    point: str
    strength: str


class AgentTraceEntry(BaseModel):
    agent_id: str
    role: str
    model: str
    content_preview: str


class AnalyzeResponse(BaseModel):
    trace_id: str = Field(..., description="Unique ID for this analysis. Use for the /trace endpoint.")
    proposal_id: str
    proposal_title: str
    proposal_url: str

    # Plain-language verdict
    tldr: str
    summary: str
    pros: list[dict[str, Any]]
    cons: list[dict[str, Any]]
    risk_flags: list[dict[str, Any]]
    overall_risk_level: str = Field(..., description="critical|high|medium|low")

    # Recommendation
    recommendation: str = Field(..., description="For|Against|Abstain")
    recommended_choice_index: int | None = Field(
        None, description="0-based index into choices array"
    )
    recommended_choice_label: str
    confidence: float = Field(..., ge=0.0, le=1.0, description="0.0–1.0")
    reasoning: str
    key_considerations: list[str]
    dissenting_view: str
    warnings: list[str]

    # Proposal context
    choices: list[str]
    vote_state: str
    scores_total: float
    quorum_percentage: float
    quorum_met: bool
    votes_count: int

    # Audit
    agent_trace: list[dict[str, Any]]
    created_at: str
    elapsed_ms: int


class VoteRequest(BaseModel):
    proposal_url: str = Field(
        ...,
        description="Snapshot proposal URL or ID",
    )
    choice_index: int = Field(
        ...,
        ge=0,
        description="0-based index of the choice to vote for",
    )
    reason: str = Field(
        default="Voted via Purposa",
        max_length=280,
        description="Optional voting reason (shown publicly on Snapshot)",
    )
    trace_id: str | None = Field(
        None,
        description="Optional: trace_id from a prior /analyze call, for audit linking",
    )


class VoteResponse(BaseModel):
    success: bool
    proposal_id: str
    choice_label: str
    voter_address: str
    signature: str
    snapshot_receipt: dict[str, Any] | None = None
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    wallet_connected: bool
    okx_credentials: bool
    llm_available: bool
    onchainos_version: str


class TraceResponse(BaseModel):
    trace_id: str
    data: dict[str, Any]
