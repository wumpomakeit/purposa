"""
Rule-based analysis mock — runs the full pipeline without an LLM.

Used when no LLM API key is configured or all providers return errors.
Extracts facts from proposal text via pattern matching and returns a structured
analysis that mirrors the real pipeline output. Clearly labeled as mock in the trace.

This is useful for:
- Hackathon demos when API credits run out
- Development testing without consuming LLM quota
- Integration testing of the pipeline shape
"""
from __future__ import annotations

import re
from typing import Any

from src.snapshot.client import ProposalData


def _extract_dollar_amounts(text: str) -> list[str]:
    """Extract dollar/token amounts from proposal text."""
    patterns = [
        r"\$[\d,]+(?:\.\d+)?[KkMmBb]?",
        r"[\d,]+(?:\.\d+)?\s*(?:USD|USDC|USDT|ETH|tokens?|UNI|ARB|ENS)",
        r"[\d,]+(?:\.\d+)?\s*(?:million|thousand|billion)",
    ]
    found = []
    for pat in patterns:
        found.extend(re.findall(pat, text, re.IGNORECASE))
    return list(set(found[:5]))


def _extract_addresses(text: str) -> list[str]:
    """Extract Ethereum addresses from proposal text."""
    return re.findall(r"0x[a-fA-F0-9]{40}", text)[:5]


def _extract_dates(text: str) -> list[str]:
    """Extract dates/periods from proposal text."""
    patterns = [
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
        r"\d+\s+(?:days?|weeks?|months?|years?)",
        r"Q[1-4]\s+\d{4}",
    ]
    found = []
    for pat in patterns:
        found.extend(re.findall(pat, text, re.IGNORECASE))
    return list(set(found[:5]))


def _assess_treasury_risk(text: str, amounts: list[str]) -> tuple[str, str]:
    """Return (severity, detail) for treasury risk based on text analysis."""
    text_lower = text.lower()
    large_amount = any(
        re.search(r"[\d,]+\s*million|[\d,]+[Mm]\b|\$[\d,]+,[0-9]{3}", a) for a in amounts
    )
    if large_amount or any(w in text_lower for w in ["million", "treasury transfer", "grant", "fund"]):
        return "high", "Proposal involves significant fund movement"
    if amounts:
        return "medium", "Proposal involves token or fund allocation"
    return "low", "No major treasury action detected"


def _assess_vagueness_risk(text: str) -> tuple[str, str]:
    """Return (severity, detail) for vagueness risk."""
    text_lower = text.lower()
    vague_phrases = [
        "to be determined", "tbd", "at the discretion", "as needed",
        "approximately", "may include", "could potentially",
    ]
    vague_count = sum(1 for p in vague_phrases if p in text_lower)
    if vague_count >= 3:
        return "medium", f"Found {vague_count} vague phrases indicating underspecified scope"
    if vague_count > 0:
        return "low", "Some terms are loosely defined but scope is broadly clear"
    return "low", "Proposal scope appears reasonably well-defined"


def _infer_recommendation(
    proposal: ProposalData,
    risk_level: str,
) -> tuple[str, int | None, float, str]:
    """
    Infer a recommendation from quorum state, vote distribution, and risk.
    Returns: (recommendation, choice_index, confidence, reasoning)
    """
    # If quorum not met and vote is close to ending, lean Abstain
    if not proposal.quorum_met and proposal.state == "active":
        return (
            "Abstain",
            None,
            0.45,
            "Quorum has not been reached. Participation needed — abstaining until quorum clears.",
        )

    # Use current vote distribution as a weak signal
    leading = proposal.leading_choice
    if leading and leading.percentage > 60 and risk_level in ("low", "medium"):
        idx = leading.index
        return (
            "For" if leading.label.lower() in ("yes", "for", "yae", "yea") else leading.label,
            idx,
            0.62,
            f"Current vote strongly favors '{leading.label}' ({leading.percentage:.0f}%) "
            f"with {risk_level} risk. Rule-based analysis supports the leading position.",
        )

    if risk_level == "high":
        return (
            "Abstain",
            None,
            0.50,
            "High-risk flags detected. Recommend reviewing proposal details before committing a vote.",
        )

    # Default: lean toward first choice (often "Yes"/"For") with low confidence
    if proposal.choices:
        first = proposal.choices[0]
        return (
            first,
            0,
            0.42,
            "Insufficient data for a high-confidence recommendation. "
            "Leans toward the leading choice based on vote distribution.",
        )

    return ("Abstain", None, 0.3, "Unable to determine recommendation from available data.")


