"""
Storage layer.

Talks to any SQL database via the DATABASE_URL environment variable:
  - unset            -> local SQLite file (zero setup, for development)
  - a Postgres URL   -> managed Postgres (Neon, Supabase, Cloud SQL, Render, Railway...)

Images are stored as rows in their own table, so the app keeps NO local files and
runs statelessly on any host. The image bytes live apart from the submission row so
the feed query never has to carry them.
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, LargeBinary, MetaData,
    String, Table, Text, create_engine, delete, func, insert, select,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DATA_DIR / 'unplayable.db'}"
    # Managed hosts often hand out a 'postgres://' URL; SQLAlchemy wants an explicit driver.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
metadata = MetaData()

submission = Table(
    "submission", metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("image_path", String),
    Column("user_note", Text),
    Column("situation", Text),
    Column("ruling_type", String),
    Column("verdict", Text),
    Column("explanation", Text),
    Column("rule_number", String),
    Column("rule_url", Text),
    Column("confidence", Float),
    Column("model_used", String),
    Column("session_id", String),
)

image = Table(
    "image", metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("content_type", String, nullable=False),
    Column("data", LargeBinary, nullable=False),
)

vote = Table(
    "vote", metadata,
    Column("id", String, primary_key=True),
    Column("submission_id", String, ForeignKey("submission.id"), nullable=False),
    Column("session_id", String, nullable=False),
    Column("value", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    metadata.create_all(engine)


def insert_image(data: bytes, content_type: str) -> str:
    image_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(insert(image).values(
            id=image_id, created_at=_now(), content_type=content_type, data=data))
    return image_id


def get_image(image_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            select(image.c.content_type, image.c.data).where(image.c.id == image_id)
        ).first()
    return (row.content_type, row.data) if row else None


def insert_submission(rec: dict) -> str:
    sid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(insert(submission).values(
            id=sid,
            created_at=_now(),
            image_path=rec.get("image_path"),
            user_note=rec.get("user_note"),
            situation=rec.get("situation"),
            ruling_type=rec.get("ruling_type"),
            verdict=rec.get("verdict"),
            explanation=rec.get("explanation"),
            rule_number=rec.get("rule_number"),
            rule_url=rec.get("rule_url"),
            confidence=rec.get("confidence"),
            model_used=rec.get("model_used"),
            session_id=rec.get("session_id"),
        ))
    return sid


def get_feed(limit: int = 50) -> list[dict]:
    s, v = submission, vote
    # Aggregate votes in a subquery so the outer SELECT needs no GROUP BY.
    # (Postgres rejects selecting non-grouped columns; this keeps it portable.)
    tally = (
        select(
            v.c.submission_id.label("sid"),
            func.coalesce(func.sum(v.c.value), 0).label("score"),
            func.count(v.c.id).label("vote_count"),
        )
        .group_by(v.c.submission_id)
        .subquery()
    )
    q = (
        select(
            s,
            func.coalesce(tally.c.score, 0).label("score"),
            func.coalesce(tally.c.vote_count, 0).label("vote_count"),
        )
        .select_from(s.outerjoin(tally, tally.c.sid == s.c.id))
        .order_by(s.c.created_at.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(q).mappings().all()
    return [dict(r) for r in rows]


def add_vote(submission_id: str, session_id: str, value: int) -> None:
    value = 1 if value > 0 else -1
    # One vote per (submission, session): replace any prior vote. Portable across dialects.
    with engine.begin() as conn:
        conn.execute(delete(vote).where(
            (vote.c.submission_id == submission_id) & (vote.c.session_id == session_id)))
        conn.execute(insert(vote).values(
            id=str(uuid.uuid4()), submission_id=submission_id,
            session_id=session_id, value=value, created_at=_now()))
