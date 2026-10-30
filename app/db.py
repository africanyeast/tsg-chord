from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
