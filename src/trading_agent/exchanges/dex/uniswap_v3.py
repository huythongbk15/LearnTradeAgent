"""Uniswap V3 adapter for Ethereum and EVM-compatible chains."""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from web3 import Web3
from web3.contract import Contract

from trading_agent.exchanges.dex.base import BaseDEXAdapter, PoolInfo, SwapQuote
from trading_agent.exchanges.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Symbol,
)

logger = logging.getLogger(__name__)


# Uniswap V3 ABIs (minimal)
ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

UNISWAP_V3_POOL_ABI = json.loads("""[
    {"inputs":[],"name":"slot0","outputs":[{"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},{"internalType":"int24","name":"tick","type":"int24"},{"internalType":"uint16","name":"observationIndex","type":"uint16"},{"internalType":"uint16","name":"observationCardinality","type":"uint16"},{"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},{"internalType":"uint8","name":"feeProtocol","type":"uint8"},{"internalType":"bool","name":"unlocked","type":"bool"}],"name":"slot0","outputs":[{"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},{"internalType":"int24","name":"tick","type":"int24"},{"internalType":"uint16","name":"observationIndex","type":"uint16"},{"internalType":"uint16","name":"observationCardinality","type":"uint16"},{"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},{"internalType":"uint8","name":"feeProtocol","type":"uint8"},{"internalType":"bool","name":"unlocked","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"liquidity","outputs":[{"internalType":"uint128","name":"","type":"uint128"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"token1","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"fee","outputs":[{"internalType":"uint24","name":"","type":"uint24"}],"stateMutability":"view","type":"function"}
]""")

UNISWAP_V3_FACTORY_ABI = json.loads("""[
    {"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}
]""")

SWAP_ROUTER_ABI = json.loads("""[
    {"inputs":[{"internalType":"bytes","name":"path","type":"bytes"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMinimum","type":"uint256"}],"name":"exactInput","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"},
    {"inputs":[{"internalType":"bytes","name":"path","type":"bytes"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint256","name":"amountInMaximum","type":"uint256"}],"name":"exactOutput","outputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"}],"stateMutability":"payable","type":"function"}
]""")

QUOTER_ABI = json.loads("""[
    {"inputs":[{"internalType":"bytes","name":"path","type":"bytes"},{"internalType":"uint256","name":"amountIn","type":"uint256"}],"name":"quoteExactInput","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},{"internalType":"int24","name":"tickAfter","type":"int24"},{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}
]""")


