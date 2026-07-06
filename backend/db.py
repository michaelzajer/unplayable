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
    Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, delete, func, insert,
    inspect, select, text,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# The six stamps, lucky to cursed. This tuple is the single source of truth;
# the adapter and the API both validate against it.
STAMPS = ("gift", "fluke", "fair_cop", "stiff", "cooked", "brutal")

# "Worst lies" is a weighted sort, not a raw count.
STAMP_WEIGHTS = {"brutal": 3, "cooked": 2, "stiff": 1}


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
    Column("shared", Boolean, default=False),
    Column("suggested_stamp", String),
    # Optional round details, supplied by the golfer.
    Column("course", String),
    Column("hole", Integer),
    Column("played_on", String),  # ISO date YYYY-MM-DD; string for cross-dialect simplicity
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
    Column("stamp", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("submission_id", "session_id", name="uq_vote_once"),
)


feedback = Table(
    "feedback", metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("submission_id", String),  # the ruling it concerns, when known
    Column("session_id", String),
    Column("message", Text, nullable=False),
    Column("contact", String),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    metadata.create_all(engine)
    insp = inspect(engine)

    # Lightweight migrations for databases created before these columns existed.
    sub_cols = {c["name"] for c in insp.get_columns("submission")}
    with engine.begin() as conn:
        if "shared" not in sub_cols:
            conn.execute(text("ALTER TABLE submission ADD COLUMN shared BOOLEAN DEFAULT FALSE"))
        if "suggested_stamp" not in sub_cols:
            conn.execute(text("ALTER TABLE submission ADD COLUMN suggested_stamp VARCHAR"))
        if "course" not in sub_cols:
            conn.execute(text("ALTER TABLE submission ADD COLUMN course VARCHAR"))
            conn.execute(text("ALTER TABLE submission ADD COLUMN hole INTEGER"))
            conn.execute(text("ALTER TABLE submission ADD COLUMN played_on VARCHAR"))

    # Vote model migration: boolean up/down votes become stamps. Every legacy vote
    # maps to 'brutal' (the old button meant "what a shocker"), then the numeric
    # column is dropped so inserts against the new schema work on old databases.
    vote_cols = {c["name"] for c in insp.get_columns("vote")}
    with engine.begin() as conn:
        if "stamp" not in vote_cols:
            conn.execute(text("ALTER TABLE vote ADD COLUMN stamp VARCHAR"))
            conn.execute(text("UPDATE vote SET stamp = 'brutal' WHERE stamp IS NULL"))
        if "value" in vote_cols:
            conn.execute(text("ALTER TABLE vote DROP COLUMN value"))


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
            suggested_stamp=rec.get("suggested_stamp"),
            course=rec.get("course"),
            hole=rec.get("hole"),
            played_on=rec.get("played_on"),
        ))
    return sid


def get_submission(submission_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            select(submission).where(submission.c.id == submission_id)
        ).mappings().first()
    return dict(row) if row else None


def get_feed(limit: int = 50, sort: str = "latest",
             session_id: str | None = None, mine: bool = False) -> list[dict]:
    """Feed rows with per-stamp tallies.

    sort: 'latest' (newest first) or 'worst' (weighted: brutal 3, cooked 2, stiff 1).
    mine: only the caller's own submissions (shared or not), identified by session_id.
    Aggregation happens in Python: at feed scale (LIMIT-bounded) this is simpler and
    portable across SQLite and Postgres.
    """
    s, v = submission, vote

    q = select(s)
    if mine:
        if not session_id:
            return []
        q = q.where(s.c.session_id == session_id)
    else:
        q = q.where(s.c.shared == True)  # noqa: E712 - only lies the user chose to post
    q = q.order_by(s.c.created_at.desc()).limit(max(limit * 4, 200))

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(q).mappings().all()]
        ids = [r["id"] for r in rows]
        counts: dict[str, dict[str, int]] = {}
        mine_map: dict[str, str] = {}
        if ids:
            tq = (
                select(v.c.submission_id, v.c.stamp, func.count(v.c.id).label("n"))
                .where(v.c.submission_id.in_(ids))
                .group_by(v.c.submission_id, v.c.stamp)
            )
            for sid, stamp, n in conn.execute(tq):
                counts.setdefault(sid, {})[stamp] = n
            if session_id:
                mq = select(v.c.submission_id, v.c.stamp).where(
                    (v.c.submission_id.in_(ids)) & (v.c.session_id == session_id))
                mine_map = {sid: stamp for sid, stamp in conn.execute(mq)}

    for r in rows:
        tally = {stamp: counts.get(r["id"], {}).get(stamp, 0) for stamp in STAMPS}
        r["stamp_counts"] = tally
        r["vote_count"] = sum(tally.values())
        r["worst_score"] = sum(STAMP_WEIGHTS.get(k, 0) * n for k, n in tally.items())
        r["my_stamp"] = mine_map.get(r["id"])

    if sort == "worst":
        rows.sort(key=lambda r: (r["worst_score"], r["created_at"]), reverse=True)
    return rows[:limit]


def set_shared(submission_id: str) -> bool:
    from sqlalchemy import update
    with engine.begin() as conn:
        result = conn.execute(
            update(submission).where(submission.c.id == submission_id).values(shared=True)
        )
    return result.rowcount > 0


def add_vote(submission_id: str, session_id: str, stamp: str) -> bool:
    """Apply one stamp per (submission, session), replacing any prior stamp.

    Returns False if the stamp is invalid or the submission does not exist /
    is not stampable — callers must not be able to stamp arbitrary ids. A lie is
    stampable once shared, or at any time by the golfer who submitted it (so the
    result page can carry a stamp before the lie is posted to the feed).
    """
    if stamp not in STAMPS:
        return False
    with engine.begin() as conn:
        row = conn.execute(
            select(submission.c.shared, submission.c.session_id).where(
                submission.c.id == submission_id)
        ).first()
        if not row or not (row.shared or (row.session_id and row.session_id == session_id)):
            return False
        conn.execute(delete(vote).where(
            (vote.c.submission_id == submission_id) & (vote.c.session_id == session_id)))
        conn.execute(insert(vote).values(
            id=str(uuid.uuid4()), submission_id=submission_id,
            session_id=session_id, stamp=stamp, created_at=_now()))
    return True


def insert_feedback(rec: dict) -> str:
    fid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(insert(feedback).values(
            id=fid, created_at=_now(),
            submission_id=rec.get("submission_id"),
            session_id=rec.get("session_id"),
            message=rec["message"],
            contact=rec.get("contact"),
        ))
    return fid


def clear_vote(submission_id: str, session_id: str) -> None:
    """Remove the caller's stamp (tapping the applied stamp un-stamps it)."""
    with engine.begin() as conn:
        conn.execute(delete(vote).where(
            (vote.c.submission_id == submission_id) & (vote.c.session_id == session_id)))
