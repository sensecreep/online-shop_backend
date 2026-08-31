from datetime import datetime
import aio_pika
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from jose import JWTError
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from models import ALGORITHM, SECRET_KEY, UserCreate, UserLogin, create_access_token, hash_password, verify_password
from bd import Cart, Orders, Records, User, async_session, get_async_session, process_message
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from logging_config import setup_logging
import logging

# Настройка логирования
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

#Регистрация пользователя
@app.post("/signup")
async def register(user: UserCreate, db: AsyncSession = Depends(get_async_session)):
    try:
        logger.info(f"Received registration request for email: {user.email}")
        async with db.begin():
            result = await db.execute(select(User).where(User.email == user.email))
            existing_user = result.scalars().first()
            if existing_user:
                logger.warning(f"Registration failed: User with email {user.email} already exists.")
                raise HTTPException(status_code=400, detail="User with this email already registered")

            hashed_password = hash_password(user.password)
            new_user = User(
                username=user.username,
                email=user.email,
                hashed_password=hashed_password,
                role="user"
            )
            db.add(new_user)
            await db.commit()
            logger.info(f"User with email {user.email} registered successfully.")
        return JSONResponse(content={"message": "User registered successfully"})
    except HTTPException as e:
        logger.error(f"HTTPException during registration: {e.detail}")
        raise HTTPException(status_code=500, detail={e})
    except Exception as e:
        logger.error(f"An unexpected error occurred during registration: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

#Авторизация пользователя
@app.post("/login")
async def login(user: UserLogin, db: AsyncSession = Depends(get_async_session)):
    try:
        async with db.begin():
            result = await db.execute(select(User).where(User.email == user.email))
            db_user = result.scalars().first()
            if not db_user or not verify_password(user.password, db_user.hashed_password):
                logger.warning(f"Failed login attempt for email: {user.email}")
                raise HTTPException(status_code=401, detail="Invalid email or password")
            
            # Генерация токена с добавлением роли пользователя
            access_token = create_access_token(data={"sub": db_user.id, "role": db_user.role})
            logger.info(f"User {db_user.email} successfully logged in.")
            return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Error during login process: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

#Функция для извлечения текущего пользователя
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

connection: aio_pika.RobustConnection | None = None

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
