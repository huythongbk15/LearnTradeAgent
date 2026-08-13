"""Tests for LLMPool — multi-provider failover + quota tracking.

Chạy: python -m pytest tests/test_llm_pool.py -v
"""

import json
import time

import pytest

from trading_agent.llm.pool import (
    LLMPool,
    PoolProvider,
    QuotaTracker,
    build_default_providers,
    create_llm_pool,
)


# ── Fixtures ──────────────────────────────────────────────────


def make_provider(name: str, **kw) -> PoolProvider:
    base = dict(
        name=name,
        base_url="http://fake",
        model="test-model",
        priority=50,
        daily_limit=100,
        enabled=True,
    )
    base.update(kw)
    return PoolProvider(**base)


def make_pool(providers, quota_path: str) -> LLMPool:
    quota = QuotaTracker(path=quota_path)
    return LLMPool(providers=providers, quota=quota)


@pytest.fixture
def quota_path(tmp_path):
    return str(tmp_path / "llm_quota.json")


class FakeResp:
    def __init__(self, spec):
        self.status = spec.get("status", 200)
        self._json = spec.get("json", {})

    async def json(self):
        return self._json

    async def text(self):
        return json.dumps(self._json)

    # aiohttp-style async context manager: async with session.post(...) as resp
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Ghi nhận các POST, trả response theo danh sách spec."""

    def __init__(self, specs):
        self.specs = list(specs)
        self.posts = []

    def post(self, url, json=None, headers=None):  # sync — trả FakeResp trực tiếp
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self.specs:
            return FakeResp(self.specs.pop(0))
        return FakeResp(
            {"status": 200, "json": {"choices": [{"message": {"content": "default"}}]}}
        )

    @property
    def closed(self):
        return False


@pytest.fixture
def aiohttp_mock_factory(monkeypatch):
    """Thay LLMPool._get_session bằng FakeSession; trả về factory để inject specs."""

    def factory(specs):
        session = FakeSession(specs)

        async def fake_get_session(self):
            return session

        monkeypatch.setattr(LLMPool, "_get_session", fake_get_session)
        return session

    return factory


# ── QuotaTracker ──────────────────────────────────────────────


class TestQuotaTracker:
    def test_record_and_usage(self, quota_path):
        q = QuotaTracker(path=quota_path)
        q.record("groq", success=True)
        q.record("groq", success=True)
        q.record("groq", success=False)
        assert q.usage_today("groq") == 3

        # Persisted — tracker mới đọc lại được
        q2 = QuotaTracker(path=quota_path)
        assert q2.usage_today("groq") == 3

    def test_remaining(self, quota_path):
        q = QuotaTracker(path=quota_path)
        p = make_provider("groq", daily_limit=10)
        assert q.remaining(p) == 10
        q.record("groq")
        assert q.remaining(p) == 9

    def test_corrupt_file_resets(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        q = QuotaTracker(path=str(path))
        assert q.usage_today("x") == 0


# ── Routing / candidates ──────────────────────────────────────


class TestRouting:
    def test_priority_order(self, quota_path):
        pool = make_pool(
            [
                make_provider("low_pri", priority=90),
                make_provider("high_pri", priority=10),
            ],
            quota_path,
        )
        names = [p.name for p in pool._candidates()]
        assert names == ["high_pri", "low_pri"]

    def test_skips_missing_key(self, quota_path, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        pool = make_pool(
            [make_provider("groq", api_key_env="GROQ_API_KEY")], quota_path
        )
        assert pool._candidates() == []

    def test_skips_disabled(self, quota_path):
        pool = make_pool([make_provider("off", enabled=False)], quota_path)
        assert pool._candidates() == []

    def test_skips_cooldown(self, quota_path):
        p = make_provider("slow", priority=10)
        p.cooldown_until = time.time() + 999
        pool = make_pool([p, make_provider("ok", priority=20)], quota_path)
        names = [x.name for x in pool._candidates()]
        assert names == ["ok"]

    def test_skips_exhausted_quota(self, quota_path):
        pool = make_pool([make_provider("limit", daily_limit=1)], quota_path)
        pool.quota.record("limit")  # dùng hết quota
        assert pool._candidates() == []


# ── Failover ──────────────────────────────────────────────────


class TestFailover:
    async def test_failover_on_429(self, quota_path, aiohttp_mock_factory):
        pool = make_pool(
            [
                make_provider("a", priority=10, base_url="http://a"),
                make_provider("b", priority=20, base_url="http://b"),
            ],
            quota_path,
        )
        # a trả 429, b trả 200
        aiohttp_mock_factory(
            [
                {"status": 429, "json": {"error": "rate limited"}},
                {
                    "status": 200,
                    "json": {"choices": [{"message": {"content": "from-b"}}]},
                },
            ]
        )
        text = await pool.chat([{"role": "user", "content": "hi"}])
        assert text == "from-b"
        assert pool.last_provider == "b"
        # a vào cooldown
        assert pool.providers[0].in_cooldown()
        # quota: a fail, b success
        assert pool.quota.usage_today("a") == 1
        assert pool.quota.usage_today("b") == 1

    async def test_failover_on_500(self, quota_path, aiohttp_mock_factory):
        pool = make_pool(
            [
                make_provider("a", priority=10, base_url="http://a"),
                make_provider("b", priority=20, base_url="http://b"),
            ],
            quota_path,
        )
        aiohttp_mock_factory(
            [
                {"status": 500, "json": {}},
                {"status": 200, "json": {"choices": [{"message": {"content": "ok"}}]}},
            ]
        )
        text = await pool.chat([{"role": "user", "content": "hi"}])
        assert text == "ok"

    async def test_all_fail_raises(self, quota_path, aiohttp_mock_factory):
        pool = make_pool(
            [
                make_provider("a", priority=10, base_url="http://a"),
                make_provider("b", priority=20, base_url="http://b"),
            ],
            quota_path,
        )
        aiohttp_mock_factory(
            [
                {"status": 429, "json": {}},
                {"status": 429, "json": {}},
            ]
        )
        with pytest.raises(RuntimeError, match="all providers failed"):
            await pool.chat([{"role": "user", "content": "hi"}])

    async def test_empty_content_fails_over(self, quota_path, aiohttp_mock_factory):
        """200 nhưng content rỗng → coi như fail, thử provider kế."""
        pool = make_pool(
            [
                make_provider("a", priority=10, base_url="http://a"),
                make_provider("b", priority=20, base_url="http://b"),
            ],
            quota_path,
        )
        aiohttp_mock_factory(
            [
                {"status": 200, "json": {"choices": [{"message": {"content": ""}}]}},
                {
                    "status": 200,
                    "json": {"choices": [{"message": {"content": "real answer"}}]},
                },
            ]
        )
        text = await pool.chat([{"role": "user", "content": "hi"}])
        assert text == "real answer"
        assert pool.last_provider == "b"

    async def test_consecutive_failures_long_cooldown(
        self, quota_path, aiohttp_mock_factory
    ):
        """3 lỗi liên tiếp → cooldown dài FAIL_COOLDOWN_SECONDS."""
        pool = make_pool(
            [make_provider("a", priority=10, base_url="http://a", daily_limit=10)],
            quota_path,
        )
        # Mỗi lần fail đều ghi quota — phải đủ quota để chạy 3 lần.
        # Reset cooldown giữa các lần để fail liên tiếp (mô phỏng retry sau cooldown ngắn).
        for _ in range(3):
            aiohttp_mock_factory([{"status": 500, "json": {}}])
            with pytest.raises(RuntimeError):
                await pool.chat([{"role": "user", "content": "hi"}])
            if _ < 2:  # giữ cooldown sau lần fail thứ 3 để assert
                pool.providers[0].cooldown_until = 0.0
        assert pool.providers[0].consecutive_failures == 3
        assert pool.providers[0].in_cooldown()
        # cooldown >= FAIL_COOLDOWN_SECONDS
        remaining = pool.providers[0].cooldown_until - time.time()
        assert remaining > 250  # FAIL_COOLDOWN_SECONDS=300


# ── Auth header ───────────────────────────────────────────────


class TestAuth:
    async def test_bearer_header_sent(self, quota_path, aiohttp_mock_factory):
        import os

        pool = make_pool(
            [make_provider("keyed", api_key_env="MY_TEST_KEY", base_url="http://k")],
            quota_path,
        )
        os.environ["MY_TEST_KEY"] = "sk-test-123"
        try:
            session = aiohttp_mock_factory(
                [{"status": 200, "json": {"choices": [{"message": {"content": "ok"}}]}}]
            )
            await pool.chat([{"role": "user", "content": "hi"}])
            assert (
                session.posts[0]["headers"].get("Authorization") == "Bearer sk-test-123"
            )
        finally:
            del os.environ["MY_TEST_KEY"]

    async def test_no_key_provider_sends_no_auth(
        self, quota_path, aiohttp_mock_factory
    ):
        pool = make_pool(
            [make_provider("anon", api_key_env=None, base_url="http://a")],
            quota_path,
        )
        session = aiohttp_mock_factory(
            [{"status": 200, "json": {"choices": [{"message": {"content": "ok"}}]}}]
        )
        await pool.chat([{"role": "user", "content": "hi"}])
        assert "Authorization" not in session.posts[0]["headers"]


# ── Default providers ─────────────────────────────────────────


class TestDefaults:
    def test_build_defaults(self):
        providers = build_default_providers()
        names = [p.name for p in providers]
        assert "opencode" in names
        assert "groq" in names
        # ollama mặc định tắt
        assert next(p for p in providers if p.name == "ollama").enabled is False

    def test_create_pool_roundtrip(self, tmp_path):
        pool = create_llm_pool(quota_path=str(tmp_path / "q.json"))
        assert len(pool.providers) >= 5
