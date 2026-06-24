"""SQLite persistence. Single file, zero config, fine for a single-machine alpha."""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "unplayable.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS submission (
                id           TEXT PRIMARY KEY,
                created_at   TEXT NOT NULL,
                image_path   TEXT,
                user_note    TEXT,
                situation    TEXT,
                ruling_type  TEXT,
                verdict      TEXT,
                explanation  TEXT,
                rule_number  TEXT,
                rule_url     TEXT,
                confidence   REAL,
                model_used   TEXT,
                session_id   TEXT
            );

            CREATE TABLE IF NOT EXISTS vote (
                id            TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL,
                session_id    TEXT NOT NULL,
                value         INTEGER NOT NULL,
                created_at    TEXT NOT NULL,
                UNIQUE(submission_id, session_id)
            );
            """
        )


def insert_submission(rec: dict) -> str:
    sid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            """INSERT INTO submission
               (id, created_at, image_path, user_note, situation, ruling_type, verdict,
                explanation, rule_number, rule_url, confidence, model_used, session_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sid,
                _now(),
                rec.get("image_path"),
                rec.get("user_note"),
                rec.get("situation"),
                rec.get("ruling_type"),
                rec.get("verdict"),
                rec.get("explanation"),
                rec.get("rule_number"),
                rec.get("rule_url"),
                rec.get("confidence"),
                rec.get("model_used"),
                rec.get("session_id"),
            ),
        )
    return sid


def get_feed(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT s.*,
                      COALESCE(SUM(v.value), 0) AS score,
                      COUNT(v.id)               AS vote_count
               FROM submission s
               LEFT JOIN vote v ON v.submission_id = s.id
               GROUP BY s.id
               ORDER BY s.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_vote(submission_id: str, session_id: str, value: int) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO vote (id, submission_id, session_id, value, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(submission_id, session_id)
               DO UPDATE SET value = excluded.value, created_at = excluded.created_at""",
            (str(uuid.uuid4()), submission_id, session_id, value, _now()),
        )