# Known addresses for mainnet
UNISWAP_V3_ADDRESSES = {
    1: {  # Ethereum Mainnet
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "quoter": "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
        "nft_position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    },
    137: {  # Polygon
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "quoter": "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    },
    42161: {  # Arbitrum
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    },
    10: {  # Optimism
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    },
    8453: {  # Base
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    },
}

# Common token addresses
COMMON_TOKENS = {
    1: {
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC": "0xA0b86a33E6441b8C4C8C8C8C8C8C8C8C8C8C8C8C8",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    },
    137: {
        "WETH": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
        "WBTC": "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
    },
}


class UniswapV3Adapter(BaseDEXAdapter):
    """Uniswap V3 DEX adapter."""

    def __init__(
        self,
        chain_id: int = 1,
        rpc_url: str = "https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY",
        private_key: Optional[str] = None,
    ):
        super().__init__("uniswap_v3", chain_id, rpc_url, private_key)

        if chain_id not in UNISWAP_V3_ADDRESSES:
            raise ValueError(f"Unsupported chain ID: {chain_id}")

        self.addresses = UNISWAP_V3_ADDRESSES[chain_id]
        self.tokens = COMMON_TOKENS.get(chain_id, {})

        self._w3: Optional[Web3] = None
        self._factory: Optional[Contract] = None
        self._router: Optional[Contract] = None
        self._quoter: Optional[Contract] = None
        self._account = None

    async def connect(self) -> bool:
        """Connect to the blockchain."""
        try:
            self._w3 = Web3(Web3.AsyncHTTPProvider(self.rpc_url))

            # Check connection
            is_connected = await self._w3.is_connected()
            if not is_connected:
                logger.error("Failed to connect to RPC")
                return False

            # Initialize contracts
            self._factory = self._w3.eth.contract(
                address=Web3.to_checksum_address(self.addresses["factory"]),
                abi=UNISWAP_V3_FACTORY_ABI,
            )
            self._router = self._w3.eth.contract(
                address=Web3.to_checksum_address(self.addresses["router"]),
                abi=SWAP_ROUTER_ABI,
            )
            self._quoter = self._w3.eth.contract(
                address=Web3.to_checksum_address(self.addresses["quoter"]),
                abi=QUOTER_ABI,
            )

            # Set up account if private key provided
            if self.private_key:
                self._account = self._w3.eth.account.from_key(self.private_key)

            self._connected = True
            logger.info(f"Connected to Uniswap V3 on chain {self.chain_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the blockchain."""
        self._connected = False
        self._w3 = None
        self._factory = None
        self._router = None
        self._quoter = None
        self._account = None

    def _get_token_address(self, symbol: Symbol) -> str:
        """Get token contract address."""
        token_key = symbol.base.upper()
        if token_key in self.tokens:
            return self.tokens[token_key]
        # Try to find by symbol in quote
        if symbol.quote.upper() in self.tokens:
            return self.tokens[symbol.quote.upper()]
        raise ValueError(f"Unknown token: {symbol.base} on chain {self.chain_id}")

    async def get_pool_info(
        self, token0: Symbol, token1: Symbol, fee_tier: int
    ) -> PoolInfo:
        """Get liquidity pool information."""
        if not self._connected:
            raise RuntimeError("Not connected")

        token0_addr = Web3.to_checksum_address(self._get_token_address(token0))
        token1_addr = Web3.to_checksum_address(self._get_token_address(token1))

        pool_addr = await self._factory.functions.getPool(
            token0_addr, token1_addr, fee_tier
        ).call()

        if pool_addr == "0x0000000000000000000000000000000000000000":
            raise ValueError(f"Pool not found for {token0}/{token1} fee {fee_tier}")

        pool = self._w3.eth.contract(address=pool_addr, abi=UNISWAP_V3_POOL_ABI)

        slot0 = await pool.functions.slot0().call()
        liquidity = await pool.functions.liquidity().call()
        fee = await pool.functions.fee().call()
        token0_actual = await pool.functions.token0().call()
        token1_actual = await pool.functions.token1().call()

        return PoolInfo(
            pool_address=pool_addr,
            token0=token0 if token0_actual.lower() == token0_addr.lower() else token1,
            token1=token1 if token1_actual.lower() == token1_addr.lower() else token0,
            fee_tier=fee,
            liquidity=Decimal(liquidity),
            sqrt_price_x96=slot0[0],
            tick=slot0[1],
        )

    async def get_swap_quote(
        self,
        token_in: Symbol,
        token_out: Symbol,
        amount_in: Decimal,
        slippage_pct: Decimal = Decimal("0.5"),
    ) -> SwapQuote:
        """Get a swap quote using the Quoter contract."""
        if not self._connected:
            raise RuntimeError("Not connected")

        token_in_addr = Web3.to_checksum_address(self._get_token_address(token_in))
        token_out_addr = Web3.to_checksum_address(self._get_token_address(token_out))

        # Find best fee tier (try common ones)
        best_quote = None
        best_pool = None

        for fee_tier in [500, 3000, 10000]:  # 0.05%, 0.3%, 1%
            pool_addr = await self._factory.functions.getPool(
                token_in_addr, token_out_addr, fee_tier
            ).call()

            if pool_addr == "0x0000000000000000000000000000000000000000":
                continue

            # Encode path for quoter
            path = (
                token_in_addr[2:]
                + fee_tier.to_bytes(3, "big").hex()
                + token_out_addr[2:]
            )
            path_bytes = bytes.fromhex(path)

            try:
                amount_in_wei = int(amount_in * Decimal(10**18))  # Simplified
                quote_result = await self._quoter.functions.quoteExactInput(
                    path_bytes, amount_in_wei
                ).call()

                amount_out = Decimal(quote_result[0]) / Decimal(10**18)
                gas_estimate = quote_result[3]

                if best_quote is None or amount_out > best_quote.amount_out:
                    best_quote = SwapQuote(
                        token_in=token_in,
                        token_out=token_out,
                        amount_in=amount_in,
                        amount_out=amount_out,
                        amount_out_min=amount_out
                        * (Decimal(1) - slippage_pct / Decimal(100)),
                        price_impact_pct=Decimal(0),  # Would need more calc
                        gas_estimate=gas_estimate,
                        route=[pool_addr],
                        timestamp=datetime.utcnow(),
                    )
                    best_pool = pool_addr
            except Exception as e:
                logger.debug(f"Quote failed for fee {fee_tier}: {e}")
                continue

        if best_quote is None:
            raise ValueError(f"No liquidity for {token_in} -> {token_out}")

        return best_quote

    async def execute_swap(
        self,
        quote: SwapQuote,
        deadline_seconds: int = 300,
    ) -> Order:
        """Execute a swap transaction."""
        if not self._connected or not self._account:
            raise RuntimeError("Not connected or no private key")

        # Build path
        token_in_addr = Web3.to_checksum_address(
            self._get_token_address(quote.token_in)
        )
        token_out_addr = Web3.to_checksum_address(
            self._get_token_address(quote.token_out)
        )

        # Need to determine fee tier from route
        fee_tier = 3000  # Default

        path = (
            token_in_addr[2:] + fee_tier.to_bytes(3, "big").hex() + token_out_addr[2:]
        )
        path_bytes = bytes.fromhex(path)

        amount_in_wei = int(quote.amount_in * Decimal(10**18))
        amount_out_min_wei = int(quote.amount_out_min * Decimal(10**18))

        # Approve token if needed
        await self.approve_token(
            quote.token_in, self.addresses["router"], quote.amount_in
        )

        # Execute swap
        tx = await self._router.functions.exactInput(
            path_bytes, self._account.address, amount_in_wei, amount_out_min_wei
        ).build_transaction(
            {
                "from": self._account.address,
                "nonce": await self._w3.eth.get_transaction_count(
                    self._account.address
                ),
                "gas": quote.gas_estimate + 50000,
                "gasPrice": await self._w3.eth.gas_price,
                "value": 0,
            }
        )

        signed = self._w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.rawTransaction)

        # Wait for receipt
        receipt = await self._w3.eth.wait_for_transaction_receipt(tx_hash)

        return Order(
            id=tx_hash.hex(),
            symbol=quote.token_in,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=quote.amount_in,
            status=OrderStatus.FILLED if receipt.status == 1 else OrderStatus.REJECTED,
            filled_size=quote.amount_in,
            avg_fill_price=quote.amount_out / quote.amount_in,
        )

    async def get_token_balance(
        self, token: Symbol, address: Optional[str] = None
    ) -> Decimal:
        """Get token balance."""
        if not self._connected:
            raise RuntimeError("Not connected")

        token_addr = Web3.to_checksum_address(self._get_token_address(token))
        token_contract = self._w3.eth.contract(address=token_addr, abi=ERC20_ABI)

        addr = address or (self._account.address if self._account else None)
        if not addr:
            raise ValueError("No address provided")

        balance = await token_contract.functions.balanceOf(addr).call()
        decimals = await token_contract.functions.decimals().call()

        return Decimal(balance) / Decimal(10**decimals)

    async def approve_token(self, token: Symbol, spender: str, amount: Decimal) -> str:
        """Approve token spending."""
        if not self._connected or not self._account:
            raise RuntimeError("Not connected or no private key")

        token_addr = Web3.to_checksum_address(self._get_token_address(token))
        token_contract = self._w3.eth.contract(address=token_addr, abi=ERC20_ABI)

        spender_addr = Web3.to_checksum_address(spender)
        amount_wei = int(amount * Decimal(10**18))

        # Check current allowance
        current = await token_contract.functions.allowance(
            self._account.address, spender_addr
        ).call()

        if current >= amount_wei:
            return "Already approved"

        tx = await token_contract.functions.approve(
            spender_addr, amount_wei
        ).build_transaction(
            {
                "from": self._account.address,
                "nonce": await self._w3.eth.get_transaction_count(
                    self._account.address
                ),
                "gas": 100000,
                "gasPrice": await self._w3.eth.gas_price,
            }
        )

        signed = self._w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.rawTransaction)
        await self._w3.eth.wait_for_transaction_receipt(tx_hash)

        return tx_hash.hex()
