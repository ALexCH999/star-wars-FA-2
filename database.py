from dotenv import load_dotenv
import os
from pathlib import Path
from sqlmodel import create_engine, SQLModel

# загружаем .env рядом с main.py
load_dotenv(Path(__file__).with_name(".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не установлена. Скопируйте .env.example -> .env и заполните.")

engine = create_engine(DATABASE_URL, echo=True)
