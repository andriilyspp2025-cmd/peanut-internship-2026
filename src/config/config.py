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
    RPC_URL: str = os.getenv("RPC_URL", "http://127.0.0.1:8545")
    FORK_URL: str = os.getenv("ETH_RPC_URL", "")

    # --- Binance Testnet (CEX) ---
    BINANCE_API_KEY: str = os.getenv("BINANCE_TESTNET_API_KEY", "")
    BINANCE_SECRET: str = os.getenv("BINANCE_TESTNET_SECRET", "")

    # --- System Params ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def binance_config(self) -> dict:
        """
        Повертає готовий словник конфігурації для ініціалізації ccxt.binance
        """
        if not self.BINANCE_API_KEY or not self.BINANCE_SECRET:
            raise ValueError(
                "BINANCE_TESTNET_API_KEY та BINANCE_TESTNET_SECRET повинні бути в .env"
            )

        return {
            "apiKey": self.BINANCE_API_KEY,
            "secret": self.BINANCE_SECRET,
            "sandbox": True,  # Testnet
            "options": {
                "defaultType": "spot",
            },
            "enableRateLimit": True,
        }


# Створюємо єдиний (Singleton) екземпляр для імпорту по всьому проекту
config = Settings()
