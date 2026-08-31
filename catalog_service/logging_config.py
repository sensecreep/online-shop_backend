import logging
import sys

# Настройка логгера
def setup_logging():
    # Формат сообщений
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"
    
    # Уровень логирования
    logging.basicConfig(
        level=logging.INFO,  # Выводить сообщения INFO и выше
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),  # Вывод в консоль
            logging.FileHandler("app.log", encoding="utf-8"),  # Вывод в файл
        ],
    )
    # Установка уровня логирования для сторонних библиотек (например, SQLAlchemy)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)  # Скрыть лишнюю информацию от SQLAlchemy