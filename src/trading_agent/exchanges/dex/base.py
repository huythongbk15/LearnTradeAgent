"""Base DEX adapter with common functionality."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from trading_agent.exchanges.models import Symbol, Order, PoolInfo, SwapQuote


class BaseDEXAdapter(ABC):
    """Base class for DEX adapters."""

    def __init__(
        self,
        name: str,
        chain_id: int,
        rpc_url: str,
        private_key: Optional[str] = None,
    ):
        self.name = name
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        self.private_key = private_key
        self._connected = False

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the blockchain."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the blockchain."""
        pass

    @abstractmethod
    async def get_pool_info(
        self, token0: Symbol, token1: Symbol, fee_tier: int
    ) -> PoolInfo:
        """Get liquidity pool information."""
        pass

    @abstractmethod
    async def get_swap_quote(
        self,
        token_in: Symbol,
        token_out: Symbol,
        amount_in: Decimal,
        slippage_pct: Decimal = Decimal("0.5"),
    ) -> SwapQuote:
        """Get a swap quote."""
        pass

    @abstractmethod
    async def execute_swap(
        self,
        quote: SwapQuote,
        deadline_seconds: int = 300,
    ) -> Order:
        """Execute a swap transaction."""
        pass

    @abstractmethod
    async def get_token_balance(
        self, token: Symbol, address: Optional[str] = None
    ) -> Decimal:
        """Get token balance for an address."""
        pass

    @abstractmethod
    async def approve_token(self, token: Symbol, spender: str, amount: Decimal) -> str:
        """Approve token spending."""
        pass

    @property
    def is_connected(self) -> bool:
        return self._connected


class DEXRouter:
    """Router for finding best swap routes across multiple DEXes."""

    def __init__(self, adapters: list[BaseDEXAdapter]):
        self.adapters = {a.name: a for a in adapters}

    async def find_best_route(
        self,
        token_in: Symbol,
        token_out: Symbol,
        amount_in: Decimal,
        slippage_pct: Decimal = Decimal("0.5"),
    ) -> tuple[str, SwapQuote]:
        """Find the best swap route across all DEXes."""
        best_quote = None
        best_adapter = None

        for name, adapter in self.adapters.items():
            if not adapter.is_connected:
                continue
            try:
                quote = await adapter.get_swap_quote(
                    token_in, token_out, amount_in, slippage_pct
                )
                if best_quote is None or quote.amount_out > best_quote.amount_out:
                    best_quote = quote
                    best_adapter = name
            except Exception:
                continue

        if best_quote is None:
            raise ValueError(f"No route found for {token_in} -> {token_out}")

        return best_adapter, best_quote

    async def execute_best_swap(
        self,
        token_in: Symbol,
        token_out: Symbol,
        amount_in: Decimal,
        slippage_pct: Decimal = Decimal("0.5"),
    ) -> Order:
        """Execute swap on best DEX."""
        adapter_name, quote = await self.find_best_route(
            token_in, token_out, amount_in, slippage_pct
        )
        adapter = self.adapters[adapter_name]
        return await adapter.execute_swap(quote)
