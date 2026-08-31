from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, AsyncEngine
import json
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Table, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import pika
import asyncio
import aio_pika
from logging_config import setup_logging
import logging

# Настройка логирования
setup_logging()
logger = logging.getLogger(__name__)

#Подключение к БД и создание таблиц
DATABASE_URL = "postgresql+asyncpg://postgres:pass@localhost:5432/user_server"
Base = declarative_base()

async_engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

async def create_tables(async_engine: AsyncEngine):
    async with async_engine.begin() as conn:
        # Запуск DDL операций в асинхронном режиме
        await conn.run_sync(Base.metadata.create_all)

#Получение сессии базы данных
async def get_async_session():
    async with async_session() as session:
        yield session

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)

    user = relationship("Cart", back_populates="users")
    user = relationship("Orders", back_populates="users")

class Cart(Base):
    __tablename__ = "cart"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    record_id = Column(Integer, nullable=False)
    quantity = Column(Integer, default=1)

    user = relationship("User", back_populates="cart")

order_records_association = Table(
    "order_records", Base.metadata,
    Column("order_id", Integer, ForeignKey("orders.id"), primary_key=True),
    Column("record_id", Integer, ForeignKey("records.id"), primary_key=True)
)

class Orders(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    total_price = Column(Float, nullable=False)
    order_date = Column(DateTime, nullable=False)

    records = relationship("Records", secondary=order_records_association, back_populates="orders")
    user = relationship("User", back_populates="orders")

class Records(Base):
    __tablename__ = "records"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    album_name = Column(String, nullable=False)
    band_name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    in_stock = Column(Integer, nullable=False)

    orders = relationship("Orders", secondary=order_records_association, back_populates="records")

#Base.metadata.create_all(bind=engine)

async def process_message(message: aio_pika.IncomingMessage):
    async with message.process():
        try:
            logger.info("Received a message from RabbitMQ.")
            message_data = json.loads(message.body)
            action = message_data.get("action")  # Получаем тип действия из сообщения
            logger.info(f"Processing message: {message_data}")
            async with async_session() as session:
                if action == "delete":
                    # Удаление записи
                    result = await session.execute(
                        select(Records).where(Records.id == message_data["id"])
                    )
                    record = result.scalars().first()
                    if record:
                        await session.delete(record)
                        await session.commit()
                        logger.info(f"Record with ID {message_data['id']} deleted successfully.")
                    else:
                        logger.warning(f"Record with ID {message_data['id']} not found in local DB.")
                else:
                    result = await session.execute(
                        select(Records).where(Records.id == message_data['id'])
                    )
                    record = result.scalars().first()

                    if record:
                        logger.info(f"Updating record with ID {message_data['id']}.")
                        record.album_name = message_data['album_name']
                        record.band_name = message_data['band_name']
                        record.price = message_data['price']
                        record.in_stock = message_data['in_stock']
                    else:
                        logger.info(f"Creating a new record with ID {message_data['id']}.")
                        new_record = Records(
                            id=message_data['id'],
                            album_name=message_data['album_name'],
                            band_name=message_data['band_name'],
                            price=message_data['price'],
                            in_stock=message_data['in_stock'],
                        )
                        session.add(new_record)

                    await session.commit()
                    logger.info(f"Message processed successfully: {message_data}")
        except json.JSONDecodeError as e:
            logger.error(f"Decoding error: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
