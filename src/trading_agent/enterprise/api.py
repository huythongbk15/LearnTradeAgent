#!/usr/bin/env python3
"""
Enterprise Layer — REST API, multi-tenant, auth, rate limiting.

Components:
1. TradingAPI — FastAPI-based REST API for the trading system
2. MultiTenantManager — tenant isolation, quotas, billing
3. AuthManager — API key / JWT authentication
4. RateLimiter — per-tenant rate limiting
5. AuditLog — operation logging for compliance

Design (CLI):
    trading-agent api start --port 8080
    trading-agent tenant create --name "fund_alpha" --plan pro
    trading-agent api keys create --tenant fund_alpha
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


# ── Auth Manager ─────────────────────────────────────────────


@dataclass
class APIKey:
    key_id: str
    tenant_id: str
    key_hash: str
    permissions: list[str] = field(default_factory=lambda: ["read", "trade"])
    created_at: float = 0.0
    expires_at: float = 0.0
    is_active: bool = True
    last_used: float = 0.0


class AuthManager:
    """API key authentication with hashing and permission checks."""

    def __init__(self):
        self._keys: dict[str, APIKey] = {}  # key_hash → APIKey

    def create_key(
        self, tenant_id: str, permissions: list[str] | None = None
    ) -> tuple[str, APIKey]:
        """Create new API key, returns (plaintext_key, APIKey)."""
        raw_key = f"tak_{secrets.token_hex(24)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = APIKey(
            key_id=secrets.token_hex(8),
            tenant_id=tenant_id,
            key_hash=key_hash,
            permissions=permissions or ["read", "trade"],
            created_at=time.time(),
            expires_at=time.time() + 365 * 86400,
        )
        self._keys[key_hash] = api_key
        return raw_key, api_key

    def validate(self, raw_key: str) -> APIKey | None:
        """Validate API key and return the key record."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key = self._keys.get(key_hash)
        if key and key.is_active and key.expires_at > time.time():
            key.last_used = time.time()
            return key
        return None

    def revoke(self, key_hash: str) -> bool:
        key = self._keys.get(key_hash)
        if key:
            key.is_active = False
            return True
        return False

    def list_keys(self, tenant_id: str) -> list[APIKey]:
        return [k for k in self._keys.values() if k.tenant_id == tenant_id]


# ── Rate Limiter ─────────────────────────────────────────────


class RateLimiter:
    """Token bucket rate limiter per tenant."""

    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self._buckets: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=1000))

    def check(self, tenant_id: str) -> tuple[bool, dict]:
        """Check if request is allowed. Returns (allowed, info)."""
        now = time.time()
        bucket = self._buckets[tenant_id]
        # Remove old entries
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        current = len(bucket)
        allowed = current < self.rpm
        if allowed:
            bucket.append(now)
        return allowed, {
            "tenant_id": tenant_id,
            "current_rpm": current,
            "limit_rpm": self.rpm,
            "remaining": max(0, self.rpm - current - 1),
            "reset_in_s": 60 - (now - bucket[0]) if bucket else 60,
        }


# ── Multi-Tenant Manager ────────────────────────────────────


@dataclass
class Tenant:
    tenant_id: str
    name: str
    plan: str = "free"  # free, pro, enterprise
    max_symbols: int = 5
    max_strategies: int = 3
    max_orders_per_day: int = 100
    max_api_calls_per_minute: int = 30
    features: list[str] = field(default_factory=lambda: ["backtest", "paper_trading"])
    created_at: float = 0.0
    is_active: bool = True
    metadata: dict = field(default_factory=dict)


