"""Sync the static, crawl-critical pages + assets into public/ for Firebase's edge.

Why: Firebase Hosting serves files in public/ straight from its CDN, BEFORE the
`**` rewrite to Cloud Run. Anything we place here is served at the edge with no
cold start, so it can never return a 5xx while the backend wakes. Only the truly
dynamic paths (/api/*, /r/ share pages, /sitemap.xml fallback) still reach Cloud Run.

frontend/ stays the single source of truth — you keep editing there and testing
with uvicorn. This copies the current static files into public/ at deploy time.

Run from the project root, before `firebase deploy`:
    python3 scripts/build_static.py
"""

import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
PUBLIC = ROOT / "public"

# frontend file  ->  public file (the renames are the routing: landing is the
# home page at /, the app shell lives at /app via cleanUrls).
PAGES = {
    "landing.html": "index.html",
    "index.html": "app.html",
    "about.html": "about.html",
    "feedback.html": "feedback.html",
}
ASSETS = [
    "og-image.png", "favicon.ico", "favicon.png", "apple-touch-icon.png",
    "icon-192.png", "icon-512.png", "manifest.json",
]


def _write_sitemap() -> str:
    """Build public/sitemap.xml from the DB so it serves from the edge too.
    Falls back to just the static pages if the database is unreachable."""
    load_dotenv()
    import os
    base = f"https://{os.environ.get('CANONICAL_HOST', 'golfrules.pro').strip() or 'golfrules.pro'}"
    urls = [(f"{base}/", None), (f"{base}/about", None)]
    note = "static pages only"
    try:
        sys.path.insert(0, str(ROOT))
        from backend import db  # noqa: E402
        rows = db.list_shared()
        for r in rows:
            lastmod = str(r["created_at"])[:10] if r["created_at"] else None
            urls.append((f"{base}/r/{r['id']}", lastmod))
        note = f"{len(rows)} shared lies"
    except Exception as e:  # DB unreachable at build time — ship the static pages.
        print(f"  ! sitemap: database not reachable ({type(e).__name__}); "
              f"writing static pages only")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        entry = f"  <url><loc>{loc}</loc>"
        if lastmod:
            entry += f"<lastmod>{lastmod}</lastmod>"
        parts.append(entry + "</url>")
    parts.append("</urlset>")
    (PUBLIC / "sitemap.xml").write_text("\n".join(parts) + "\n", encoding="utf-8")
    return note


def main() -> None:
    PUBLIC.mkdir(exist_ok=True)
    for src, dst in PAGES.items():
        shutil.copyfile(FRONTEND / src, PUBLIC / dst)
        print(f"  page   {src:16} -> public/{dst}")
    for a in ASSETS:
        if (FRONTEND / a).is_file():
            shutil.copyfile(FRONTEND / a, PUBLIC / a)
            print(f"  asset  {a}")
    shots_src, shots_dst = FRONTEND / "shots", PUBLIC / "shots"
    if shots_src.is_dir():
        shots_dst.mkdir(exist_ok=True)
        copied = 0
        for p in sorted(shots_src.glob("*")):
            if not p.is_file():
                continue
            try:
                shutil.copyfile(p, shots_dst / p.name)
                copied += 1
            except PermissionError:
                # A locked/foreign-owned copy already exists; the content is the
                # same, so warn and carry on rather than crashing the whole build.
                print(f"  ! shots/{p.name}: not writable, left in place")
        print(f"  shots  {copied} screenshot(s) copied")
    note = _write_sitemap()
    print(f"  sitemap public/sitemap.xml ({note})")
    print("Done. Now: firebase deploy --only hosting")


if __name__ == "__main__":
    main()
