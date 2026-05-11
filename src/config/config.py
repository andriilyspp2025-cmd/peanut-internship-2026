import os
from dataclasses import dataclass
from decimal import Decimal

from dotenv import load_dotenv

# Завантажуємо змінні з .env файлу при ініціалізації модуля
load_dotenv()


@dataclass
class Settings:
    """
    Централізований клас для управління конфігурацією всього проекту.
    """

    # --- Blockchain / DEX ---
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "testnet").lower()
    ARBITRUM_RPC_URL: str = os.getenv(
        "ARBITRUM_RPC_URL", os.getenv("RPC_URL", "http://127.0.0.1:8545")
    )
    SEPOLIA_RPC_URL: str = os.getenv("SEPOLIA_RPC_URL", "")
    RPC_URL: str = os.getenv("RPC_URL", "http://127.0.0.1:8545")
    FORK_URL: str = os.getenv("ETH_RPC_URL", "")

    # --- Chain Constants ---
    ARBITRUM_CHAIN_ID: int = 42161
    SEPOLIA_CHAIN_ID: int = 11155111
    UNISWAP_V2_ROUTER: str = "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24"
    WETH_ADDRESS: str = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    USDC_ADDRESS: str = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    # --- Binance Production (CEX) ---
    BINANCE_TESTNET_API_KEY: str = os.getenv("BINANCE_TESTNET_API_KEY", "")
    BINANCE_TESTNET_SECRET: str = os.getenv("BINANCE_TESTNET_SECRET", "")
    BINANCE_PROD_API_KEY: str = os.getenv("BINANCE_PROD_API_KEY", "")
    BINANCE_PROD_SECRET: str = os.getenv("BINANCE_PROD_SECRET", "")
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET: str = os.getenv("BINANCE_SECRET", "")

    # --- System Params ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    WSS_URL: str = os.getenv("WSS_URL", "")

    # --- RPC Endpoint Rotation (QuickNode multiple endpoints) ---
    HTTP_RPC_ENDPOINTS: list = None  # Will be populated in __post_init__
    WSS_RPC_ENDPOINTS: list = None  # Will be populated in __post_init__
    RPC_CACHE_TTL_MS: int = int(
        os.getenv("RPC_CACHE_TTL_MS", "300")
    )  # 300ms for DEX quotes
    GAS_CACHE_TTL_SECONDS: int = int(
        os.getenv("GAS_CACHE_TTL_SECONDS", "15")
    )  # 15s for gas

    # --- Strategy Params ---
    MIN_SPREAD_BPS: Decimal = Decimal(os.getenv("MIN_SPREAD_BPS", "15"))
    MIN_PROFIT_USD: Decimal = Decimal(os.getenv("MIN_PROFIT_USD", "0.20"))
    MAX_TRADE_USD: Decimal = Decimal(os.getenv("MAX_TRADE_USD", "10.0"))
    MAX_POSITION_USD: Decimal = Decimal(os.getenv("MAX_POSITION_USD", "50"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "2"))
    MIN_OPEN_POSITION_USD: Decimal = Decimal(os.getenv("MIN_OPEN_POSITION_USD", "1"))
    GAS_BUFFER_BPS: Decimal = Decimal(os.getenv("GAS_BUFFER_BPS", "20"))
    GAS_UNITS_V3_SWAP: int = int(os.getenv("GAS_UNITS_V3_SWAP", "220000"))
    MAX_DAILY_LOSS_USD: Decimal = Decimal(os.getenv("MAX_DAILY_LOSS_USD", "15.0"))
    MAX_DRAWDOWN_PCT: Decimal = Decimal(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))
    MAX_TRADES_PER_HOUR: int = int(os.getenv("MAX_TRADES_PER_HOUR", "20"))
    MAX_CONSECUTIVE_LOSSES: int = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
    MAX_SIGNAL_AGE_SECONDS: int = int(os.getenv("MAX_SIGNAL_AGE_SECONDS", "5"))
    MAX_SPREAD_BPS: Decimal = Decimal(os.getenv("MAX_SPREAD_BPS", "1000"))
    MIN_SCORE_THRESHOLD: float = float(os.getenv("MIN_SCORE_THRESHOLD", "60.0"))
    SIGNAL_TTL_SECONDS: float = float(os.getenv("SIGNAL_TTL_SECONDS", "5.0"))
    COOLDOWN_SECONDS: float = float(os.getenv("COOLDOWN_SECONDS", "2.0"))
    ETH_PRICE_CACHE_SECONDS: float = float(os.getenv("ETH_PRICE_CACHE_SECONDS", "30.0"))
    ENABLE_V2_POOLS: bool = os.getenv("ENABLE_V2_POOLS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    DEX_SLIPPAGE_PCT: Decimal = Decimal(os.getenv("DEX_SLIPPAGE_PCT", "0.97"))
    DEX_EMERGENCY_SLIPPAGE_PCT: Decimal = Decimal(
        os.getenv("DEX_EMERGENCY_SLIPPAGE_PCT", "0.05")
    )
    PAIRS: str = os.getenv("PAIRS", "ETH/USDT,ARB/USDT,GMX/USDT")
    ETH_USDC_POOL_V3: str = os.getenv("ETH_USDC_POOL_V3", "")
    ARB_USDC_POOL_V3: str = os.getenv("ARB_USDC_POOL_V3", "")
    GMX_USDC_POOL_V3: str = os.getenv("GMX_USDC_POOL_V3", "")

    def __post_init__(self) -> None:
        env = self.ENVIRONMENT.lower()
        if env in {"prod", "production", "mainnet"}:
            self.ENVIRONMENT = "prod"
            self.RPC_URL = self.ARBITRUM_RPC_URL
        else:
            self.ENVIRONMENT = "testnet"
            self.RPC_URL = self.SEPOLIA_RPC_URL or self.ARBITRUM_RPC_URL

        # Parse HTTP and WSS endpoints for rotation (synchronized)
        self.HTTP_RPC_ENDPOINTS, self.WSS_RPC_ENDPOINTS = self._parse_rpc_endpoints()

    def _parse_rpc_endpoints(self) -> tuple[list[str], list[str]]:
        """
        Parse HTTP and WSS RPC endpoints from .env file.

        Supports:
        - HTTP: QN_HTTP_1, QN_HTTP_2, ... or RPC_URL fallback
        - WSS: QN_WSS_1, QN_WSS_2, ... or WSS_URL fallback

        Returns:
            Tuple of (http_endpoints, wss_endpoints)
        """
        http_endpoints = []
        wss_endpoints = []

        # Parse HTTP endpoints: QN_HTTP_1, QN_HTTP_2, ...
        for i in range(1, 10):
            url = os.getenv(f"QN_HTTP_{i}", "").strip()
            if url:
                http_endpoints.append(url)

        # Fallback: use RPC_URL
        if not http_endpoints:
            http_endpoints = [self.RPC_URL]

        # Parse WSS endpoints: QN_WSS_1, QN_WSS_2, ...
        for i in range(1, 10):
            url = os.getenv(f"QN_WSS_{i}", "").strip()
            if url:
                wss_endpoints.append(url)

        # Fallback: use WSS_URL
        if not wss_endpoints and self.WSS_URL:
            wss_endpoints = [self.WSS_URL]

        return http_endpoints, wss_endpoints

    @property
    def is_prod(self) -> bool:
        return self.ENVIRONMENT == "prod"

    @property
    def binance_config(self) -> dict:
        """
        Повертає готовий словник конфігурації для ініціалізації ccxt.binance
        """
        if self.is_prod:
            api_key = self.BINANCE_PROD_API_KEY or self.BINANCE_API_KEY
            secret = self.BINANCE_PROD_SECRET or self.BINANCE_SECRET
            sandbox = False
        else:
            api_key = self.BINANCE_TESTNET_API_KEY or self.BINANCE_API_KEY
            secret = self.BINANCE_TESTNET_SECRET or self.BINANCE_SECRET
            sandbox = True

        if not api_key or not secret:
            raise ValueError(
                "Binance API keys are missing for the selected environment."
            )

        return {
            "apiKey": api_key,
            "secret": secret,
            "sandbox": sandbox,
            "options": {
                "defaultType": "spot",
            },
            "enableRateLimit": True,
        }


# Створюємо єдиний (Singleton) екземпляр для імпорту по всьому проекту
config = Settings()
