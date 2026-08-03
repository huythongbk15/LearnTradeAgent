"""Multi-provider LLM pool with failover, quota tracking and health checks.

Thay thế LLMClient đơn-provider: pool nhiều free-tier provider lại với nhau,
tự động chọn provider khả dụng nhất và failover khi bị 429/timeout.

Interface giữ nguyên `chat()` để tương thích với swarm agents:
    pool = create_llm_pool()
    text = await pool.chat([{"role": "user", "content": "hi"}])

Cấu hình mặc định đọc từ env (xem .env.example):
    GROQ_API_KEY, CEREBRAS_API_KEY, NVIDIA_API_KEY, OPENROUTER_API_KEY,
    GITHUB_TOKEN, MISTRAL_API_KEY, GOOGLE_API_KEY, OPENCODE_API_KEY ...

Quota tracking: lưu tại data/llm_quota.json, reset theo ngày UTC.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# OpenAI-compatible providers (dùng chung /chat/completions)
# priority thấp = ưu tiên cao hơn. No key = không cần api_key.
DEFAULT_PROVIDERS = [
    {
        "name": "opencode",
        "base_url": "https://opencode.ai/zen/v1",
        "model": "deepseek-v4-flash-free",
        "api_key_env": "OPENCODE_API_KEY",  # optional — free tier không cần key
        "priority": 10,
        "daily_limit": 200,
        "enabled": True,
    },
    {
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "priority": 20,
        "daily_limit": 1000,
        "enabled": True,
    },
    {
        "name": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama3.1-70b",
        "api_key_env": "CEREBRAS_API_KEY",
        "priority": 30,
        "daily_limit": 1000,
        "enabled": True,
    },
    {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "api_key_env": "OPENROUTER_API_KEY",
        "priority": 40,
        "daily_limit": 200,
        "enabled": True,
    },
    {
        "name": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "z-ai/glm-5.2",
        "api_key_env": "NVIDIA_API_KEY",
        "priority": 50,
        "daily_limit": 1000,
        "enabled": True,
    },
    {
        "name": "github-models",
        "base_url": "https://models.github.ai/inference",
        "model": "Phi-4",
        "api_key_env": "GITHUB_TOKEN",
        "priority": 60,
        "daily_limit": 50,
        "enabled": True,
    },
    {
        "name": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "open-mistral-7b",
        "api_key_env": "MISTRAL_API_KEY",
        "priority": 70,
        "daily_limit": 500,
        "enabled": True,
    },
    {
        "name": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.5-flash",
        "api_key_env": "GOOGLE_API_KEY",
        "priority": 80,
        "daily_limit": 1500,
        "enabled": True,
    },
    {
        "name": "ovhcloud",
        "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "model": "Meta-Llama-3_3-70B-Instruct",
        "api_key_env": None,  # anonymous tier, không cần key
        "priority": 90,
        "daily_limit": 100,
        "enabled": True,
    },
    {
        "name": "ollama",
        "base_url": "http://localhost:11434",
        "model": "qwen3:8b",
        "api_key_env": None,
        "priority": 100,
        "daily_limit": 10000,
        "enabled": False,  # local — bật thủ công qua LLM_POOL_OLLAMA=1
    },
]

# HTTP status → hành vi
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
COOLDOWN_SECONDS = 60          # sau 429
FAIL_COOLDOWN_SECONDS = 300    # sau lỗi liên tiếp
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class PoolProvider:
    """Cấu hình một provider trong pool."""
    name: str
    base_url: str
    model: str
    api_key_env: Optional[str] = None
    priority: int = 50
    daily_limit: int = 200
    enabled: bool = True
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_error: str = ""

    @property
    def api_key(self) -> Optional[str]:
        if not self.api_key_env:
            return None
        return os.getenv(self.api_key_env) or None

    @property
    def requires_key(self) -> bool:
        return bool(self.api_key_env and not self.api_key)

    def in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until


class QuotaTracker:
    """Track requests per provider per ngày, persist JSON. Reset theo UTC."""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            # Mặc định: data/llm_quota.json (tương đối với project root)
            project_root = Path(__file__).resolve().parents[2]  # trading/llm/pool.py → project
            path = str(project_root / "data" / "llm_quota.json")
        self.path = Path(path)
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("quota file corrupt, reset: %s", self.path)
                self._data = {}
        self._data.setdefault("days", {})

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2))
        except OSError as exc:
            logger.warning("cannot persist quota: %s", exc)

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def record(self, provider: str, success: bool = True) -> None:
        day = self._today()
        days = self._data.setdefault("days", {})
        entry = days.setdefault(day, {}).setdefault(provider, {"requests": 0, "errors": 0})
        entry["requests"] += 1
        if not success:
            entry["errors"] += 1
        self._save()

    def usage_today(self, provider: str) -> int:
        day = self._today()
        entry = self._data.get("days", {}).get(day, {}).get(provider, {})
        return int(entry.get("requests", 0))

    def remaining(self, provider: PoolProvider) -> int:
        return max(0, provider.daily_limit - self.usage_today(provider.name))

    def snapshot(self) -> dict:
        """Trả về usage hôm nay cho tất cả providers (để dashboard/log)."""
        return dict(self._data.get("days", {}).get(self._today(), {}))


class LLMPool:
    """Multi-provider LLM pool.

    Routing: ưu tiên theo `priority`, sau đó lọc các provider:
    - chưa hết quota ngày
    - không trong cooldown
    - không yêu cầu key bị thiếu
    Failover: 429/timeout/5xx → cooldown provider đó, thử provider kế tiếp.
    """

    def __init__(
        self,
        providers: Optional[list[PoolProvider]] = None,
        quota: Optional[QuotaTracker] = None,
        timeout: int = 30,
    ):
        self.timeout = timeout
        self.quota = quota or QuotaTracker()
        self.providers = providers or build_default_providers()
        self._session: Optional[aiohttp.ClientSession] = None
        self.last_provider: Optional[str] = None
        logger.info(
            "LLMPool ready: %d providers (%s)",
            len(self.providers),
            ", ".join(p.name for p in self.providers),
        )

    # ── session ──────────────────────────────────────────────
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── routing ───────────────────────────────────────────────
    def _candidates(self) -> list[PoolProvider]:
        """Sắp xếp các provider khả dụng theo priority."""
        today = self._data_today()
        usable = []
        for p in self.providers:
            if not p.enabled:
                continue
            if p.requires_key:
                continue
            if p.in_cooldown():
                continue
            if self.quota.remaining(p) <= 0:
                continue
            usable.append(p)
        usable.sort(key=lambda p: p.priority)
        return usable

    def _data_today(self) -> str:
        return self.quota._today()

    def mark_cooldown(self, provider: PoolProvider, status: int | None = None) -> None:
        provider.consecutive_failures += 1
        if status == 429:
            provider.cooldown_until = time.time() + COOLDOWN_SECONDS
            logger.warning("[llm] %s rate-limited (429), cooldown %ds", provider.name, COOLDOWN_SECONDS)
        elif provider.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            provider.cooldown_until = time.time() + FAIL_COOLDOWN_SECONDS
            logger.warning(
                "[llm] %s failed %dx, cooldown %ds",
                provider.name, provider.consecutive_failures, FAIL_COOLDOWN_SECONDS,
            )
        else:
            provider.cooldown_until = time.time() + min(COOLDOWN_SECONDS, 2 ** provider.consecutive_failures * 5)

    def mark_success(self, provider: PoolProvider) -> None:
        provider.consecutive_failures = 0
        provider.last_error = ""

    # ── main entry ────────────────────────────────────────────
    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Gửi request qua pool; failover qua các provider còn lại.

        Raises RuntimeError nếu tất cả providers đều fail (giống LLMClient).
        """
        candidates = self._candidates()
        if not candidates:
            raise RuntimeError(
                "LLMPool: no available provider (all exhausted, in cooldown, or missing keys)"
            )

        errors: list[str] = []
        for provider in candidates:
            try:
                text = await self._chat_provider(provider, messages, temperature, max_tokens)
                self.mark_success(provider)
                self.quota.record(provider.name, success=True)
                self.last_provider = provider.name
                return text
            except PoolRateLimitError as exc:
                self.mark_cooldown(provider, status=429)
                self.quota.record(provider.name, success=False)
                errors.append(f"{provider.name}: {exc}")
            except PoolError as exc:
                self.mark_cooldown(provider)
                self.quota.record(provider.name, success=False)
                errors.append(f"{provider.name}: {exc}")
            except Exception as exc:  # noqa: BLE001 — failover safety
                self.mark_cooldown(provider)
                self.quota.record(provider.name, success=False)
                errors.append(f"{provider.name}: {exc}")

        raise RuntimeError(f"LLMPool: all providers failed — {'; '.join(errors)}")

    async def _chat_provider(
        self,
        provider: PoolProvider,
        messages: list[dict],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        """Gọi 1 provider. Ném PoolError/PoolRateLimitError khi fail."""
        session = await self._get_session()

        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.1,
            "max_tokens": max_tokens if max_tokens is not None else 1000,
        }

        if provider.name == "ollama":
            url = f"{provider.base_url}/api/chat"
            payload = {
                "model": provider.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": payload["temperature"],
                    "num_predict": payload["max_tokens"],
                },
            }
            headers = {"Content-Type": "application/json"}
        else:
            url = f"{provider.base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if provider.api_key:
                headers["Authorization"] = f"Bearer {provider.api_key}"

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                text = await resp.text()
                raise PoolRateLimitError(f"{provider.name}: HTTP 429 — {text[:200]}")
            if resp.status != 200:
                text = await resp.text()
                raise PoolError(f"{provider.name}: HTTP {resp.status} — {text[:200]}")
            data = await resp.json()
            content = data["choices"][0]["message"].get("content") or ""
            if not content.strip():
                # Free tier hay trả 200 kèm content rỗng (reasoning consume hết token,
                # hoặc rate limit mềm). Coi như fail để pool failover provider khác.
                raise PoolError(f"{provider.name}: empty content — {str(data)[:200]}")
            return content

    # ── status / health ───────────────────────────────────────
    def status(self) -> dict:
        """Trạng thái hiện tại của pool (không gọi network)."""
        today = self._data_today()
        out = {"today": today, "providers": []}
        for p in self.providers:
            out["providers"].append({
                "name": p.name,
                "enabled": p.enabled,
                "requires_key": p.requires_key,
                "quota_remaining": self.quota.remaining(p),
                "in_cooldown": p.in_cooldown(),
                "last_error": p.last_error,
            })
        return out

    async def health(self) -> dict:
        """Probe từng provider bằng 1 request nhỏ. Dùng cho diagnostic."""
        results = {}
        for p in self.providers:
            if not p.enabled or p.requires_key:
                results[p.name] = {"ok": False, "reason": "disabled or missing key"}
                continue
            try:
                started = time.monotonic()
                text = await self._chat_provider(
                    p,
                    [{"role": "user", "content": "ping"}],
                    temperature=0,
                    max_tokens=1,
                )
                results[p.name] = {
                    "ok": True,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "preview": text[:40],
                }
            except Exception as exc:  # noqa: BLE001
                results[p.name] = {"ok": False, "reason": str(exc)[:120]}
        return results


