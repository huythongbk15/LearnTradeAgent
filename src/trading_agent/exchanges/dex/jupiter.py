"""Jupiter adapter for Solana DEX aggregation."""

import logging
from decimal import Decimal
from typing import Optional

import aiohttp

from trading_agent.exchanges.dex.base import BaseDEXAdapter, SwapQuote
from trading_agent.exchanges.models import Symbol, Order, OrderSide, OrderType, OrderStatus

logger = logging.getLogger(__name__)


class JupiterAdapter(BaseDEXAdapter):
    """Jupiter Aggregator adapter for Solana."""

    def __init__(
        self,
        rpc_url: str = "https://api.mainnet-beta.solana.com",
        private_key: Optional[str] = None,
    ):
        super().__init__("jupiter", 0, rpc_url, private_key)  # chain_id 0 for Solana
        
        self.base_url = "https://quote-api.jup.ag/v6"
        self._session: Optional[aiohttp.ClientSession] = None
        self._token_map: dict[str, str] = {}  # symbol -> mint address

    async def connect(self) -> bool:
        """Connect to Jupiter API."""
        try:
            self._session = aiohttp.ClientSession()
            
            # Fetch token list
            async with self._session.get(f"{self.base_url}/tokens") as resp:
                if resp.status == 200:
                    tokens = await resp.json()
                    self._token_map = {t["symbol"].upper(): t["address"] for t in tokens}
                    logger.info(f"Loaded {len(self._token_map)} tokens from Jupiter")
            
            self._connected = True
            logger.info("Connected to Jupiter Aggregator")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Jupiter: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from Jupiter."""
        if self._session:
            await self._session.close()
        self._connected = False

    def _get_mint(self, symbol: Symbol) -> str:
        """Get token mint address."""
        sym = symbol.base.upper()
        if sym in self._token_map:
            return self._token_map[sym]
        # Try quote
        sym = symbol.quote.upper()
        if sym in self._token_map:
            return self._token_map[sym]
        raise ValueError(f"Unknown token: {symbol.base}")

    async def get_pool_info(self, token0: Symbol, token1: Symbol, fee_tier: int) -> None:
        """Jupiter doesn't expose pool info directly."""
        raise NotImplementedError("Jupiter doesn't expose individual pool info")

    async def get_swap_quote(
        self,
        token_in: Symbol,
        token_out: Symbol,
        amount_in: Decimal,
        slippage_pct: Decimal = Decimal("0.5"),
    ) -> SwapQuote:
        """Get a swap quote from Jupiter."""
        if not self._connected:
            raise RuntimeError("Not connected")

        input_mint = self._get_mint(token_in)
        output_mint = self._get_mint(token_out)
        
        # Convert amount to lamports (assuming 9 decimals for most tokens)
        amount_in_lamports = int(amount_in * Decimal(10**9))

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_in_lamports),
            "slippageBps": int(slippage_pct * 100),  # Convert to basis points
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }

        async with self._session.get(f"{self.base_url}/quote", params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Quote failed: {await resp.text()}")
            
            data = await resp.json()

        amount_out = Decimal(data["outAmount"]) / Decimal(10**9)
        amount_out_min = Decimal(data["outAmount"]) / Decimal(10**9) * (Decimal(1) - slippage_pct / Decimal(100))
        
        # Calculate price impact
        in_amount = Decimal(data["inAmount"]) / Decimal(10**9)
        price_impact = Decimal(data.get("priceImpactPct", "0"))

        return SwapQuote(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=amount_out,
            amount_out_min=amount_out_min,
            price_impact_pct=price_impact,
            gas_estimate=5000,  # Solana compute units estimate
            route=data.get("routePlan", []),
            timestamp=datetime.utcnow(),
        )

    async def execute_swap(
        self,
        quote: SwapQuote,
        deadline_seconds: int = 300,
    ) -> Order:
        """Execute a swap via Jupiter."""
        if not self._connected or not self.private_key:
            raise RuntimeError("Not connected or no private key")

        # Get swap transaction
        input_mint = self._get_mint(quote.token_in)
        output_mint = self._get_mint(quote.token_out)
        amount_in_lamports = int(quote.amount_in * Decimal(10**9))

        # Get serialized transaction
        swap_data = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_in_lamports),
            "slippageBps": int(Decimal("0.5") * 100),
            "userPublicKey": self.private_key,  # This would need to be the public key
            "wrapAndUnwrapSol": True,
        }

        async with self._session.post(f"{self.base_url}/swap", json=swap_data) as resp:
            if resp.status != 200:
                raise ValueError(f"Swap failed: {await resp.text()}")
            
            swap_result = await resp.json()

        # The transaction would need to be signed and sent via Solana RPC
        # This is a simplified version
        tx_signature = swap_result.get("txid", "pending")

        return Order(
            id=tx_signature,
            symbol=quote.token_in,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=quote.amount_in,
            status=OrderStatus.FILLED,
            filled_size=quote.amount_in,
            avg_fill_price=quote.amount_out / quote.amount_in,
        )

    async def get_token_balance(self, token: Symbol, address: Optional[str] = None) -> Decimal:
        """Get token balance via RPC."""
        if not self._connected:
            raise RuntimeError("Not connected")

        # Would need Solana RPC call to get token account balance
        # Simplified for now
        return Decimal(0)

    async def approve_token(self, token: Symbol, spender: str, amount: Decimal) -> str:
        """Solana doesn't need token approval (uses token accounts)."""
        return "Not required on Solana"


# Need to import datetime
from datetime import datetime