from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def get_database_url() -> str:
    # Default to a local SQLite DB for dev; set DATABASE_URL on Ubuntu to Postgres.
    # Example: postgresql+psycopg://user:password@127.0.0.1:5432/garmin_tracker
    return os.getenv("DATABASE_URL") or "sqlite:///instance/app.db"


_ENGINE = None
_SessionLocal = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        url = get_database_url()
        connect_args = {}
        if url.startswith("sqlite:"):
            connect_args = {"check_same_thread": False}
        _ENGINE = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return _ENGINE


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            future=True,
            expire_on_commit=False,
        )
    return _SessionLocal


@contextmanager
def db_session():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