class PoolError(RuntimeError):
    """Provider trả lỗi có thể retry qua provider khác."""


class PoolRateLimitError(PoolError):
    """Provider trả 429 — cần cooldown."""


def build_default_providers() -> list[PoolProvider]:
    """Xây danh sách providers từ DEFAULT_PROVIDERS + env overrides.

    - Provider thiếu key bắt buộc sẽ bị skip tự động trong _candidates().
    - LLM_POOL_OLLAMA=1 bật ollama local.
    """
    providers = []
    for spec in DEFAULT_PROVIDERS:
        enabled = spec.get("enabled", True)
        if spec["name"] == "ollama":
            enabled = os.getenv("LLM_POOL_OLLAMA", "0") == "1"
        providers.append(PoolProvider(
            name=spec["name"],
            base_url=spec["base_url"],
            model=spec["model"],
            api_key_env=spec.get("api_key_env"),
            priority=spec["priority"],
            daily_limit=spec["daily_limit"],
            enabled=enabled,
        ))
    return providers


def create_llm_pool(
    providers: Optional[list[PoolProvider]] = None,
    quota_path: Optional[str] = None,
) -> LLMPool:
    """Factory — dùng thay create_llm_client khi cần multi-provider failover."""
    if providers is None:
        providers = build_default_providers()
    quota = QuotaTracker(path=quota_path) if quota_path else QuotaTracker()
    return LLMPool(providers=providers, quota=quota)