class TenantManager:
    """Multi-tenant management with plan-based quotas."""

    PLANS = {
        "free": {
            "max_symbols": 5,
            "max_strategies": 3,
            "max_orders_per_day": 100,
            "rpm": 30,
            "features": ["backtest", "paper_trading"],
        },
        "pro": {
            "max_symbols": 50,
            "max_strategies": 20,
            "max_orders_per_day": 10000,
            "rpm": 300,
            "features": ["backtest", "paper_trading", "live_trading", "alerts"],
        },
        "enterprise": {
            "max_symbols": -1,
            "max_strategies": -1,
            "max_orders_per_day": -1,
            "rpm": 3000,
            "features": [
                "backtest",
                "paper_trading",
                "live_trading",
                "alerts",
                "api",
                "white_label",
            ],
        },
    }

    def __init__(self):
        self._tenants: dict[str, Tenant] = {}

    def create_tenant(self, name: str, plan: str = "free") -> Tenant:
        tenant_id = f"t_{secrets.token_hex(6)}"
        plan_config = self.PLANS.get(plan, self.PLANS["free"])
        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            plan=plan,
            max_symbols=plan_config["max_symbols"],
            max_strategies=plan_config["max_strategies"],
            max_orders_per_day=plan_config["max_orders_per_day"],
            max_api_calls_per_minute=plan_config["rpm"],
            features=plan_config["features"],
            created_at=time.time(),
        )
        self._tenants[tenant_id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def upgrade_plan(self, tenant_id: str, new_plan: str) -> Tenant | None:
        tenant = self._tenants.get(tenant_id)
        if tenant and new_plan in self.PLANS:
            plan_config = self.PLANS[new_plan]
            tenant.plan = new_plan
            tenant.max_symbols = plan_config["max_symbols"]
            tenant.max_strategies = plan_config["max_strategies"]
            tenant.max_orders_per_day = plan_config["max_orders_per_day"]
            tenant.max_api_calls_per_minute = plan_config["rpm"]
            tenant.features = plan_config["features"]
            return tenant
        return None

    def check_quota(self, tenant_id: str, resource: str) -> bool:
        """Check if tenant has quota for a resource."""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return False
        if resource == "symbols":
            return tenant.max_symbols == -1 or tenant.max_symbols > 0
        elif resource == "strategies":
            return tenant.max_strategies == -1 or tenant.max_strategies > 0
        return True

    def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())


# ── Audit Log ────────────────────────────────────────────────


@dataclass
class AuditEntry:
    timestamp: float
    tenant_id: str
    action: str
    resource: str
    details: dict = field(default_factory=dict)
    ip_address: str = ""


class AuditLog:
    """Append-only audit log for compliance."""

    def __init__(self, max_entries: int = 100_000):
        self._entries: deque[AuditEntry] = deque(maxlen=max_entries)

    def log(
        self,
        tenant_id: str,
        action: str,
        resource: str,
        details: dict | None = None,
        ip: str = "",
    ):
        entry = AuditEntry(
            timestamp=time.time(),
            tenant_id=tenant_id,
            action=action,
            resource=resource,
            details=details or {},
            ip_address=ip,
        )
        self._entries.append(entry)

    def query(
        self, tenant_id: str = "", action: str = "", limit: int = 100
    ) -> list[AuditEntry]:
        results = list(self._entries)
        if tenant_id:
            results = [e for e in results if e.tenant_id == tenant_id]
        if action:
            results = [e for e in results if e.action == action]
        return results[-limit:]

    def export_json(self, filepath: str):
        with open(filepath, "w") as f:
            json.dump(
                [
                    {
                        "timestamp": e.timestamp,
                        "tenant_id": e.tenant_id,
                        "action": e.action,
                        "resource": e.resource,
                        "details": e.details,
                        "ip": e.ip_address,
                    }
                    for e in self._entries
                ],
                f,
                indent=2,
                default=str,
            )


# ── REST API (lightweight, no FastAPI dependency) ────────────


