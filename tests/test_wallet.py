"""Tests for wallet/vote-signing helpers — pure logic, no live onchainos session required."""
from __future__ import annotations

import json

from src.api.routes import _build_self_serve_vote_instructions
from src.wallet.okx import build_snapshot_vote_typed_data


def test_typed_data_matches_snapshot_vote_spec():
    typed = build_snapshot_vote_typed_data(
        space_id="uniswap.eth",
        proposal_id="0xabc123",
        choice=1,
        voter_address="0x88f8d8a8426301159ae487d11ccd0538881296fa",
        reason="Voted via Purposa",
        timestamp=1700000000,
    )
    assert typed["domain"] == {"name": "snapshot", "version": "0.1.4"}
    assert typed["primaryType"] == "Vote"
    assert typed["message"] == {
        "from": "0x88f8d8a8426301159ae487d11ccd0538881296fa",
        "space": "uniswap.eth",
        "timestamp": 1700000000,
        "proposal": "0xabc123",
        "choice": 1,
        "reason": "Voted via Purposa",
        "app": "purposa",
        "metadata": "{}",
    }


def test_typed_data_defaults_timestamp_when_omitted():
    typed = build_snapshot_vote_typed_data(
        space_id="uniswap.eth",
        proposal_id="0xabc123",
        choice=1,
        voter_address="0x88f8...",
    )
    # Should default to "now" rather than raising or leaving it unset.
    assert isinstance(typed["message"]["timestamp"], int)
    assert typed["message"]["timestamp"] > 1_700_000_000


def test_self_serve_instructions_are_self_contained():
    """
    The self-serve text must give a technical caller everything they need
    to sign and submit independently — never referencing Purposa credentials,
    and always including a runnable onchainos command + curl command.
    """
    text = _build_self_serve_vote_instructions(
        proposal_space_id="uniswap.eth",
        proposal_id="0xabc123",
        choice=1,
        reason="Voted via Purposa",
        timestamp=1700000000,
    )
    assert "onchainos wallet sign-message" in text
    assert "hub.snapshot.org/api/msg" in text
    assert "<YOUR_WALLET_ADDRESS>" in text
    # Must never suggest sending any credential to Purposa.
    assert "api_key" not in text.lower()
    assert "secret_key" not in text.lower()
    assert "passphrase" not in text.lower()


def test_self_serve_instructions_embed_the_exact_signed_message():
    """
    The embedded EIP-712 message inside the instructions must match what
    was actually signed for this vote (same proposal/choice/reason/timestamp) —
    otherwise a caller following the instructions would produce a
    differently-shaped vote than the one Purposa itself cast.
    """
    text = _build_self_serve_vote_instructions(
        proposal_space_id="uniswap.eth",
        proposal_id="0xabc123",
        choice=2,
        reason="My custom reason",
        timestamp=1700000000,
    )
    assert '"proposal": "0xabc123"' in text
    assert '"choice": 2' in text
    assert '"reason": "My custom reason"' in text
    assert '"timestamp": 1700000000' in text
    assert '"space": "uniswap.eth"' in text


def test_self_serve_hub_payload_omits_signing_only_fields():
    """
    The curl example's "data" object must match what submit_vote() actually
    sends to Snapshot Hub (no `primaryType`, no `EIP712Domain` type entry) —
    those are signing-time-only fields the Hub API does not expect.
    """
    text = _build_self_serve_vote_instructions(
        proposal_space_id="uniswap.eth",
        proposal_id="0xabc123",
        choice=1,
        reason="Voted via Purposa",
        timestamp=1700000000,
    )
    # Split into "sign this payload" vs "submit this to Hub" sections and
    # check the curl-destined JSON specifically excludes primaryType.
    hub_section = text.split("curl -X POST")[1]
    assert "primaryType" not in hub_section
    assert "EIP712Domain" not in hub_section


def test_typed_data_json_serializable():
    typed = build_snapshot_vote_typed_data(
        space_id="uniswap.eth",
        proposal_id="0xabc123",
        choice=1,
        voter_address="0x88f8...",
        timestamp=1700000000,
    )
    # Should round-trip cleanly — this is embedded verbatim in API responses.
    assert json.loads(json.dumps(typed)) == typed
