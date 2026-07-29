"""
LLM client — abstraction cho OpenAI-compatible API.

Hỗ trợ: OpenAI, DeepSeek, Ollama, Anthropic (qua adapter).
Fallback chain: thử provider chính → fallback → rule-based.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from trading_agent.config.loader import config

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    tokens_used: int = 0
    error: str | None = None


class LLMError(Exception):
    """Raised when all LLM providers fail."""


# ── Provider configs ─────────────────────────────────────────────────────


PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "env_key": None,  # no API key needed
    },
}


def _get_provider_url(provider: str, base_url_override: str | None = None) -> str:
    """Get base URL for provider, respecting overrides from config."""
    if base_url_override:
        return base_url_override
    return PROVIDER_CONFIGS.get(provider, {}).get("base_url", "http://localhost:11434/v1")


def _get_api_key(provider: str) -> str | None:
    cfg = PROVIDER_CONFIGS.get(provider, {})
    env_key = cfg.get("env_key")
    if env_key:
        return os.environ.get(env_key)
    return None


# ── Chat completion ──────────────────────────────────────────────────────


def chat(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> LLMResponse:
    """Send a chat completion request with fallback support.

    Uses config's ``llm`` section as default. Falls back through the
    configured fallback chain if the primary provider fails.
    """
    provider = provider or config.llm_provider
    model = model or config.llm_model
    temperature = temperature if temperature is not None else config.llm_temperature
    max_tokens = max_tokens or config.llm_max_tokens
    timeout = timeout if timeout is not None else config.llm_timeout

    # Build provider chain: primary + fallbacks
    providers_to_try = [(provider, model, None)]
    for fb in config.llm_fallback:
        providers_to_try.append(
            (
                fb.get("provider", "ollama"),
                fb.get("model", "qwen2.5:7b"),
                fb.get("base_url"),
            )
        )

    last_error = None
    for prov, mdl, base_url_override in providers_to_try:
        try:
            return _try_provider(
                prov, mdl, messages,
                temperature, max_tokens, timeout,
                base_url_override=base_url_override,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"LLM provider {prov}/{mdl} failed: {e}")
            continue

    # All providers failed → return empty response with error
    logger.error(f"All LLM providers failed. Last error: {last_error}")
    raise LLMError(f"All LLM providers failed. Last error: {last_error}")


def _try_provider(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    base_url_override: str | None = None,
) -> LLMResponse:
    base_url = _get_provider_url(provider, base_url_override)
    api_key = _get_api_key(provider)

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    url = urljoin(base_url.rstrip("/") + "/", "chat/completions")
    logger.debug(f"LLM request to {url} with model={model}")

    resp = httpx.post(
        url,
        headers=headers,
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    msg = data["choices"][0]["message"]
    content = msg.get("content")
    # Reasoning models (DeepSeek V4 Flash) put text in "reasoning" field
    if not content:
        content = msg.get("reasoning", "")
    usage = data.get("usage", {})
    tokens = usage.get("total_tokens", 0)

    return LLMResponse(
        content=content.strip(),
        provider=provider,
        model=data.get("model", model),
        tokens_used=tokens,
    )


# ── Structured output helpers ────────────────────────────────────────────


SYSTEM_PROMPT_BASE = """You are a professional trading analyst in a multi-agent system.

Rules:
- Be concise and data-driven.
- Always base your analysis on the provided data, not general knowledge.
- Output in JSON format with keys: signal, confidence, reasoning, details.
- signal: "BUY" | "SELL" | "HOLD"
- confidence: 0.0 to 1.0
- reasoning: short explanation (1-2 sentences)
- details: dict with any additional data relevant to your role"""


def ask_agent(
    system_prompt: str,
    user_prompt: str,
    **kwargs,
) -> dict[str, Any]:
    """Send role-specific prompt, parse JSON response.

    Returns parsed dict. Falls back to rule-based if LLM unavailable.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = chat(messages, **kwargs)
        # Try to extract JSON from response
        text = response.content.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if "```" in text:
                text = text.split("```")[0]
        # Find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

        parsed = json.loads(text)
        return parsed
    except (json.JSONDecodeError, LLMError) as e:
        logger.warning(f"LLM parsing failed ({e}), returning fallback")
        return _json_fallback(system_prompt, user_prompt)


def _json_fallback(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Rule-based fallback when LLM is unavailable."""
    return {
        "signal": "HOLD",
        "confidence": 0.3,
        "reasoning": "LLM unavailable — conservative HOLD by default",
        "details": {},
    }
