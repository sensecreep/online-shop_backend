import logging
import sys

# Настройка логгера
def setup_logging():
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"
    
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("app.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
