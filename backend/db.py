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

# Rating model v4: TWO independent ratings per golfer per lie.
#   luck  — 5-notch slider on THE LIE: "1" good lie .. "5" hard luck
#   call  — thumbs on THE RULING: good_call / bad_call
LUCK_SET = frozenset(("1", "2", "3", "4", "5"))
CALL_SET = frozenset(("good_call", "bad_call"))
STAMPS = ("1", "2", "3", "4", "5", "good_call", "bad_call")

# "The Feed" sort: hardest-luck lies first (call votes carry no weight).
STAMP_WEIGHTS = {"2": 1, "3": 2, "4": 3, "5": 4}


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
    # The Feed sort: most-shared first. featured pins a highlight to the very top.
    Column("share_count", Integer, default=0),
    Column("featured", Boolean, default=False),
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
    # No unique constraint: one luck row AND one call row may coexist per
    # (submission, session). add_vote enforces one-per-kind by replacement.
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


import re as _re  # noqa: E402


def ruling_slug(situation: str | None, verdict: str | None, sid: str) -> str:
    """Readable, keyword-rich slug for a share URL: '<words>-<shortid>', e.g.
    'ball-against-a-tree-root-a2691fd7'. The trailing 8 hex chars uniquely
    resolve back to the submission (see get_submission_by_prefix)."""
    text = (situation or verdict or "golf-ruling")
    slug = _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    short = sid.replace("-", "")[:8]
    return f"{slug}-{short}" if slug else short


def get_submission_by_prefix(prefix: str):
    """Resolve a submission from the short id at the end of a slug URL."""
    if not prefix or len(prefix) < 6:
        return None
    with engine.connect() as conn:
        rows = conn.execute(
            select(submission).where(submission.c.id.like(prefix + "%")).limit(2)
        ).mappings().all()
    return dict(rows[0]) if len(rows) == 1 else None


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
        if "share_count" not in sub_cols:
            conn.execute(text("ALTER TABLE submission ADD COLUMN share_count INTEGER DEFAULT 0"))
        if "featured" not in sub_cols:
            conn.execute(text("ALTER TABLE submission ADD COLUMN featured BOOLEAN DEFAULT FALSE"))

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

    # Rating v4 migration: the six lie-stamps predate both current scales and
    # are DELETED rather than faked. good_call/bad_call rows remain valid.
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM vote WHERE stamp IN "
            "('gift', 'fluke', 'fair_cop', 'stiff', 'cooked', 'brutal')"))
    # Databases created under v2/v3 carry a one-vote-per-session constraint that
    # would block holding a luck AND a call rating at once. Drop it (Postgres).
    if engine.dialect.name.startswith("postgres"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE vote DROP CONSTRAINT IF EXISTS uq_vote_once"))


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
        mine_luck: dict[str, str] = {}
        mine_call: dict[str, str] = {}
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
                for sid, stamp in conn.execute(mq):
                    (mine_luck if stamp in LUCK_SET else mine_call)[sid] = stamp

    for r in rows:
        tally = {stamp: counts.get(r["id"], {}).get(stamp, 0) for stamp in STAMPS}
        r["stamp_counts"] = tally
        r["vote_count"] = sum(tally.values())
        r["worst_score"] = sum(STAMP_WEIGHTS.get(k, 0) * n for k, n in tally.items())
        r["my_luck"] = mine_luck.get(r["id"])
        r["my_call"] = mine_call.get(r["id"])
        r["share_path"] = "/r/" + ruling_slug(r.get("situation"), r.get("verdict"), r["id"])

    if sort == "worst":
        # "The Feed": a featured pin first, then most-shared, then newest.
        # (Legacy luck weighting kept as the final tiebreak.)
        rows.sort(key=lambda r: (bool(r.get("featured")), r.get("share_count") or 0,
                                 r["worst_score"], r["created_at"]), reverse=True)
    return rows[:limit]


def set_shared(submission_id: str) -> bool:
    from sqlalchemy import update
    with engine.begin() as conn:
        result = conn.execute(
            update(submission).where(submission.c.id == submission_id).values(shared=True)
        )
    return result.rowcount > 0


def add_share(submission_id: str) -> bool:
    """Count one share of a ruling — drives The Feed's most-shared sort."""
    from sqlalchemy import update
    with engine.begin() as conn:
        result = conn.execute(
            update(submission).where(submission.c.id == submission_id)
            .values(share_count=func.coalesce(submission.c.share_count, 0) + 1)
        )
    return result.rowcount > 0


def set_featured(submission_id: str, featured: bool) -> bool:
    """Pin (or unpin) a ruling to the very top of The Feed — editorial highlight."""
    from sqlalchemy import update
    with engine.begin() as conn:
        result = conn.execute(
            update(submission).where(submission.c.id == submission_id)
            .values(featured=featured)
        )
    return result.rowcount > 0


def list_shared() -> list[dict]:
    """Slug path + created_at for every lie on the public feed — feeds the sitemap."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(submission.c.id, submission.c.created_at,
                   submission.c.situation, submission.c.verdict)
            .where(submission.c.shared.is_(True))
            .order_by(submission.c.created_at.desc())
        ).all()
    return [{"id": r[0], "created_at": r[1],
             "share_path": "/r/" + ruling_slug(r[2], r[3], r[0])} for r in rows]


def add_vote(submission_id: str, session_id: str, stamp: str) -> bool:
    """Apply one stamp per (submission, session), replacing any prior stamp.

    Returns False if the stamp is invalid or the submission does not exist /
    is not stampable — callers must not be able to stamp arbitrary ids. A lie is
    stampable once shared, or at any time by the golfer who submitted it (so the
    result page can carry a stamp before the lie is posted to the feed).
    """
    if stamp not in STAMPS:
        return False
    same_kind = tuple(LUCK_SET) if stamp in LUCK_SET else tuple(CALL_SET)
    with engine.begin() as conn:
        row = conn.execute(
            select(submission.c.shared, submission.c.session_id).where(
                submission.c.id == submission_id)
        ).first()
        if not row or not (row.shared or (row.session_id and row.session_id == session_id)):
            return False
        # Replace only the same KIND of rating; the other kind stays put.
        conn.execute(delete(vote).where(
            (vote.c.submission_id == submission_id)
            & (vote.c.session_id == session_id)
            & (vote.c.stamp.in_(same_kind))))
        conn.execute(insert(vote).values(
            id=str(uuid.uuid4()), submission_id=submission_id,
            session_id=session_id, stamp=stamp, created_at=_now()))
    return True


def get_stamp_counts(submission_id: str) -> dict:
    """Per-stamp tallies for one lie, every stamp present (zeros included)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(vote.c.stamp, func.count(vote.c.id))
            .where(vote.c.submission_id == submission_id)
            .group_by(vote.c.stamp)
        ).all()
    counts = {s: 0 for s in STAMPS}
    for stamp, n in rows:
        if stamp in counts:
            counts[stamp] = n
    return counts


