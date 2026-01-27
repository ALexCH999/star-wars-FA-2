from dotenv import load_dotenv
import os
from sqlmodel import create_engine, SQLModel

# загружаем .env рядом с main.py
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не установлена. Скопируйте .env.example -> .env и заполните.")

engine = create_engine(DATABASE_URL, echo=True)
