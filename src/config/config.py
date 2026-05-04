import os
from dotenv import load_dotenv
from dataclasses import dataclass

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

    def __post_init__(self) -> None:
        env = self.ENVIRONMENT.lower()
        if env in {"prod", "production", "mainnet"}:
            self.ENVIRONMENT = "prod"
            self.RPC_URL = self.ARBITRUM_RPC_URL
        else:
            self.ENVIRONMENT = "testnet"
            self.RPC_URL = self.SEPOLIA_RPC_URL or self.ARBITRUM_RPC_URL

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