class TradingAPI:
    """
    Lightweight REST-like API handler.

    In production, wrap with FastAPI/Flask.
    Handles: /strategies, /portfolio, /orders, /backtest, /health.
    """

    def __init__(
        self,
        auth: AuthManager | None = None,
        tenant_mgr: TenantManager | None = None,
        rate_limiter: RateLimiter | None = None,
        audit: AuditLog | None = None,
    ):
        self.auth = auth or AuthManager()
        self.tenants = tenant_mgr or TenantManager()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.audit = audit or AuditLog()
        self._handlers: dict[str, callable] = {}
        self._register_defaults()

    def _register_defaults(self):
        self._handlers["GET /health"] = self._health
        self._handlers["GET /api/v1/strategies"] = self._list_strategies
        self._handlers["POST /api/v1/orders"] = self._place_order
        self._handlers["GET /api/v1/portfolio"] = self._get_portfolio
        self._handlers["POST /api/v1/backtest"] = self._run_backtest

    def handle(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        api_key: str = "",
        ip: str = "",
    ) -> dict:
        """Route a request through auth → rate limit → handler."""
        route = f"{method} {path}"
        handler = self._handlers.get(route)

        if not handler:
            return {"status": 404, "error": "Not found"}

        # Auth (skip health check)
        if path != "/health":
            key = self.auth.validate(api_key)
            if not key:
                return {"status": 401, "error": "Invalid API key"}
            tenant = self.tenants.get_tenant(key.tenant_id)
            if not tenant:
                return {"status": 403, "error": "Tenant not found"}
            allowed, info = self.rate_limiter.check(key.tenant_id)
            if not allowed:
                return {"status": 429, "error": "Rate limit exceeded", **info}
            self.audit.log(key.tenant_id, method, path, ip=ip)

        return handler(body=body or {})

    def _health(self, body: dict) -> dict:
        return {"status": 200, "healthy": True, "version": "1.0.0"}

    def _list_strategies(self, body: dict) -> dict:
        return {
            "status": 200,
            "strategies": ["ma_crossover", "rsi", "bbands", "agent_ensemble"],
        }

    def _place_order(self, body: dict) -> dict:
        symbol = body.get("symbol", "")
        side = body.get("side", "")
        qty = body.get("qty", 0)
        if not symbol or not side or qty <= 0:
            return {
                "status": 400,
                "error": "Missing required fields: symbol, side, qty > 0",
            }
        order_id = f"ord_{secrets.token_hex(8)}"
        return {
            "status": 201,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "order_status": "pending",
        }

    def _get_portfolio(self, body: dict) -> dict:
        return {
            "status": 200,
            "portfolio": {
                "total_value_usd": 100_000,
                "positions": [
                    {
                        "symbol": "BTC/USDT",
                        "qty": 0.5,
                        "value_usd": 50_000,
                        "pnl_pct": 5.2,
                    },
                    {
                        "symbol": "ETH/USDT",
                        "qty": 5.0,
                        "value_usd": 17_500,
                        "pnl_pct": 3.1,
                    },
                ],
                "cash_usd": 32_500,
            },
        }

    def _run_backtest(self, body: dict) -> dict:
        strategy = body.get("strategy", "ma_crossover")
        symbol = body.get("symbol", "BTC/USDT")
        return {
            "status": 200,
            "backtest": {
                "strategy": strategy,
                "symbol": symbol,
                "total_return_pct": 12.5,
                "sharpe": 1.2,
                "max_drawdown_pct": 8.3,
                "trades": 45,
            },
        }


if __name__ == "__main__":
    print("=" * 60)
    print("ENTERPRISE LAYER — DEMO")
    print("=" * 60)

    # Tenant
    tm = TenantManager()
    t1 = tm.create_tenant("Quant Fund Alpha", "pro")
    t2 = tm.create_tenant("Retail Trader", "free")
    print(f"\nTenants: {len(tm.list_tenants())}")
    print(
        f"  {t1.name} ({t1.plan}): symbols={t1.max_symbols}, rpm={t1.max_api_calls_per_minute}"
    )
    print(
        f"  {t2.name} ({t2.plan}): symbols={t2.max_symbols}, rpm={t2.max_api_calls_per_minute}"
    )

    # Auth
    auth = AuthManager()
    key1, _ = auth.create_key(t1.tenant_id)
    key2, _ = auth.create_key(t2.tenant_id)
    print(f"\nAPI Keys created: {len(auth._keys)}")

    # Rate limiter
    rl = RateLimiter(requests_per_minute=5)
    for i in range(7):
        allowed, info = rl.check(t2.tenant_id)
        print(
            f"  Request {i + 1}: {'✓' if allowed else '✗'} (remaining: {info['remaining']})"
        )

    # API
    api = TradingAPI(auth=auth, tenant_mgr=tm, rate_limiter=rl)
    responses = [
        api.handle("GET", "/health"),
        api.handle("GET", "/api/v1/strategies", api_key=key1),
        api.handle(
            "POST",
            "/api/v1/orders",
            {"symbol": "BTC/USDT", "side": "buy", "qty": 0.1},
            api_key=key1,
        ),
        api.handle("GET", "/api/v1/portfolio", api_key=key1),
        api.handle(
            "POST",
            "/api/v1/backtest",
            {"strategy": "rsi", "symbol": "ETH/USDT"},
            api_key=key1,
        ),
        api.handle("GET", "/api/v1/strategies", api_key="invalid_key"),
    ]
    print("\nAPI Responses:")
    for r in responses:
        print(
            f"  status={r['status']} {'✓' if r['status'] == 200 else r.get('error', '')}"
        )
