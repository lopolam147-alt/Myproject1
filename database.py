"""
PostgreSQL database caching for search results.
Uses SQLAlchemy ORM with a 7-day TTL.
"""

from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv

load_dotenv()
POSTGRES_USER = os.getenv('POSTGRES_USER', 'lpl')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'lpl01470')
POSTGRES_DB = os.getenv('POSTGRES_DB', 'postgres')
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5433')  # default as string

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class SearchCache(Base):
    __tablename__ = "search_cache"
    id = Column(Integer, primary_key=True, index=True)
    query_hash = Column(String(64), index=True)  # SHA256 hash of query_text
    query_text = Column(Text)
    results = Column(JSON)  # list of product dicts
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(bind=engine)

def get_cached_results(query_text: str, max_age_days: int = 7) -> list | None:
    """
    Retrieve cached results for a given query if they exist and are not older than max_age_days.
    """
    import hashlib
    query_hash = hashlib.sha256(query_text.encode()).hexdigest()
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    with SessionLocal() as session:
        record = session.query(SearchCache).filter(
            SearchCache.query_hash == query_hash,
            SearchCache.created_at >= cutoff
        ).first()
        if record:
            return record.results
    return None

def cache_results(query_text: str, results: list):
    """
    Store results in the database, deleting any old entry for the same query first.
    Converts numpy types to Python native types to ensure JSON serialization.
    """
    import hashlib
    import json
    import numpy as np

    def convert_to_serializable(obj):
        if isinstance(obj, (np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.int32, np.int64)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError(f"Type {type(obj)} not serializable")

    cleaned_results = json.loads(json.dumps(results, default=convert_to_serializable))

    query_hash = hashlib.sha256(query_text.encode()).hexdigest()
    with SessionLocal() as session:
        session.query(SearchCache).filter(SearchCache.query_hash == query_hash).delete()
        cache_entry = SearchCache(
            query_hash=query_hash,
            query_text=query_text,
            results=cleaned_results
        )
        session.add(cache_entry)
        session.commit()
        print(f"✅ DB SAVE SUCCESS: {len(cleaned_results)} products cached for '{query_text}'")

def delete_old_entries(days: int = 7):
    """Delete cache entries older than 'days'."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    with SessionLocal() as session:
        session.query(SearchCache).filter(SearchCache.created_at < cutoff).delete()
        session.commit()