"""
Entry point: fetch Goodreads shelf → check library → write docs/data/books.json
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from goodreads import fetch_shelf
from library import check_availability

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.json"
OUT_PATH = ROOT / "docs" / "data" / "books.json"


def main():
    config = json.loads(CONFIG_PATH.read_text())
    user_id = config["goodreads_user_id"]
    shelf = config.get("shelf", "to-read")

    if user_id == "YOUR_USER_ID_HERE":
        logger.error("Set goodreads_user_id in config.json first.")
        sys.exit(1)

    logger.info("Fetching shelf '%s' for user %s ...", shelf, user_id)
    gr_books = fetch_shelf(user_id, shelf)
    logger.info("Got %d books from Goodreads", len(gr_books))

    # Build input list for library checker
    lib_input = [{"title": b["title"], "author": b["author"], "isbn": b["isbn"]} for b in gr_books]
    logger.info("Checking library availability...")
    lib_results = check_availability(lib_input)

    # Group library results by (title, author)
    lib_by_book: dict[tuple, list] = {}
    for row in lib_results:
        key = (row["title"], row["author"])
        lib_by_book.setdefault(key, []).append({
            "language": row["language"],
            "status": row["status"],
            "locations": row["locations"],
            "url": row["url"],
            "library_title": row["library_title"],
            "library_author": row.get("library_author", ""),
        })

    books_out = []
    for b in gr_books:
        key = (b["title"], b["author"])
        library_rows = lib_by_book.get(key, [{"language": "", "status": "not_found", "locations": [], "url": "", "library_title": "", "library_author": ""}])
        books_out.append({
            "title": b["title"],
            "author": b["author"],
            "isbn": b["isbn"],
            "goodreads_url": b["goodreads_url"],
            "cover_url": b["cover_url"],
            "library": library_rows,
        })

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shelf": shelf,
        "books": books_out,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    logger.info("Wrote %s", OUT_PATH)


if __name__ == "__main__":
    main()
