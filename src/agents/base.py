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


def _make_nvidia_client():
    """OpenAI client pointed at NVIDIA NIM endpoint."""
    try:
        from openai import AsyncOpenAI
        settings = get_settings()
        return AsyncOpenAI(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
        )
    except ImportError:
        return None


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


async def call_nvidia(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    timeout: float = 60.0,
) -> str:
    """Call NVIDIA NIM (OpenAI-compatible) endpoint."""
    settings = get_settings()
    client = _make_nvidia_client()
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
        timeout=timeout,
    )
    return response.choices[0].message.content or ""


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
    prefer: str | None = None,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    timeout: float = 60.0,
) -> str:
    """
    Call an LLM. Priority order:
      1. prefer (if explicitly set)
      2. settings.llm_provider
      3. first available (nvidia → openai → anthropic)
    """
    settings = get_settings()
    provider = prefer or settings.llm_provider

    if provider == "nvidia" and settings.nvidia_api_key:
        return await call_nvidia(system, user, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)

    if provider == "openai" and settings.openai_api_key:
        return await call_openai(system, user, model=model, temperature=temperature, max_tokens=max_tokens)

    if provider == "anthropic" and settings.anthropic_api_key:
        return await call_anthropic(system, user, temperature=temperature, max_tokens=max_tokens)

    # Fallback chain
    if settings.nvidia_api_key:
        return await call_nvidia(system, user, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    if settings.openai_api_key:
        return await call_openai(system, user, model=model, temperature=temperature, max_tokens=max_tokens)
    if settings.anthropic_api_key:
        return await call_anthropic(system, user, temperature=temperature, max_tokens=max_tokens)

    raise RuntimeError(
        "No LLM API key configured. Set NVIDIA_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY in .env"
    )
