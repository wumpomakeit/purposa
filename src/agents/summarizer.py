"""Summarizer agent — produces a neutral plain-language summary of the proposal."""
from __future__ import annotations

import json

from src.agents.base import AgentOutput, call_llm
from src.config import get_settings
from src.snapshot.client import ProposalData

SYSTEM_PROMPT = """You are a neutral DAO governance analyst. Your job is to summarize
governance proposals clearly and accurately, without injecting personal opinion.

Return your response as a JSON object with these fields:
{
  "summary": "<2-4 paragraph neutral summary of what the proposal is, why it was created, and what it would change>",
  "tldr": "<one sentence TL;DR>",
  "key_actors": ["<list of key addresses or entities mentioned>"],
  "timeline": "<key dates and deadlines>",
  "voting_context": "<brief note on current vote state and quorum>"
}

Be factual. Do not recommend For or Against. Use plain language — assume the reader
is a token holder who hasn't read governance forums."""


async def run_summarizer(proposal: ProposalData, agent_idx: int = 0) -> AgentOutput:
    settings = get_settings()
    model = settings.primary_model if agent_idx == 0 else settings.secondary_model
    prefer = "openai"

    raw = await call_llm(
        system=SYSTEM_PROMPT,
        user=proposal.to_context_string(),
        model=model,
        prefer=prefer,
        temperature=0.2,
        max_tokens=1500,
    )

    # Try to parse JSON; fall back to raw text
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Extract JSON block if wrapped in markdown
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        parsed = json.loads(m.group(1)) if m else {"summary": raw, "tldr": "", "key_actors": [], "timeline": "", "voting_context": ""}

    return AgentOutput(
        agent_id=f"summarizer-{agent_idx}",
        role="summarizer",
        content=parsed.get("summary", raw),
        metadata={
            "tldr": parsed.get("tldr", ""),
            "key_actors": parsed.get("key_actors", []),
            "timeline": parsed.get("timeline", ""),
            "voting_context": parsed.get("voting_context", ""),
            "model": model,
        },
    )
