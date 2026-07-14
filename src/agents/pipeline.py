"""Multi-agent analysis pipeline orchestrator."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from src.agents.base import AgentOutput
from src.agents.critic import run_critic
from src.agents.judge import run_judge
from src.agents.risk import run_risk_assessor
from src.agents.summarizer import run_summarizer
from src.config import get_settings
from src.snapshot.client import ProposalData
from src.snapshot.social import fetch_social_sentiment, format_sentiment_context, get_token_symbol

log = structlog.get_logger(__name__)


@dataclass
class AnalysisResult:
    """Full result of the multi-agent analysis pipeline."""

    trace_id: str
    proposal_id: str
    proposal_title: str
    proposal_url: str

    # Sub-agent outputs
    summary: str
    tldr: str
    pros: list[dict[str, Any]]
    cons: list[dict[str, Any]]
    risk_flags: list[dict[str, Any]]
    overall_risk_level: str

    # Judge verdict
    recommendation: str
    recommended_choice_index: int | None
    confidence: float
    reasoning: str
    key_considerations: list[str]
    dissenting_view: str
    warnings: list[str]

    # Metadata
    choices: list[str]
    vote_state: str
    scores_total: float
    quorum_percentage: float
    quorum_met: bool
    votes_count: int

    # Trace
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    elapsed_ms: int = 0

    @property
    def recommended_choice_label(self) -> str:
        if self.recommendation == "Abstain":
            return "Abstain"
        idx = self.recommended_choice_index
        if idx is not None and 0 <= idx < len(self.choices):
            return self.choices[idx]
        return self.recommendation

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "proposal_id": self.proposal_id,
            "proposal_title": self.proposal_title,
            "proposal_url": self.proposal_url,
            "summary": self.summary,
            "tldr": self.tldr,
            "pros": self.pros,
            "cons": self.cons,
            "risk_flags": self.risk_flags,
            "overall_risk_level": self.overall_risk_level,
            "recommendation": self.recommendation,
            "recommended_choice_index": self.recommended_choice_index,
            "recommended_choice_label": self.recommended_choice_label,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_considerations": self.key_considerations,
            "dissenting_view": self.dissenting_view,
            "warnings": self.warnings,
            "choices": self.choices,
            "vote_state": self.vote_state,
            "scores_total": self.scores_total,
            "quorum_percentage": self.quorum_percentage,
            "quorum_met": self.quorum_met,
            "votes_count": self.votes_count,
            "agent_trace": self.agent_trace,
            "created_at": self.created_at,
            "elapsed_ms": self.elapsed_ms,
        }


async def _fetch_social_enrichment(proposal: ProposalData) -> dict[str, Any]:
    """Fetch OKX live social sentiment — non-critical, returns {} on failure."""
    try:
        token = get_token_symbol(proposal.space.id, proposal.space.symbol)
        if not token:
            return {}
        sentiment = await asyncio.get_event_loop().run_in_executor(
            None, fetch_social_sentiment, token
        )
        return sentiment
    except Exception as e:
        log.warning("pipeline.social_enrichment_failed", error=str(e))
        return {}


async def run_analysis_pipeline(
    proposal: ProposalData,
    proposal_url: str,
) -> AnalysisResult:
    """
    Run the full multi-agent analysis pipeline:
    1. Social enrichment (OKX live sentiment) + Summarizer + Critic + Risk Assessor run in parallel
    2. Judge reconciles sub-agent outputs into a final verdict
    3. Falls back to rule-based analysis if no LLM is available
    """
    from src.config import get_settings

    settings = get_settings()
    trace_id = str(uuid.uuid4())
    start = datetime.now(UTC)
    log.info("pipeline.start", trace_id=trace_id, proposal_id=proposal.id)

    # Fetch OKX social sentiment in parallel with LLM agents (non-blocking)
    social_task = asyncio.create_task(_fetch_social_enrichment(proposal))

    if not settings.has_llm_credentials:
        # Rule-based fallback — no LLM configured
        log.warning("pipeline.mock_mode", reason="No LLM API key configured")
        from src.agents.mock import generate_mock_analysis
        mock = generate_mock_analysis(proposal)
        social = await social_task
        elapsed_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
        result = _build_result_from_mock(
            mock, proposal, proposal_url, trace_id, social, elapsed_ms
        )
        _save_trace(result)
        return result

    # Stage 1: parallel LLM analysis
    try:
        (summarizer_out, critic_out, risk_out), social = await asyncio.gather(
            asyncio.gather(
                run_summarizer(proposal, agent_idx=0),
                run_critic(proposal, agent_idx=0),
                run_risk_assessor(proposal, agent_idx=0),
            ),
            social_task,
        )
    except Exception as e:
        # LLM call failed — fall back to rule-based
        log.error("pipeline.stage1.failed", error=str(e), trace_id=trace_id)
        social = await social_task
        from src.agents.mock import generate_mock_analysis
        mock = generate_mock_analysis(proposal)
        mock["warnings"].insert(0, f"LLM pipeline failed ({type(e).__name__}: {e}). Rule-based fallback used.")
        elapsed_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
        result = _build_result_from_mock(
            mock, proposal, proposal_url, trace_id, social, elapsed_ms
        )
        _save_trace(result)
        return result

    log.info("pipeline.stage1.done", trace_id=trace_id)

    # Stage 2: judge reconciles
    try:
        judge_out = await run_judge(proposal, summarizer_out, critic_out, risk_out)
    except Exception as e:
        log.error("pipeline.stage2.failed", error=str(e), trace_id=trace_id)
        raise

    elapsed_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
    log.info("pipeline.done", trace_id=trace_id, elapsed_ms=elapsed_ms)

    agent_trace = [
        {
            "agent_id": out.agent_id,
            "role": out.role,
            "model": out.metadata.get("model", ""),
            "content_preview": out.content[:200] + "..." if len(out.content) > 200 else out.content,
        }
        for out in [summarizer_out, critic_out, risk_out, judge_out]
    ]

    # Add social enrichment to trace
    if social:
        agent_trace.append({
            "agent_id": "okx-social",
            "role": "social_enrichment",
            "model": "OKX onchainos social API",
            "content_preview": (
                f"Token: {social.get('token')} | "
                f"Sentiment: {social.get('sentiment_label')} | "
                f"Mentions: {social.get('mention_count')} (24h)"
            ),
        })

    warnings = judge_out.metadata.get("warnings", [])
    if social and social.get("sentiment_label") == "bearish":
        warnings.append(
            f"⚠ OKX live social sentiment for {social['token']} is BEARISH "
            f"({social['bearish_ratio']*100:.0f}% bearish, {social['mention_count']} mentions in 24h)"
        )

    result = AnalysisResult(
        trace_id=trace_id,
        proposal_id=proposal.id,
        proposal_title=proposal.title,
        proposal_url=proposal_url,
        summary=summarizer_out.content,
        tldr=summarizer_out.metadata.get("tldr", ""),
        pros=critic_out.metadata.get("pros", []),
        cons=critic_out.metadata.get("cons", []),
        risk_flags=risk_out.metadata.get("risk_flags", []),
        overall_risk_level=risk_out.metadata.get("overall_risk_level", "medium"),
        recommendation=judge_out.metadata.get("recommendation", "Abstain"),
        recommended_choice_index=judge_out.metadata.get("recommended_choice_index"),
        confidence=judge_out.metadata.get("confidence", 0.5),
        reasoning=judge_out.content,
        key_considerations=judge_out.metadata.get("key_considerations", []),
        dissenting_view=judge_out.metadata.get("dissenting_view", ""),
        warnings=warnings,
        choices=proposal.choices,
        vote_state=proposal.state,
        scores_total=proposal.scores_total,
        quorum_percentage=proposal.quorum_percentage,
        quorum_met=proposal.quorum_met,
        votes_count=proposal.votes_count,
        agent_trace=agent_trace,
        elapsed_ms=elapsed_ms,
    )

    _save_trace(result)
    return result


def _build_result_from_mock(
    mock: dict[str, Any],
    proposal: ProposalData,
    proposal_url: str,
    trace_id: str,
    social: dict[str, Any],
    elapsed_ms: int,
) -> AnalysisResult:
    """Build an AnalysisResult from a mock/rule-based analysis dict."""
    agent_trace: list[dict[str, Any]] = [
        {
            "agent_id": "rule-based-analyzer",
            "role": "mock",
            "model": "rule-based (no LLM)",
            "content_preview": "Fact extraction + keyword matching on proposal text",
        }
    ]
    if social:
        agent_trace.append({
            "agent_id": "okx-social",
            "role": "social_enrichment",
            "model": "OKX onchainos social API",
            "content_preview": (
                f"Token: {social.get('token')} | "
                f"Sentiment: {social.get('sentiment_label')} | "
                f"Mentions: {social.get('mention_count')} (24h)"
            ),
        })

    warnings = list(mock.get("warnings", []))
    if social and social.get("sentiment_label") == "bearish":
        warnings.append(
            f"⚠ OKX live social: {social['token']} is BEARISH "
            f"({social['bearish_ratio']*100:.0f}% bearish, {social['mention_count']} mentions/24h)"
        )

    return AnalysisResult(
        trace_id=trace_id,
        proposal_id=proposal.id,
        proposal_title=proposal.title,
        proposal_url=proposal_url,
        summary=mock["summary"],
        tldr=mock["tldr"],
        pros=mock["pros"],
        cons=mock["cons"],
        risk_flags=mock["risk_flags"],
        overall_risk_level=mock["overall_risk_level"],
        recommendation=mock["recommendation"],
        recommended_choice_index=mock["recommended_choice_index"],
        confidence=mock["confidence"],
        reasoning=mock["reasoning"],
        key_considerations=mock["key_considerations"],
        dissenting_view=mock["dissenting_view"],
        warnings=warnings,
        choices=proposal.choices,
        vote_state=proposal.state,
        scores_total=proposal.scores_total,
        quorum_percentage=proposal.quorum_percentage,
        quorum_met=proposal.quorum_met,
        votes_count=proposal.votes_count,
        agent_trace=agent_trace,
        elapsed_ms=elapsed_ms,
    )


def _save_trace(result: AnalysisResult) -> None:
    """Persist analysis trace to disk (or other configured backend)."""
    import json

    settings = get_settings()
    if settings.trace_backend != "local":
        return

    trace_dir = Path(settings.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_file = trace_dir / f"{result.trace_id}.json"
    trace_file.write_text(json.dumps(result.to_dict(), indent=2))
    log.info("trace.saved", path=str(trace_file))
