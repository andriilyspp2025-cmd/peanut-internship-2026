import logging
from datetime import datetime
from pathlib import Path


def setup_logger(
    log_dir: str = "logs", filename_prefix: str = "bot", level: str = "INFO"
) -> str:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = Path(log_dir) / f"{filename_prefix}_{timestamp}.log"

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    return str(log_path)
