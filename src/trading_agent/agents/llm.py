"""
LLM client — abstraction cho OpenAI-compatible API.

Hỗ trợ: OpenAI, DeepSeek, Ollama, Anthropic (qua adapter).
Fallback chain: thử provider chính → fallback → rule-based.
Cache: TTL-based disk cache để giảm chi phí LLM calls lặp lại.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from trading_agent.config.loader import config

logger = logging.getLogger(__name__)

# ── LLM Response Cache ────────────────────────────────────────────────────

_CACHE_DIR = Path.home() / ".cache" / "trading_agent" / "llm"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_TTL_SECONDS = 3600  # 1 hour default
_MAX_CACHE_SIZE_MB = 100


def _cache_key(messages: list[dict[str, str]], **kwargs) -> str:
    """Generate deterministic cache key from messages and params."""
    # Include provider/model in key since different models give different results
    key_parts = [
        kwargs.get("provider", ""),
        kwargs.get("model", ""),
        str(kwargs.get("temperature", 0.7)),
        str(kwargs.get("max_tokens", 500)),
    ]
    for msg in messages:
        key_parts.append(msg.get("role", ""))
        key_parts.append(msg.get("content", ""))
    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _get_cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.pkl"


def _cache_get(key: str) -> Any | None:
    """Get cached response if not expired."""
    path = _get_cache_path(key)
    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            cached = pickle.load(f)
        if time.time() - cached["timestamp"] < _CACHE_TTL_SECONDS:
            logger.debug(f"LLM cache HIT: {key[:8]}...")
            return cached["data"]
        else:
            path.unlink()  # expired
    except Exception:
        pass
    return None


def _cache_set(key: str, data: Any) -> None:
    """Cache response with timestamp."""
    path = _get_cache_path(key)
    try:
        with open(path, "wb") as f:
            pickle.dump({"timestamp": time.time(), "data": data}, f)
        logger.debug(f"LLM cache SET: {key[:8]}...")
    except Exception as e:
        logger.warning(f"LLM cache write failed: {e}")


def _cache_prune() -> None:
    """Remove expired/old cache entries if over size limit."""
    try:
        files = list(_CACHE_DIR.glob("*.pkl"))
        total_size = sum(f.stat().st_size for f in files)
        if total_size > _MAX_CACHE_SIZE_MB * 1024 * 1024:
            # Remove oldest first
            files.sort(key=lambda f: f.stat().st_mtime)
            for f in files[: len(files) // 2]:
                f.unlink()
            logger.info(f"Pruned LLM cache: {total_size / 1024 / 1024:.1f}MB → under limit")
    except Exception as e:
        logger.warning(f"LLM cache prune failed: {e}")


# Shared fallback (accessible from both branches)
def _json_fallback(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Rule-based fallback when LLM is unavailable."""
    return {
        "signal": "HOLD",
        "confidence": 0.3,
        "reasoning": "LLM unavailable — conservative HOLD by default",
        "details": {},
    }


# Fast skip for local testing
def llm_enabled() -> bool:
    """True nếu pipeline LLM bật (USE_LLM != 'false')."""
    return os.getenv("USE_LLM", "true").lower() != "false"


if os.getenv("USE_LLM", "true").lower() == "false":
    class LLMError(Exception):
        """Raised when all LLM providers fail."""

    def chat(*args, **kwargs) -> None:
        raise LLMError("LLM disabled via USE_LLM=false")

    def ask_agent(*args, **kwargs) -> dict[str, Any]:
        # Return fallback when LLM disabled
        return _json_fallback(args[0] if args else "", args[1] if len(args) > 1 else "")

