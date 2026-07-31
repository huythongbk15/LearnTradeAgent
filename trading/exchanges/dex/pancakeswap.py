"""PancakeSwap adapter for BSC and EVM-compatible chains."""

import logging
from decimal import Decimal
from typing import Optional

from web3 import Web3
from web3.contract import Contract

from trading.exchanges.dex.base import BaseDEXAdapter, PoolInfo, SwapQuote
from trading.exchanges.models import Symbol, Order, OrderSide, OrderType, OrderStatus
from trading.exchanges.dex.uniswap_v3 import ERC20_ABI

logger = logging.getLogger(__name__)


# PancakeSwap V3 ABIs (similar to Uniswap V3)
PANCAKESWAP_V3_FACTORY_ABI = """[
    {"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}
]"""

PANCAKESWAP_V3_POOL_ABI = """[
    {"inputs":[],"name":"slot0","outputs":[{"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},{"internalType":"int24","name":"tick","type":"int24"},{"internalType":"uint16","name":"observationIndex","type":"uint16"},{"internalType":"uint16","name":"observationCardinality","type":"uint16"},{"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},{"internalType":"uint8","name":"feeProtocol","type":"uint8"},{"internalType":"bool","name":"unlocked","type":"bool"}],"name":"slot0","outputs":[{"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},{"internalType":"int24","name":"tick","type":"int24"},{"internalType":"uint16","name":"observationIndex","type":"uint16"},{"internalType":"uint16","name":"observationCardinality","type":"uint16"},{"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},{"internalType":"uint8","name":"feeProtocol","type":"uint8"},{"internalType":"bool","name":"unlocked","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"liquidity","outputs":[{"internalType":"uint128","name":"","type":"uint128"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"token1","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"fee","outputs":[{"internalType":"uint24","name":"","type":"uint24"}],"stateMutability":"view","type":"function"}
]"""

PANCAKESWAP_ROUTER_ABI = """[
    {"inputs":[{"internalType":"bytes","name":"path","type":"bytes"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMinimum","type":"uint256"}],"name":"exactInput","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}
]"""

QUOTER_V2_ABI = """[
    {"inputs":[{"internalType":"bytes","name":"path","type":"bytes"},{"internalType":"uint256","name":"amountIn","type":"uint256"}],"name":"quoteExactInput","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},{"internalType":"int24","name":"tickAfter","type":"int24"},{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}
]"""


# PancakeSwap V3 addresses
PANCAKESWAP_ADDRESSES = {
    56: {  # BSC Mainnet
        "factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
        "router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
        "quoter": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
    },
    97: {  # BSC Testnet
        "factory": "0x6723715Ee75F5F8eF4E1F7c78876A8a5D8F5e2E6",
        "router": "0x1b81D678c19225032a203E7a7eD4B3c91441c843",
    },
}

# Common BSC tokens
BSC_TOKENS = {
    56: {
        "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
        "BTCB": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
        "ETH": "0x2170Ed0880ac9a755fd29B2688956bd959F933F8",
        "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
    },
    97: {
        "WBNB": "0xae13d989dac2f0debff460ac112a837c89baa7cd",
        "USDT": "0x78867BbEeF44f2326bF8DDdBf198Ee7DBB9c48d0",
        "USDC": "0x64544969ed7EBf5f083679233325356EbE738930",
    },
}


