from fastapi import FastAPI, Depends, HTTPException, Request
from jose import JWTError
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from models import ALGORITHM, SECRET_KEY, Admin, Record, create_access_token, verify_password
from bd import Genre, Records, Admins, get_async_session, send_to_rabbitmq
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from logging_config import setup_logging
import logging

# Настройка логирования
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI()

"""
genre1 = Genre(name = "Rock")
genre2 = Genre(name = "Alternative")
genre3 = Genre(name = "Indie")

record1 = Records(album_name = "OK Computer", band_name = "Radiohead", price = 6000, in_stock = 15)
record2 = Records(album_name = "Kid A", band_name = "Radiohead", price = 5000, in_stock = 10)

record1.genre.append(genre1)
record1.genre.append(genre2)
record2.genre.append(genre2)
record2.genre.append(genre3)

db.add_all([record1, record2, genre1, genre2, genre3])
db.commit()
"""
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:8000/login")

"""@app.middleware("http")
async def check_admin_role(request: Request, call_next):
    token = request.headers.get("Authorization")
    if not token:
        raise HTTPException(status_code=403, detail="Authorization token is required")

    try:
        payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        role = payload.get("role")
        if role != "admin":
            raise HTTPException(status_code=403, detail="Admin privileges are required")
    except Exception as e:
        raise HTTPException(status_code=403, detail="Invalid token")

    response = await call_next(request)
    return response
"""
async def get_current_admin(token: str = Depends(oauth2_scheme)):
    """Проверяет, что пользователь является администратором"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        role: str = payload.get("role")
        if not user_id or not role:
            raise credentials_exception
        if role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: admin only",
            )
        return {"user_id": user_id, "role": role}
    except JWTError as e:
        raise credentials_exception

@app.get("/catalog")
async def get_catalog(db: AsyncSession = Depends(get_async_session)):
    """Выводит список всех пластинок"""
    logger.info("Received request to fetch catalog.")

    try:
        result = []
        query = await db.execute(select(Genre).options(joinedload(Genre.records)))
        genres = query.scalars().all()
        logger.info(f"Fetched {len(genres)} genres from the database.")

        for genr in genres:
            data = {
                "genre_name": genr.name,
                "records": [
                    {
                        "album_name": record.album_name,
                        "band_name": record.band_name,
                        "price": record.price,
                        "in_stock": record.in_stock,
                    } for record in genr.records
                ]
            }
            result.append(data)
        logger.info("Successfully processed catalog request.")
        return result
    except Exception as e:
        logger.error(f"Error occurred while fetching catalog: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/create_record")
async def create_record(record: Record, genre_name: str, db: AsyncSession = Depends(get_async_session), admin: Admin = Depends(get_current_admin)):
    """Создает пластинку"""
    logger.info(f"Received request to create a new record: {record} in genre '{genre_name}'.")

    try:
        new_record = Records(
            album_name=record.album_name,
            band_name=record.band_name,
            price=record.price,
            in_stock=record.in_stock,
        )
        logger.info("Checking if the genre exists in the database.")
        query = await db.execute(select(Genre).filter(Genre.name == genre_name))
        db_genre = query.scalars().first()

        if db_genre:
            logger.info(f"Genre '{genre_name}' found. Adding record to the existing genre.")
            new_record.genre.append(db_genre)
        else:
            logger.info(f"Genre '{genre_name}' not found. Creating a new genre and associating it with the record.")
            new_genre = Genre(name=genre_name)
            new_record.genre.append(new_genre)

        db.add(new_record)
        await db.commit()
        logger.info(f"New record '{record.album_name}' by '{record.band_name}' created successfully with ID {new_record.id}.")

        # Отправка обновлений в RabbitMQ
        await send_to_rabbitmq({
            "action": "add",
            "id": new_record.id,
            "album_name": new_record.album_name,
            "band_name": new_record.band_name,
            "price": new_record.price,
            "in_stock": new_record.in_stock,
        })
        logger.info("Update successfully sent to RabbitMQ.")
        return new_record
    except Exception as e:
        logger.error(f"Error occurred while creating a record: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.put("/catalog/{record_id}")
async def update_product(record_id: int, album_name: str = None, price: int = None, db: AsyncSession = Depends(get_async_session), admin: Admin = Depends(get_current_admin),):
    logger.info(f"Received request to update record with ID {record_id}. Changes: album_name={album_name}, price={price}")

    try:
        logger.info("Fetching the record from the database.")
        query = await db.execute(select(Records).filter(Records.id == record_id))
        record = query.scalars().first()
        if not record:
            logger.warning(f"Record with ID {record_id} not found.")
            raise HTTPException(status_code=404, detail="Record not found")
        if album_name:
            logger.info(f"Updating album_name of record ID {record_id} to '{album_name}'.")
            record.album_name = album_name
        if price:
            logger.info(f"Updating price of record ID {record_id} to {price}.")
            record.price = price
        await db.commit()
        await db.refresh(record)
        logger.info(f"Record with ID {record_id} successfully updated.")

        # Отправка обновлений в RabbitMQ
        await send_to_rabbitmq({
            "action": "update",
            "id": record.id,
            "album_name": record.album_name,
            "band_name": record.band_name,
            "price": record.price,
            "in_stock": record.in_stock,
        })
        logger.info("Update successfully sent to RabbitMQ.")
        return record
    except Exception as e:
        logger.error(f"An error occurred while updating record with ID {record_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.delete("/delete_record/{record_id}")
async def delete_post(record_id: int, db: AsyncSession = Depends(get_async_session), admin: Admin = Depends(get_current_admin),):
    """Удаляет товар"""
    logger.info(f"Received request to delete record with ID {record_id}.")

    try:
        query = await db.execute(select(Records).filter(Records.id == record_id))
        db_record = query.scalars().first()
        if not db_record:
            logger.warning(f"Record with ID {record_id} not found.")
            raise HTTPException(status_code=404, detail="Post not found")
        await db.delete(db_record)
        await db.commit()
        logger.info(f"Record with ID {record_id} successfully deleted.")

        # Отправка сообщения об удалении в RabbitMQ
        await send_to_rabbitmq({
            "action": "delete",  # Указываем, что действие — удаление
            "id": record_id
        })
        logger.info(f"Delete message for record ID {record_id} successfully sent to RabbitMQ.")
        return {"message": "Record deleted successfully"}
    except Exception as e:
        logger.error(f"An error occurred while deleting record with ID {record_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
