"""
OKX social sentiment enrichment for DAO governance analysis.

Uses the onchainos CLI to fetch real-time social signals (mention count,
bullish/bearish ratio, KOL chatter) for the governance token of the space
being analyzed. This enriches the LLM context with live social data.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)

# Map of known Snapshot space IDs to their governance token symbols
# (expanded on demand from the proposal's space.symbol field)
SPACE_TOKEN_MAP: dict[str, str] = {
    "uniswap.eth": "UNI",
    "aave.eth": "AAVE",
    "arbitrum.eth": "ARB",
    "ens.eth": "ENS",
    "gitcoin.eth": "GTC",
    "curve.eth": "CRV",
    "compound-governance.eth": "COMP",
    "opcollective.eth": "OP",
    "balancer.eth": "BAL",
    "1inch.eth": "1INCH",
    "sushigov.eth": "SUSHI",
    "dydxgov.eth": "DYDX",
    "snapshot.dcl.eth": "MANA",
    "lido-snapshot.eth": "LDO",
}


def _run_onchainos_social(*args: str) -> dict[str, Any]:
    import os

    settings = get_settings()
    env = os.environ.copy()
    env["OKX_API_KEY"] = settings.okx_api_key
    env["OKX_SECRET_KEY"] = settings.okx_secret_key
    env["OKX_PASSPHRASE"] = settings.okx_passphrase

    cmd = [settings.onchainos_bin, "social", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"onchainos social failed: {result.stderr}")
    return json.loads(result.stdout)


def get_token_symbol(space_id: str, space_symbol: str) -> str:
    """Resolve the governance token symbol for a Snapshot space."""
    if space_id in SPACE_TOKEN_MAP:
        return SPACE_TOKEN_MAP[space_id]
    # Use the space's own symbol if it looks like a token ticker (all caps, ≤10 chars)
    if space_symbol and space_symbol.isupper() and len(space_symbol) <= 10:
        return space_symbol
    return ""


def fetch_social_sentiment(token_symbol: str) -> dict[str, Any]:
    """
    Fetch real-time social sentiment for a token via OKX onchainos.

    Returns a dict with mention counts, bullish/bearish ratio, and sentiment label.
    Returns empty dict on failure (non-critical enrichment).
    """
    if not token_symbol:
        return {}
    try:
        raw = _run_onchainos_social(
            "sentiment-symbol",
            "--token-symbols", token_symbol,
            "--time-frame", "3",  # 24h window
        )
        details = raw.get("data", {}).get("details", [])
        if not details:
            return {}
        d = details[0]
        sentiment = d.get("sentiment", {})
        return {
            "token": token_symbol,
            "period": "24h",
            "mention_count": int(d.get("mentionCount", 0)),
            "x_mention_count": int(d.get("xMentionCount", 0)),
            "news_mention_count": int(d.get("newsMentionCount", 0)),
            "sentiment_label": sentiment.get("label", "neutral"),
            "bullish_ratio": float(sentiment.get("bullishRatio", 0)),
            "bearish_ratio": float(sentiment.get("bearishRatio", 0)),
        }
    except Exception as e:
        log.warning("social.fetch_failed", token=token_symbol, error=str(e))
        return {}


def format_sentiment_context(sentiment: dict[str, Any]) -> str:
    """Format social sentiment into a context string for LLM prompts."""
    if not sentiment:
        return ""
    return (
        f"\n## OKX Live Social Sentiment ({sentiment['token']}, {sentiment['period']})\n"
        f"- Overall: {sentiment['sentiment_label'].upper()}\n"
        f"- Mentions: {sentiment['mention_count']} total "
        f"({sentiment['x_mention_count']} on X, {sentiment['news_mention_count']} in news)\n"
        f"- Bullish: {sentiment['bullish_ratio']*100:.0f}% | "
        f"Bearish: {sentiment['bearish_ratio']*100:.0f}%\n"
    )