def generate_mock_analysis(proposal: ProposalData) -> dict[str, Any]:
    """
    Generate a rule-based analysis for a proposal.
    All fields match the shape of the real multi-agent pipeline output.
    """
    body = proposal.body or ""
    title = proposal.title or ""
    combined = title + "\n" + body

    amounts = _extract_dollar_amounts(combined)
    addresses = _extract_addresses(combined)
    dates = _extract_dates(combined)

    treas_sev, treas_detail = _assess_treasury_risk(combined, amounts)
    vague_sev, vague_detail = _assess_vagueness_risk(combined)

    risk_flags = []
    if treas_sev != "low":
        risk_flags.append({
            "category": "treasury",
            "severity": treas_sev,
            "flag": "Treasury action detected",
            "detail": treas_detail,
        })
    if vague_sev != "low":
        risk_flags.append({
            "category": "vagueness",
            "severity": vague_sev,
            "flag": "Underspecified scope",
            "detail": vague_detail,
        })
    if not proposal.quorum_met:
        risk_flags.append({
            "category": "quorum",
            "severity": "medium",
            "flag": "Quorum not reached",
            "detail": f"Only {proposal.quorum_percentage:.1f}% of required quorum reached.",
        })

    risk_severities = [f["severity"] for f in risk_flags]
    overall_risk = (
        "high" if "high" in risk_severities
        else "medium" if "medium" in risk_severities
        else "low"
    )

    rec, rec_idx, confidence, reasoning = _infer_recommendation(proposal, overall_risk)

    # Build pros from the leading vote distribution
    pros = [{"point": "Community has already demonstrated voting interest", "strength": "medium"}]
    if proposal.scores_total > 0:
        pros.append({
            "point": f"{proposal.votes_count} votes cast with {proposal.scores_total:.2f} total voting power",
            "strength": "medium",
        })
    if not risk_flags:
        pros.append({"point": "No major risk flags detected in proposal text", "strength": "high"})

    # Build cons from risk flags and vagueness
    cons: list[dict] = []
    if amounts:
        cons.append({
            "point": f"Fund allocation detected: {', '.join(amounts[:3])}",
            "strength": "medium",
        })
    if not proposal.quorum_met:
        cons.append({
            "point": f"Quorum only {proposal.quorum_percentage:.1f}% reached — vote may not count",
            "strength": "high",
        })

    # Summary built from extracted facts
    summary_parts = [
        f"**{title}** — proposed by {proposal.author[:12]}...{proposal.author[-4:] if len(proposal.author) > 16 else ''}.",
        f"Space: {proposal.space.name}. Vote type: {proposal.vote_type}.",
    ]
    if dates:
        summary_parts.append(f"Key dates/periods mentioned: {', '.join(dates[:3])}.")
    if amounts:
        summary_parts.append(f"Financial figures referenced: {', '.join(amounts[:3])}.")
    if addresses:
        summary_parts.append(f"On-chain addresses mentioned: {len(addresses)}.")
    summary_parts.append(
        f"Current state: {proposal.state} — {proposal.votes_count} votes, "
        f"{proposal.scores_total:.2f} total voting power, "
        f"{proposal.quorum_percentage:.1f}% quorum."
    )

    return {
        "summary": " ".join(summary_parts),
        "tldr": f"{title[:120]}{'...' if len(title) > 120 else ''}",
        "pros": pros,
        "cons": cons,
        "risk_flags": risk_flags,
        "overall_risk_level": overall_risk,
        "recommendation": rec,
        "recommended_choice_index": rec_idx,
        "confidence": confidence,
        "reasoning": reasoning,
        "key_considerations": [
            f"Quorum status: {proposal.quorum_percentage:.1f}% reached",
            f"Risk level: {overall_risk}",
            f"Votes so far: {proposal.votes_count}",
        ],
        "dissenting_view": "Rule-based analysis may miss nuance present in the full proposal text.",
        "warnings": ["⚠ This analysis was generated by rule-based extraction, not an LLM. "
                     "For a full multi-agent verdict, configure NVIDIA_API_KEY or OPENAI_API_KEY."],
        "_mock": True,
    }