def export_call_ratings() -> list[dict]:
    """Every ruling with its good_call/bad_call tallies — training signal for
    improving the model. Rulings with zero votes are included so the absence
    of a rating is visible too. Newest first."""
    s, v = submission, vote
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                s.c.id, s.c.created_at, s.c.user_note, s.c.situation,
                s.c.ruling_type, s.c.verdict, s.c.explanation,
                s.c.rule_number, s.c.rule_url, s.c.confidence,
                s.c.model_used, s.c.shared,
            ).order_by(s.c.created_at.desc())
        ).mappings().all()
        tq = (
            select(v.c.submission_id, v.c.stamp, func.count(v.c.id))
            .where(v.c.stamp.in_(tuple(CALL_SET)))
            .group_by(v.c.submission_id, v.c.stamp)
        )
        tallies: dict = {}
        for sid, stamp, n in conn.execute(tq):
            tallies.setdefault(sid, {})[stamp] = n
    out = []
    for r in rows:
        rec = dict(r)
        if rec.get("created_at") is not None:
            rec["created_at"] = rec["created_at"].isoformat()
        t = tallies.get(rec["id"], {})
        rec["good_call"] = t.get("good_call", 0)
        rec["bad_call"] = t.get("bad_call", 0)
        out.append(rec)
    return out


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


def clear_vote(submission_id: str, session_id: str, kind: str = "call") -> None:
    """Remove the caller's rating of one kind (call or luck)."""
    values = tuple(CALL_SET) if kind == "call" else tuple(LUCK_SET)
    with engine.begin() as conn:
        conn.execute(delete(vote).where(
            (vote.c.submission_id == submission_id)
            & (vote.c.session_id == session_id)
            & (vote.c.stamp.in_(values))))
