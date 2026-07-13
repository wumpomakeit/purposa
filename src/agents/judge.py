"""Judge agent — reconciles all sub-agent outputs into a final verdict."""
from __future__ import annotations

import json
import re
from typing import Any

from src.agents.base import AgentOutput, call_llm
from src.config import get_settings
from src.snapshot.client import ProposalData


def _build_judge_prompt(
    proposal: ProposalData,
    summarizer_output: AgentOutput,
    critic_output: AgentOutput,
    risk_output: AgentOutput,
) -> str:
    pros = critic_output.metadata.get("pros", [])
    cons = critic_output.metadata.get("cons", [])
    risk_flags = risk_output.metadata.get("risk_flags", [])

    pros_fmt = "\n".join(
        f"  [{p.get('strength','?').upper()}] {p.get('point','')}" for p in pros
    )
    cons_fmt = "\n".join(
        f"  [{c.get('strength','?').upper()}] {c.get('point','')}" for c in cons
    )
    flags_fmt = "\n".join(
        f"  [{f.get('severity','?').upper()}] {f.get('flag','')} — {f.get('detail','')}"
        for f in risk_flags
    )

    return f"""You are the final judge in a multi-agent DAO governance analysis panel.
Three specialist agents have analyzed this proposal. Synthesize their findings into a
final verdict that a token holder can act on.

## Proposal Overview
{summarizer_output.metadata.get('tldr', proposal.title)}

## Arguments For
{pros_fmt or '  (none identified)'}

## Arguments Against
{cons_fmt or '  (none identified)'}

## Risk Flags
{flags_fmt or '  (none identified)'}
Overall risk level: {risk_output.metadata.get('overall_risk_level', 'unknown').upper()}

## Agent Disagreements
- Summarizer key tension: {critic_output.metadata.get('key_tension', 'N/A')}
- Risk summary: {risk_output.metadata.get('risk_summary', 'N/A')}

## Current Vote State
Scores total: {proposal.scores_total:.2f}
Quorum: {proposal.quorum:.2f} ({proposal.quorum_percentage:.1f}% reached)
Leading choice: {proposal.leading_choice.label if proposal.leading_choice else 'N/A'}

---
Produce a final verdict as JSON:
{{
  "recommendation": "For|Against|Abstain",
  "recommended_choice_index": <0-based index into choices array, or null for Abstain>,
  "confidence": <0.0–1.0>,
  "reasoning": "<2-3 sentence explanation of why this recommendation was reached>",
  "key_considerations": ["<top 3 factors a voter should weigh>"],
  "dissenting_view": "<strongest counterargument to the recommendation, if any>",
  "warnings": ["<any important caveats the voter must know before voting>"]
}}

The recommendation must be one of the available choices or Abstain.
Available choices: {proposal.choices}
Be decisive. A confidence below 0.5 should result in Abstain."""


SYSTEM_PROMPT = """You are the senior judge in a multi-agent governance analysis panel.
You receive inputs from specialist analysts and produce a final, auditable verdict.
Always return valid JSON matching the requested schema. Be calibrated about uncertainty
— high confidence (>0.8) only when the evidence strongly points one way."""


async def run_judge(
    proposal: ProposalData,
    summarizer_output: AgentOutput,
    critic_output: AgentOutput,
    risk_output: AgentOutput,
) -> AgentOutput:
    settings = get_settings()
    model = settings.judge_model

    user_prompt = _build_judge_prompt(
        proposal, summarizer_output, critic_output, risk_output
    )

    raw = await call_llm(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        model=model,
        prefer="nvidia",
        temperature=0.1,
        max_tokens=800,
        timeout=60.0,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        parsed = json.loads(m.group(1)) if m else {
            "recommendation": "Abstain",
            "recommended_choice_index": None,
            "confidence": 0.3,
            "reasoning": raw,
            "key_considerations": [],
            "dissenting_view": "",
            "warnings": ["Judge output could not be parsed; use raw reasoning above"],
        }

    return AgentOutput(
        agent_id="judge",
        role="judge",
        content=parsed.get("reasoning", raw),
        metadata={
            "recommendation": parsed.get("recommendation", "Abstain"),
            "recommended_choice_index": parsed.get("recommended_choice_index"),
            "confidence": float(parsed.get("confidence", 0.5)),
            "key_considerations": parsed.get("key_considerations", []),
            "dissenting_view": parsed.get("dissenting_view", ""),
            "warnings": parsed.get("warnings", []),
            "model": model,
        },
    )
