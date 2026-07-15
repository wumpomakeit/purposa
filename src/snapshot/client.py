"""Snapshot GraphQL client — fetches proposal data for analysis."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import get_settings

PROPOSAL_QUERY = """
query Proposal($id: String!) {
  proposal(id: $id) {
    id
    title
    body
    choices
    start
    end
    snapshot
    state
    scores
    scores_total
    scores_updated
    author
    space {
      id
      name
      about
      network
      symbol
    }
    quorum
    discussion
    votes
    type
    plugins
    strategies {
      name
      params
    }
  }
}
"""

VOTES_QUERY = """
query Votes($proposal: String!, $first: Int!, $skip: Int!) {
  votes(
    first: $first
    skip: $skip
    where: { proposal: $proposal }
    orderBy: "vp"
    orderDirection: desc
  ) {
    id
    voter
    vp
    vp_by_strategy
    choice
    reason
    created
  }
}
"""

SPACE_PROPOSALS_QUERY = """
query SpaceProposals($space: String!, $first: Int!) {
  proposals(
    first: $first
    where: { space: $space, state: "closed" }
    orderBy: "end"
    orderDirection: desc
  ) {
    id
    title
    state
    scores_total
    quorum
    end
  }
}
"""


@dataclass
class ProposalChoice:
    index: int
    label: str
    score: float
    percentage: float


@dataclass
class SpaceInfo:
    id: str
    name: str
    about: str
    network: str
    symbol: str


@dataclass
class ProposalData:
    """Full proposal data fetched from Snapshot."""

    id: str
    title: str
    body: str
    choices: list[str]
    choice_scores: list[ProposalChoice]
    author: str
    state: str
    start: int
    end: int
    scores_total: float
    quorum: float
    discussion: str
    votes_count: int
    vote_type: str
    space: SpaceInfo
    top_votes: list[dict[str, Any]] = field(default_factory=list)
    past_proposals_summary: list[dict[str, Any]] = field(default_factory=list)

    @property
    def quorum_met(self) -> bool:
        return self.scores_total >= self.quorum if self.quorum else True

    @property
    def quorum_percentage(self) -> float:
        if not self.quorum:
            return 100.0
        return min(100.0, (self.scores_total / self.quorum) * 100)

    @property
    def leading_choice(self) -> ProposalChoice | None:
        if not self.choice_scores:
            return None
        return max(self.choice_scores, key=lambda c: c.score)

    def to_context_string(self) -> str:
        """Format proposal into a context string for LLM analysis."""
        scores_fmt = "\n".join(
            f"  • {c.label}: {c.score:.2f} votes ({c.percentage:.1f}%)"
            for c in self.choice_scores
        )
        top_votes_fmt = "\n".join(
            f"  • {v['voter'][:8]}...{v['voter'][-4:]}: choice={v['choice_label']} "
            f"(vp={v['vp']:.2f}, reason: {v.get('reason') or 'none'})"
            for v in self.top_votes[:10]
        )
        return f"""
# Snapshot Governance Proposal

**Space**: {self.space.name} ({self.space.id})
**Proposal ID**: {self.id}
**Title**: {self.title}
**Author**: {self.author}
**State**: {self.state}
**Vote Type**: {self.vote_type}
**Discussion**: {self.discussion or "N/A"}

## Proposal Body
{self.body[:8000]}{"..." if len(self.body) > 8000 else ""}

## Voting Choices
{chr(10).join(f"  {i+1}. {c}" for i, c in enumerate(self.choices))}

## Current Results ({self.votes_count} votes)
{scores_fmt}
Total Voting Power: {self.scores_total:.2f}
Quorum: {self.quorum:.2f} ({self.quorum_percentage:.1f}% reached)
Quorum Met: {"Yes" if self.quorum_met else "No — quorum not reached"}

