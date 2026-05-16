"""
Fetch books from a public Goodreads shelf via the RSS feed.

URL: https://www.goodreads.com/review/list_rss/<user_id>?shelf=<shelf>&per_page=100
"""

import logging
import re
import feedparser

logger = logging.getLogger(__name__)

_RSS = "https://www.goodreads.com/review/list_rss/{user_id}?shelf={shelf}&per_page=100&page={page}"


def fetch_shelf(user_id: str, shelf: str = "to-read") -> list[dict]:
    books = []
    page = 1
    while True:
        url = _RSS.format(user_id=user_id, shelf=shelf, page=page)
        feed = feedparser.parse(url)
        entries = feed.get("entries", [])
        if not entries:
            break
        for entry in entries:
            book = _parse_entry(entry)
            if book:
                books.append(book)
        if len(entries) < 100:
            break
        page += 1
    logger.info("Fetched %d books from Goodreads shelf '%s'", len(books), shelf)
    return books


def _parse_entry(entry: dict) -> dict | None:
    title = entry.get("title", "").strip()
    if not title:
        return None

    author = entry.get("author_name", "") or entry.get("author", "")
    author = author.strip()

    isbn = ""
    isbn13 = ""
    cover_url = ""
    goodreads_url = entry.get("link", "")

    # Goodreads puts structured book data in the summary/content HTML
    summary = entry.get("summary", "")
    if summary:
        m = re.search(r'isbn["\s:]+([0-9X]{10})', summary, re.IGNORECASE)
        if m:
            isbn = m.group(1)
        m13 = re.search(r'isbn13["\s:]+([0-9]{13})', summary, re.IGNORECASE)
        if m13:
            isbn13 = m13.group(1)
        img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
        if img:
            cover_url = img.group(1)

    # feedparser also exposes these directly on some setups
    cover_url = cover_url or entry.get("book_image_url", "") or entry.get("book_small_image_url", "")

    # Strip Goodreads thumbnail size suffix (e.g. ._SY75_) to get full-res image
    cover_url = re.sub(r'\._S[XY]\d+_(?=\.[a-z]+$)', '', cover_url)

    return {
        "title": title,
        "author": author,
        "isbn": isbn13 or isbn,
        "goodreads_url": goodreads_url,
        "cover_url": cover_url,
    }
