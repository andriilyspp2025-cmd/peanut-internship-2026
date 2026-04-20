import asyncio
import logging
from src.config.logger import setup_logger


async def main():
    setup_logger()
    logger = logging.getLogger("MAIN")
    logger.info("Запуск Peanut Arb Bot")
    logger.info("Вхід в основний цикл бота")
    while True:

        await asyncio.sleep(0.5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("MAIN").info("Bot stopped by user")