else:
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
        "opencode": {
            "base_url": "https://opencode.ai/zen/v1",
            "env_key": None,  # no API key needed — free tier
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "env_key": "GROQ_API_KEY",
        },
        "nvidia": {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "env_key": "NVIDIA_API_KEY",
        },
        "cerebras": {
            "base_url": "https://api.cerebras.ai/v1",
            "env_key": "CEREBRAS_API_KEY",
        },
        "cohere": {
            "base_url": "https://api.cohere.com/v2",
            "env_key": "COHERE_API_KEY",
        },
        "cloudflare": {
            "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run",
            "env_key": "CLOUDFLARE_API_KEY",
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
        use_cache: bool = True,
    ) -> LLMResponse:
        """Send a chat completion request with fallback support.

        Uses config's ``llm`` section as default. Falls back through the
        configured fallback chain if the primary provider fails.

        Args:
            messages: List of message dicts with role and content
            provider: Override primary provider (default: config.llm_provider)
            model: Override model (default: config.llm_model)
            temperature: Override temperature (default: config.llm_temperature)
            max_tokens: Override max tokens (default: config.llm_max_tokens)
            timeout: Override timeout (default: config.llm_timeout)
            use_cache: Whether to use response caching (default: True)

        Returns:
            LLMResponse with content, provider, model, tokens_used
        """
        provider = provider or config.llm_provider
        model = model or config.llm_model
        temperature = temperature if temperature is not None else config.llm_temperature
        max_tokens = max_tokens or config.llm_max_tokens
        timeout = timeout if timeout is not None else config.llm_timeout

        # Get model_fallback for the primary provider (if any)
        model_fallback = getattr(config, 'llm_model_fallback', [])

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

        # Check cache first (only for primary provider/model)
        cache_kwargs = {"provider": provider, "model": model,
                        "temperature": temperature, "max_tokens": max_tokens}
        cache_key = _cache_key(messages, **cache_kwargs) if use_cache else None

        if use_cache and cache_key:
            cached = _cache_get(cache_key)
            if cached:
                logger.info(f"LLM cache HIT for {provider}/{model}")
                return LLMResponse(
                    content=cached["content"],
                    provider=cached["provider"],
                    model=cached["model"],
                    tokens_used=cached.get("tokens_used", 0),
                )

        last_error = None
        for prov, mdl, base_url_override in providers_to_try:
            # Try primary model
            try:
                response = _try_provider(
                    prov, mdl, messages,
                    temperature, max_tokens, timeout,
                    base_url_override=base_url_override,
                )
                # Cache successful response (primary provider only)
                if use_cache and cache_key and prov == provider and mdl == model:
                    _cache_set(cache_key, {
                        "content": response.content,
                        "provider": response.provider,
                        "model": response.model,
                        "tokens_used": response.tokens_used,
                    })
                    _cache_prune()
                return response
            except Exception as e:
                last_error = e
                logger.warning(f"LLM provider {prov}/{mdl} failed: {e}")

                # If this is the primary provider and has model_fallback, try those
                if prov == provider and model_fallback:
                    logger.info(f"Trying {len(model_fallback)} model fallback(s) for {prov}...")
                    for fallback_model in model_fallback:
                        try:
                            logger.info(f"Trying model fallback: {fallback_model}")
                            response = _try_provider(
                                prov, fallback_model, messages,
                                temperature, max_tokens, timeout,
                                base_url_override=base_url_override,
                            )
                            if use_cache and cache_key:
                                _cache_set(cache_key, {
                                    "content": response.content,
                                    "provider": response.provider,
                                    "model": response.model,
                                    "tokens_used": response.tokens_used,
                                })
                                _cache_prune()
                            return response
                        except Exception as e2:
                            logger.warning(f"Model fallback {prov}/{fallback_model} failed: {e2}")
                            last_error = e2
                            continue

                continue

        # All providers failed → raise error
        logger.error(f"All LLM providers failed. Last error: {last_error}")
        raise LLMError(f"All LLM providers failed. Last error: {last_error}")


    def _ensure_reasoning_content(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Trả về messages mới: thêm `reasoning_content=" "` vào mỗi assistant
        message thiếu. DeepSeek V4 Flash (thinking mode) trả 400 nếu thiếu field
        này trong history multi-turn.

        Chỉ copy khi cần để tránh rác; giữ nguyên dict nếu không cần inject.
        """
        need_inject = any(
            m.get("role") == "assistant" and "reasoning_content" not in m
            for m in messages
        )
        if not need_inject:
            return messages
        out = []
        for m in messages:
            new = dict(m)
            if new.get("role") == "assistant" and "reasoning_content" not in new:
                new["reasoning_content"] = " "
            out.append(new)
        return out


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

        # DeepSeek (and other thinking-mode via OpenAI-compat) yêu cầu MỌI
        # assistant message trong history phải kèm reasoning_content. Nếu
        # thiếu → trả 400. Inject trước để tránh lỗi vô ích.
        work_messages = _ensure_reasoning_content(messages)

        # Retry loop riêng: 400 reasoning_content → inject lại và thử cùng
        # model; các lỗi transient (429/5xx) để chat()/fallback xử lý.
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            body = {
                "model": model,
                "messages": work_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            url = urljoin(base_url.rstrip("/") + "/", "chat/completions")
            logger.debug(f"LLM request to {url} with model={model} (attempt {attempt})")

            try:
                resp = httpx.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                raise

            # Nếu 400 do thiếu reasoning_content → inject và retry cùng model
            if resp.status_code == 400 and "reasoning_content" in resp.text:
                if attempt < 3:
                    logger.warning(
                        "[llm] %s needs reasoning_content (400) — injecting & retry %d/3",
                        model, attempt,
                    )
                    work_messages = _ensure_reasoning_content(work_messages)
                    last_exc = None
                    continue
                raise httpx.HTTPStatusError(
                    f"{model} still missing reasoning_content after retries",
                    request=resp.request, response=resp,
                )

            if resp.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"LLM {model} HTTP {resp.status_code}", request=resp.request, response=resp,
                )

            data = resp.json()

            msg = data["choices"][0]["message"]
            content = msg.get("content", "")
            # Reasoning models (DeepSeek V4 Flash) may put text in reasoning_content
            if not content:
                content = msg.get("reasoning_content", "") or msg.get("reasoning", "")
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)

            return LLMResponse(
                content=content.strip(),
                provider=provider,
                model=data.get("model", model),
                tokens_used=tokens,
            )

        raise LLMError(f"LLM provider {provider}/{model} failed after reasoning retries: {last_exc}")


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

# ─── End USE_LLM conditional block ───


# ── Deterministic Backtest Mode ────────────────────────────────────────────

_BACKTEST_MODE = False
_BACKTEST_CONFIG: dict[str, Any] = {}


def enable_backtest_mode(
    provider: str = "opencode",
    model: str = "deepseek-v4-flash-free",
    temperature: float = 0.0,
    max_tokens: int = 500,
    seed: int = 0,
    use_cache: bool = False,
) -> None:
    """
    Enable deterministic LLM mode for backtesting.
    
    This forces:
    - Fixed provider/model (no fallback chain)
    - Temperature = 0 (deterministic)
    - Fixed seed (if supported by provider)
    - No caching (to ensure reproducibility)
    - Single attempt per call
    
    Args:
        provider: LLM provider to use (default: opencode for free tier)
        model: Model name
        temperature: Must be 0 for deterministic output
        max_tokens: Token limit
        seed: Random seed (0 = deterministic if provider supports it)
        use_cache: False to disable caching
    """
    global _BACKTEST_MODE, _BACKTEST_CONFIG
    _BACKTEST_MODE = True
    _BACKTEST_CONFIG = {
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "seed": seed,
        "use_cache": use_cache,
        "timeout": 30,
    }
    logger.info(f"LLM backtest mode enabled: {provider}/{model}, temp={temperature}, seed={seed}")


def disable_backtest_mode() -> None:
    """Disable deterministic backtest mode."""
    global _BACKTEST_MODE
    _BACKTEST_MODE = False
    logger.info("LLM backtest mode disabled")


def is_backtest_mode() -> bool:
    """Check if backtest mode is active."""
    return _BACKTEST_MODE


def backtest_chat(
    messages: list[dict[str, str]],
    **kwargs,
) -> LLMResponse:
    """
    Chat completion in deterministic backtest mode.
    
    Ignores fallback chain, uses fixed provider/model, temperature=0, no cache.
    """
    if not _BACKTEST_MODE:
        return chat(messages, **kwargs)
    
    cfg = _BACKTEST_CONFIG.copy()
    cfg.update(kwargs)  # Allow override of some params
    return chat(
        messages,
        provider=cfg["provider"],
        model=cfg["model"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        timeout=cfg["timeout"],
        use_cache=cfg["use_cache"],
    )


def backtest_ask_agent(
    system_prompt: str,
    user_prompt: str,
    **kwargs,
) -> dict[str, Any]:
    """Structured output in deterministic backtest mode."""
    if not _BACKTEST_MODE:
        return ask_agent(system_prompt, user_prompt, **kwargs)
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    
    try:
        response = backtest_chat(messages, **kwargs)
        text = response.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if "```" in text:
                text = text.split("```")[0]
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        return json.loads(text)
    except (json.JSONDecodeError, LLMError) as e:
        logger.warning(f"Backtest LLM parsing failed ({e}), returning fallback")
        return _json_fallback(system_prompt, user_prompt)