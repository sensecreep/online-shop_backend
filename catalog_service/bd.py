import json
import aio_pika
from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import pika
from logging_config import setup_logging
import logging

# Настройка логирования
setup_logging()
logger = logging.getLogger(__name__)

DATABASE_URL = "postgresql+asyncpg://postgres:pass@localhost:5432/catalog_server"
Base = declarative_base()

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

async def get_async_session():
    async with async_session() as session:
        yield session

# Вспомогательная таблица для связи "многие ко многим"
genre_records_association = Table(
    'genre_records', Base.metadata,
    Column('genre_id', Integer, ForeignKey('genre.id'), primary_key=True),
    Column('record_id', Integer, ForeignKey('records.id'), primary_key=True)
)

class Genre(Base):
    __tablename__ = "genre"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)

    records = relationship("Records", secondary=genre_records_association, back_populates="genre")

class Records(Base):
    __tablename__ = "records"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    album_name = Column(String, nullable=False)
    band_name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    in_stock = Column(Integer, nullable=False)

    genre = relationship("Genre", secondary=genre_records_association, back_populates="records")

class Admins(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)

#Base.metadata.create_all(bind=engine)

#Настройка подключения к RabbitMQ
async def send_to_rabbitmq(message: dict):
    logger.info("Attempting to send message to RabbitMQ.")

    try:
        connection = await aio_pika.connect_robust("amqp://guest:guest@localhost/")
        async with connection:
            logger.info("Successfully connected to RabbitMQ.")
            channel = await connection.channel()
            await channel.default_exchange.publish(
                aio_pika.Message(body=json.dumps(message).encode()),
                routing_key="catalog_updates",
            )
            logger.info(f"Message sent to RabbitMQ with routing_key 'catalog_updates': {message}")
    except Exception as e:
        logger.error(f"Unexpected error occurred: {e}")
