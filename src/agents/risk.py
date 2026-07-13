"""Risk assessor agent — surfaces governance and execution risk flags."""
from __future__ import annotations

import json
import re
from typing import Literal

from src.agents.base import AgentOutput, call_llm
from src.config import get_settings
from src.snapshot.client import ProposalData

SYSTEM_PROMPT = """You are a DAO governance risk analyst specializing in identifying
process, financial, and protocol risks in governance proposals.

Analyze the proposal and return a JSON object:
{
  "risk_flags": [
    {
      "category": "treasury|process|quorum|conflict|vagueness|governance|execution|other",
      "severity": "critical|high|medium|low",
      "flag": "<concise flag description>",
      "detail": "<explanation of the risk and why it matters>"
    }
  ],
  "overall_risk_level": "critical|high|medium|low",
  "risk_summary": "<1-2 sentence summary of the main risk profile>",
  "quorum_risk": "<assessment of whether this proposal is likely to meet quorum>"
}

Risk category definitions:
- treasury: involves significant fund disbursement or token minting without clear accountability
- process: procedural shortcut, rushed vote, or inadequate discussion period
- quorum: risk of failing to meet quorum
- conflict: proposer has apparent conflict of interest
- vagueness: key terms are undefined or scope is unclear
- governance: could concentrate power or affect governance structure
- execution: technical complexity or unclear implementation plan
- other: any other notable risk

Flag any treasury ask >$100k as at minimum medium severity.
Flag any vote with <7 days discussion as medium severity.
If there are no meaningful risks, say so explicitly."""


async def run_risk_assessor(proposal: ProposalData, agent_idx: int = 0) -> AgentOutput:
    settings = get_settings()
    model = settings.primary_model
    prefer = "openai"

    raw = await call_llm(
        system=SYSTEM_PROMPT,
        user=proposal.to_context_string(),
        model=model,
        prefer=prefer,
        temperature=0.2,
        max_tokens=2000,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        parsed = json.loads(m.group(1)) if m else {
            "risk_flags": [],
            "overall_risk_level": "medium",
            "risk_summary": raw,
            "quorum_risk": "",
        }

    return AgentOutput(
        agent_id=f"risk-assessor-{agent_idx}",
        role="risk_assessor",
        content=raw,
        metadata={
            "risk_flags": parsed.get("risk_flags", []),
            "overall_risk_level": parsed.get("overall_risk_level", "medium"),
            "risk_summary": parsed.get("risk_summary", ""),
            "quorum_risk": parsed.get("quorum_risk", ""),
            "model": model,
        },
    )
