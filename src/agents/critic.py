"""Critic agent — independently identifies pros, cons, and key tensions."""
from __future__ import annotations

import json
import re

from src.agents.base import AgentOutput, call_llm
from src.config import get_settings
from src.snapshot.client import ProposalData

SYSTEM_PROMPT = """You are a critical DAO governance analyst. Your job is to identify
the strongest arguments FOR and AGAINST a governance proposal, plus any areas of
ambiguity or missing information.

Return your response as a JSON object:
{
  "pros": [
    {"point": "<argument for the proposal>", "strength": "high|medium|low"}
  ],
  "cons": [
    {"point": "<argument against the proposal>", "strength": "high|medium|low"}
  ],
  "ambiguities": [
    "<something unclear or underspecified in the proposal>"
  ],
  "missing_info": [
    "<information that voters would want but is absent>"
  ],
  "key_tension": "<the single most important trade-off voters face>"
}

Be balanced. List at least 2 pros and 2 cons. Mark strength as high/medium/low based
on how significant the point is for governance decision-making."""


async def run_critic(proposal: ProposalData, agent_idx: int = 0) -> AgentOutput:
    settings = get_settings()
    # Use a different model for the second critic to get diversity
    model = settings.secondary_model if agent_idx == 0 else settings.primary_model
    prefer = "nvidia"

    raw = await call_llm(
        system=SYSTEM_PROMPT,
        user=proposal.to_context_string(),
        model=model,
        prefer=prefer,
        temperature=0.4,
        max_tokens=800,
        timeout=45.0,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        parsed = json.loads(m.group(1)) if m else {
            "pros": [{"point": "See full response", "strength": "medium"}],
            "cons": [],
            "ambiguities": [],
            "missing_info": [],
            "key_tension": raw,
        }

    return AgentOutput(
        agent_id=f"critic-{agent_idx}",
        role="critic",
        content=raw,
        metadata={
            "pros": parsed.get("pros", []),
            "cons": parsed.get("cons", []),
            "ambiguities": parsed.get("ambiguities", []),
            "missing_info": parsed.get("missing_info", []),
            "key_tension": parsed.get("key_tension", ""),
            "model": model,
        },
    )