## Top Voters (by voting power)
{top_votes_fmt or "  No votes recorded yet"}
""".strip()


def _extract_proposal_id(url_or_id: str) -> str:
    """Extract Snapshot proposal ID from URL or return as-is."""
    patterns = [
        r"snapshot\.org/#/[^/]+/proposal/([a-zA-Z0-9]+)",
        r"snapshot\.org/proposal/([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url_or_id)
        if m:
            return m.group(1)
    # Assume it's already a proposal ID
    return url_or_id.strip().rstrip("/")


async def fetch_proposal(url_or_id: str) -> ProposalData:
    """Fetch a Snapshot proposal by URL or ID and return structured data."""
    settings = get_settings()
    proposal_id = _extract_proposal_id(url_or_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch proposal
        resp = await client.post(
            settings.snapshot_graphql_url,
            json={"query": PROPOSAL_QUERY, "variables": {"id": proposal_id}},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            raise ValueError(f"Snapshot GraphQL error: {data['errors']}")

        raw = data.get("data", {}).get("proposal")
        if not raw:
            raise ValueError(f"Proposal '{proposal_id}' not found on Snapshot")

        # Fetch top voters
        votes_resp = await client.post(
            settings.snapshot_graphql_url,
            json={
                "query": VOTES_QUERY,
                "variables": {"proposal": proposal_id, "first": 20, "skip": 0},
            },
            headers={"Content-Type": "application/json"},
        )
        votes_resp.raise_for_status()
        votes_data = votes_resp.json().get("data", {}).get("votes", [])

        # Map choice indices to labels
        choices = raw.get("choices", [])
        for v in votes_data:
            choice_idx = v.get("choice")
            if isinstance(choice_idx, int) and 1 <= choice_idx <= len(choices):
                v["choice_label"] = choices[choice_idx - 1]
            elif isinstance(choice_idx, dict):
                # Weighted voting
                v["choice_label"] = ", ".join(
                    f"{choices[int(k)-1]}:{pct}%"
                    for k, pct in choice_idx.items()
                    if int(k) <= len(choices)
                )
            else:
                v["choice_label"] = str(choice_idx)

        # Build choice scores
        raw_scores = raw.get("scores") or [0.0] * len(choices)
        scores_total = raw.get("scores_total") or 0.0
        choice_scores = [
            ProposalChoice(
                index=i,
                label=choices[i] if i < len(choices) else f"Choice {i+1}",
                score=raw_scores[i] if i < len(raw_scores) else 0.0,
                percentage=(
                    (raw_scores[i] / scores_total * 100)
                    if scores_total and i < len(raw_scores)
                    else 0.0
                ),
            )
            for i in range(len(choices))
        ]

        space_raw = raw.get("space") or {}
        space = SpaceInfo(
            id=space_raw.get("id", ""),
            name=space_raw.get("name", ""),
            about=space_raw.get("about", ""),
            network=space_raw.get("network", ""),
            symbol=space_raw.get("symbol", ""),
        )

        return ProposalData(
            id=raw["id"],
            title=raw["title"],
            body=raw.get("body", ""),
            choices=choices,
            choice_scores=choice_scores,
            author=raw.get("author", ""),
            state=raw.get("state", ""),
            start=raw.get("start", 0),
            end=raw.get("end", 0),
            scores_total=scores_total,
            quorum=raw.get("quorum") or 0.0,
            discussion=raw.get("discussion") or "",
            votes_count=raw.get("votes") or 0,
            vote_type=raw.get("type", "single-choice"),
            space=space,
            top_votes=votes_data,
        )


async def submit_vote(
    proposal_id: str,
    choice: int | dict,
    from_address: str,
    sig: str,
    space: str,
    timestamp: int,
    reason: str = "Voted via Purposa",
    sig_type: str = "eip712",
) -> dict[str, Any]:
    """
    Submit a vote to Snapshot Hub.

    `space`, `timestamp`, and `reason` MUST exactly match the values that
    were actually EIP-712 signed (see wallet.okx.build_snapshot_vote_typed_data) —
    Snapshot Hub recovers the signer from this exact `data` payload, so any
    mismatch (e.g. a different timestamp than what was signed) causes
    signature verification to fail server-side, even though `sig` itself
    is valid for the message it was actually computed over.
    """
    settings = get_settings()
    payload: dict[str, Any] = {
        "address": from_address,
        "sig": sig,
        "data": {
            "domain": {"name": "snapshot", "version": "0.1.4"},
            "types": {
                "Vote": [
                    {"name": "from", "type": "address"},
                    {"name": "space", "type": "string"},
                    {"name": "timestamp", "type": "uint64"},
                    {"name": "proposal", "type": "bytes32"},
                    {"name": "choice", "type": "uint32"},
                    {"name": "reason", "type": "string"},
                    {"name": "app", "type": "string"},
                    {"name": "metadata", "type": "string"},
                ]
            },
            "message": {
                "from": from_address,
                "space": space,
                "timestamp": timestamp,
                "proposal": proposal_id,
                "choice": choice,
                "reason": reason,
                "app": "purposa",
                "metadata": "{}",
            },
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.snapshot_hub_url}/api/msg",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()