class PancakeSwapAdapter(BaseDEXAdapter):
    """PancakeSwap V3 DEX adapter for BSC."""

    def __init__(
        self,
        chain_id: int = 56,
        rpc_url: str = "https://bsc-dataseed.binance.org/",
        private_key: Optional[str] = None,
    ):
        super().__init__("pancakeswap_v3", chain_id, rpc_url, private_key)
        
        if chain_id not in PANCAKESWAP_ADDRESSES:
            raise ValueError(f"Unsupported chain ID: {chain_id}")
        
        self.addresses = PANCAKESWAP_ADDRESSES[chain_id]
        self.tokens = BSC_TOKENS.get(chain_id, {})
        
        self._w3: Optional[Web3] = None
        self._factory: Optional[Contract] = None
        self._router: Optional[Contract] = None
        self._quoter: Optional[Contract] = None
        self._account = None

    async def connect(self) -> bool:
        """Connect to BSC."""
        try:
            self._w3 = Web3(Web3.AsyncHTTPProvider(self.rpc_url))
            
            is_connected = await self._w3.is_connected()
            if not is_connected:
                logger.error("Failed to connect to BSC RPC")
                return False
            
            import json
            self._factory = self._w3.eth.contract(
                address=Web3.to_checksum_address(self.addresses["factory"]),
                abi=json.loads(PANCAKESWAP_V3_FACTORY_ABI)
            )
            self._router = self._w3.eth.contract(
                address=Web3.to_checksum_address(self.addresses["router"]),
                abi=json.loads(PANCAKESWAP_ROUTER_ABI)
            )
            self._quoter = self._w3.eth.contract(
                address=Web3.to_checksum_address(self.addresses["quoter"]),
                abi=json.loads(QUOTER_V2_ABI)
            )
            
            if self.private_key:
                self._account = self._w3.eth.account.from_key(self.private_key)
            
            self._connected = True
            logger.info(f"Connected to PancakeSwap V3 on chain {self.chain_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from BSC."""
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
        if symbol.quote.upper() in self.tokens:
            return self.tokens[symbol.quote.upper()]
        raise ValueError(f"Unknown token: {symbol.base} on BSC")

    async def get_pool_info(self, token0: Symbol, token1: Symbol, fee_tier: int) -> PoolInfo:
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

        import json
        pool = self._w3.eth.contract(
            address=pool_addr,
            abi=json.loads(PANCAKESWAP_V3_POOL_ABI)
        )

        slot0 = await pool.functions.slot0().call()
        liquidity = await pool.functions.liquidity().call()
        fee = await pool.functions.fee().call()

        return PoolInfo(
            pool_address=pool_addr,
            token0=token0,
            token1=token1,
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
        """Get a swap quote."""
        if not self._connected:
            raise RuntimeError("Not connected")

        token_in_addr = Web3.to_checksum_address(self._get_token_address(token_in))
        token_out_addr = Web3.to_checksum_address(self._get_token_address(token_out))

        best_quote = None
        
        for fee_tier in [100, 500, 2500, 10000]:  # PancakeSwap fee tiers
            pool_addr = await self._factory.functions.getPool(
                token_in_addr, token_out_addr, fee_tier
            ).call()
            
            if pool_addr == "0x0000000000000000000000000000000000000000":
                continue

            path = token_in_addr[2:] + fee_tier.to_bytes(3, "big").hex() + token_out_addr[2:]
            path_bytes = bytes.fromhex(path)

            try:
                amount_in_wei = int(amount_in * Decimal(10**18))
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
                        amount_out_min=amount_out * (Decimal(1) - slippage_pct / Decimal(100)),
                        price_impact_pct=Decimal(0),
                        gas_estimate=gas_estimate,
                        route=[pool_addr],
                        timestamp=datetime.utcnow(),
                    )
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

        token_in_addr = Web3.to_checksum_address(self._get_token_address(quote.token_in))
        token_out_addr = Web3.to_checksum_address(self._get_token_address(quote.token_out))
        
        fee_tier = 2500  # Default PancakeSwap
        
        path = token_in_addr[2:] + fee_tier.to_bytes(3, "big").hex() + token_out_addr[2:]
        path_bytes = bytes.fromhex(path)

        amount_in_wei = int(quote.amount_in * Decimal(10**18))
        amount_out_min_wei = int(quote.amount_out_min * Decimal(10**18))

        await self.approve_token(quote.token_in, self.addresses["router"], quote.amount_in)

        tx = await self._router.functions.exactInput(
            path_bytes,
            self._account.address,
            amount_in_wei,
            amount_out_min_wei
        ).build_transaction({
            "from": self._account.address,
            "nonce": await self._w3.eth.get_transaction_count(self._account.address),
            "gas": quote.gas_estimate + 50000,
            "gasPrice": await self._w3.eth.gas_price,
            "value": 0,
        })

        signed = self._w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.rawTransaction)
        
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

    async def get_token_balance(self, token: Symbol, address: Optional[str] = None) -> Decimal:
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

        current = await token_contract.functions.allowance(
            self._account.address, spender_addr
        ).call()
        
        if current >= amount_wei:
            return "Already approved"

        tx = await token_contract.functions.approve(
            spender_addr, amount_wei
        ).build_transaction({
            "from": self._account.address,
            "nonce": await self._w3.eth.get_transaction_count(self._account.address),
            "gas": 100000,
            "gasPrice": await self._w3.eth.gas_price,
        })

        signed = self._w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.rawTransaction)
        await self._w3.eth.wait_for_transaction_receipt(tx_hash)
        
        return tx_hash.hex()


from datetime import datetime