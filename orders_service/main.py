from datetime import datetime
import aio_pika
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from jose import JWTError
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from models import ALGORITHM, SECRET_KEY, CreateOrderRequest
from bd import Cart, Orders, Records, User, async_session, get_async_session, process_message
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from logging_config import setup_logging
import logging

# Настройка логирования
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:8000/login")

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_async_session)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            logger.error("User ID not found in token payload.")
            raise credentials_exception
        logger.info(f"Decoded user_id: {user_id}")
    except JWTError as e:
        logger.error(f"JWT decoding failed: {e}")
        raise credentials_exception
    try:
        async with db.begin():
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalars().first()
            if user is None:
                logger.warning(f"User with ID {user_id} not found in the database.")
                raise credentials_exception
            logger.info(f"User with ID {user_id} found: {user}")
            return user
    except Exception as e:
        logger.error(f"Database query failed: {e}")
        raise credentials_exception

#Добавление товаров в корзину
@app.post("/cart")
async def add_to_cart(
    record_id: int,
    quantity: int = 1,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user)
):
    logger.info(f"User {user.id} is trying to add record {record_id} to the cart.")
    try:
        async with db.begin():
            result = await db.execute(select(Records).where(Records.id == record_id))
            record = result.scalars().first()
            if not record:
                logger.warning(f"Record with ID {record_id} not found.")
                raise HTTPException(status_code=404, detail="Record not found")
            logger.info(f"Record found: {record}")
            result = await db.execute(
                select(Cart).where(Cart.user_id == user.id, Cart.record_id == record_id)
            )
            cart_item = result.scalars().first()
            if cart_item:
                logger.info(f"Record with ID {record_id} already in the cart. Updating quantity.")
                cart_item.quantity += quantity
            else:
                logger.info(f"Adding record with ID {record_id} into the cart")
                new_cart_item = Cart(user_id=user.id, record_id=record_id, quantity=quantity)
                db.add(new_cart_item)
            await db.commit()
            logger.info(f"Record with ID {record_id} added to user {user.id}'s cart with quantity {quantity}.")
        return {"message": "Record added to cart"}
    except Exception as e:
        logger.error(f"Error occurred while adding record {record_id} to cart: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/order")
async def create_order(order_request: CreateOrderRequest, db: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user)):
    logger.info(f"User {user.id} is trying to create an order.")
    try:
        async with db.begin():
            result = await db.execute(select(Cart).where(Cart.user_id == user.id))
            cart_items = result.scalars().all()

            if not cart_items:
                logger.warning(f"User {user.id} tried to create an order, but their cart is empty.")
                raise HTTPException(status_code=400, detail="Cart is empty")

            total_price = 0
            records = []
            for item in cart_items:
                result = await db.execute(select(Records).where(Records.id == item.record_id))
                record = result.scalars().first()
                if not record:
                    raise HTTPException(status_code=404, detail=f"Record with ID {item.record_id} not found")
                total_price += record.price * item.quantity
                records.append(record)

            new_order = Orders(user_id=user.id, total_price=total_price, order_date=datetime.now(), records=records)
            db.add(new_order)
            await db.execute(select(Cart).where(Cart.user_id == user.id).delete())
            await db.commit()
            await db.refresh(new_order)
        return {"message": "Order created successfully", "order_id": new_order.id, "total_price": new_order.total_price}
    except Exception as e:
        logger.error(f"Error occurred while creating order for user {user.id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.on_event("startup")
async def start_consumer():
    global connection
    try:
        logger.info("Starting RabbitMQ consumer...")
        # Подключение к RabbitMQ
        connection = await aio_pika.connect_robust("amqp://guest:guest@localhost/")
        channel = await connection.channel()
        # Объявляем очередь
        queue = await channel.declare_queue("catalog_updates")
        # Начинаем потребление сообщений
        await queue.consume(process_message)
        logger.info("RabbitMQ consumer successfully started.")
    except Exception as e:
        logger.error(f"Failed to start RabbitMQ consumer: {e}")

@app.on_event("shutdown")
async def shutdown_consumer():
    global connection
    try:
        if connection:
            await connection.close()
        logger.info("RabbitMQ connection closed.")
    except Exception as e:
        logger.error(f"Error while shutting down RabbitMQ connection: {e}")