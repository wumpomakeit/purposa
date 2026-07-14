"""Tests for the Snapshot GraphQL client."""
from __future__ import annotations

import pytest

from src.snapshot.client import _extract_proposal_id


def test_extract_from_full_url():
    url = "https://snapshot.org/#/uniswap.eth/proposal/0xabc123def456"
    assert _extract_proposal_id(url) == "0xabc123def456"


def test_extract_from_short_url():
    url = "https://snapshot.org/proposal/0xdeadbeef"
    assert _extract_proposal_id(url) == "0xdeadbeef"


def test_extract_from_id_only():
    proposal_id = "0xabc123"
    assert _extract_proposal_id(proposal_id) == "0xabc123"


def test_extract_strips_trailing_slash():
    url = "0xabc123/"
    assert _extract_proposal_id(url) == "0xabc123"
