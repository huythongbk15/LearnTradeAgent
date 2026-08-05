"""LLM client for trading system."""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM configuration."""
    provider: str = "opencode"  # opencode, openrouter, openai, deepseek, ollama
    model: str = "deepseek-v4-flash-free"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 1000
    temperature: float = 0.1
    timeout: int = 30


class LLMClient:
    """Async LLM client with multiple provider support."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Send chat completion request."""
        provider = self.config.provider.lower()
        
        if provider == "opencode":
            return await self._chat_opencode(messages, temperature, max_tokens)
        elif provider == "openrouter":
            return await self._chat_openrouter(messages, temperature, max_tokens)
        elif provider == "openai":
            return await self._chat_openai(messages, temperature, max_tokens)
        elif provider == "deepseek":
            return await self._chat_deepseek(messages, temperature, max_tokens)
        elif provider == "ollama":
            return await self._chat_ollama(messages, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")
    
    async def _chat_opencode(
        self, 
        messages: list[dict], 
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> str:
        """OpenCode API (free DeepSeek V4 Flash)."""
        session = await self._get_session()
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        
        # OpenCode Zen — free tier, không bắt buộc API key.
        # Endpoint đúng là /zen/v1 (endpoint /api cũ đã trả HTML).
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        
        async with session.post(
            "https://opencode.ai/zen/v1/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenCode API error {resp.status}: {text}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    
    async def _chat_openrouter(
        self, 
        messages: list[dict], 
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> str:
        """OpenRouter API."""
        api_key = self.config.api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OpenRouter API key required")
        
        session = await self._get_session()
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/trading-agent",
            "X-Title": "Trading Agent",
        }
        
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenRouter API error {resp.status}: {text}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    
    async def _chat_openai(
        self, 
        messages: list[dict], 
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> str:
        """OpenAI API."""
        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key required")
        
        session = await self._get_session()
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        base = self.config.base_url or "https://api.openai.com/v1"
        
        async with session.post(
            f"{base}/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenAI API error {resp.status}: {text}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    
    async def _chat_deepseek(
        self, 
        messages: list[dict], 
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> str:
        """DeepSeek API."""
        api_key = self.config.api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DeepSeek API key required")
        
        session = await self._get_session()
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        base = self.config.base_url or "https://api.deepseek.com/v1"
        
        async with session.post(
            f"{base}/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"DeepSeek API error {resp.status}: {text}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    
    async def _chat_ollama(
        self, 
        messages: list[dict], 
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> str:
        """Ollama local API."""
        session = await self._get_session()
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature or self.config.temperature,
                "num_predict": max_tokens or self.config.max_tokens,
            }
        }
        
        base = self.config.base_url or "http://localhost:11434"
        
        async with session.post(
            f"{base}/api/chat",
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Ollama API error {resp.status}: {text}")
            data = await resp.json()
            return data["message"]["content"]


def create_llm_client(config: Optional[LLMConfig] = None):
    """Factory function to create LLM client.

    Mặc định trả về LLMPool (multi-provider failover + quota tracking) để các
    callers cũ tự động hưởng lợi. Tắt pool bằng LLM_POOL_ENABLED=0 để quay về
    LLMClient đơn provider như cũ.
    """
    if os.getenv("LLM_POOL_ENABLED", "1").lower() not in ("0", "false", "no"):
        from trading_agent.llm.pool import create_llm_pool
        return create_llm_pool()

    if config is None:
        # Auto-detect from environment
        provider = os.getenv("LLM_PROVIDER", "opencode")
        model = os.getenv("LLM_MODEL", "deepseek-v4-flash-free")
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        
        config = LLMConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    
    return LLMClient(config)