"""Base agent class and shared LLM client factory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config import get_settings


@dataclass
class AgentOutput:
    agent_id: str
    role: str
    content: str
    metadata: dict[str, Any]


def _make_openai_client():
    try:
        from openai import AsyncOpenAI
        settings = get_settings()
        return AsyncOpenAI(api_key=settings.openai_api_key)
    except ImportError:
        return None


def _make_anthropic_client():
    try:
        import anthropic
        settings = get_settings()
        return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    except ImportError:
        return None


async def call_openai(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    settings = get_settings()
    client = _make_openai_client()
    if not client:
        raise RuntimeError("openai package not installed")
    model = model or settings.primary_model
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


async def call_anthropic(
    system: str,
    user: str,
    model: str = "claude-3-5-haiku-20241022",
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    client = _make_anthropic_client()
    if not client:
        raise RuntimeError("anthropic package not installed")
    response = await client.messages.create(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.content[0].text if response.content else ""


async def call_llm(
    system: str,
    user: str,
    prefer: str = "openai",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """Call an LLM, falling back between providers."""
    settings = get_settings()
    if prefer == "anthropic" and settings.anthropic_api_key:
        return await call_anthropic(system, user, temperature=temperature, max_tokens=max_tokens)
    if settings.openai_api_key:
        return await call_openai(system, user, model=model, temperature=temperature, max_tokens=max_tokens)
    if settings.anthropic_api_key:
        return await call_anthropic(system, user, temperature=temperature, max_tokens=max_tokens)
    raise RuntimeError(
        "No LLM API key configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env"
    )
