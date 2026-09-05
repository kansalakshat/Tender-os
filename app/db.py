from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/tenders"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_db():
    """FastAPI dependency. Lives here, not in api.py, so app/web.py shares the
    exact same one -- two copies means a test override reaches only one of them.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
