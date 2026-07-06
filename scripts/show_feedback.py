"""Print wrong-call reports, newest first. Run from the project root."""
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select  # noqa: E402

from backend import db  # noqa: E402

with db.engine.connect() as conn:
    rows = conn.execute(
        select(db.feedback).order_by(db.feedback.c.created_at.desc())
    ).mappings().all()

if not rows:
    print("No feedback yet.")
for r in rows:
    ref = f"  ruling {r['submission_id'][:8]}…" if r["submission_id"] else ""
    contact = f"  <{r['contact']}>" if r["contact"] else ""
    print(f"[{r['created_at']:%d %b %Y %H:%M}]{ref}{contact}\n  {r['message']}\n")
