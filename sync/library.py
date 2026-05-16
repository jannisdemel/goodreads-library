"""
Scraper for Stadtbücherei Heidelberg (https://bibli-open.heidelberg.de).

Adapted from jannisdemel/FindBooks. Added ISBN-first search strategy.
"""

import json
import logging
import re
from difflib import SequenceMatcher
from urllib.parse import urlencode, quote_plus

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://bibli-open.heidelberg.de"
SEARCH_URL = BASE_URL + "/"
AVAIL_URL = (
    BASE_URL
    + "/DesktopModules/OCLC.OPEN.PL.DNN.SearchModule/SearchService.asmx/GetAvailability"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}

_EXCLUDED_BRANCHES = {"eausleihe", "bücherbus", "bucherbus"}

logger = logging.getLogger(__name__)


def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(the|a|an|der|die|das|ein|eine)\b", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def _title_score(search_title: str, found_title: str) -> float:
    a_words = _normalize(search_title).split()
    b_words = _normalize(found_title).split()
    if not a_words or not b_words:
        return 0.0

    # Word-level Jaccard (exact matches)
    a_set, b_set = set(a_words), set(b_words)
    word_score = len(a_set & b_set) / len(a_set | b_set)

    # Word-pair character similarity: each long search word is matched against
    # its best counterpart in the found title. Catches transliterations like
    # Karamazov/Karamasow or Dostoevsky/Dostoevskij without false-positives
    # from unrelated words happening to share character patterns.
    long_a = [w for w in a_words if len(w) >= 4]
    if long_a:
        best_per_word = [
            max((SequenceMatcher(None, aw, bw).ratio() for bw in b_words if len(bw) >= 4), default=0.0)
            for aw in long_a
        ]
        word_char_score = sum(best_per_word) / len(best_per_word)
    else:
        word_char_score = 0.0

    return max(word_score, word_char_score * 0.6)


def _get_availability(session: requests.Session, mednr: str) -> dict:
    payload = json.dumps({
        "portalId": 0,
        "mednr": mednr,
        "culture": "de-DE",
        "branchFilter": "",
        "requestCopyData": True,
    })
    headers = {**HEADERS, "Content-Type": "application/json;charset=utf-8", "Referer": BASE_URL + "/"}
    try:
        resp = session.post(AVAIL_URL, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("GetAvailability failed for mednr %s: %s", mednr, exc)
        return {"at_hauptstelle": False, "available": False, "locations": []}

    copy_html = data.get("d", {}).get("CopyData", "")
    if not copy_html:
        return {"at_hauptstelle": False, "available": False, "locations": []}

    soup = BeautifulSoup(copy_html, "lxml")
    locations = []
    available = False

    for row in soup.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        branch = cells[0].get_text(strip=True).removeprefix("Zweigstelle:").strip()
        shelf  = cells[1].get_text(strip=True).removeprefix("Standorte:").strip()
        status = cells[2].get_text(strip=True).removeprefix("Status:").strip()

        if branch.lower() in _EXCLUDED_BRANCHES:
            continue
        if branch.lower() != "hauptstelle":
            continue

        loc = shelf if shelf else branch
        if status:
            loc += f" ({status})"
        locations.append(loc)

        if status.lower() == "verfügbar":
            available = True

    return {
        "at_hauptstelle": len(locations) > 0,
        "available": available,
        "locations": locations,
    }


def _get_language(session: requests.Session, detail_url: str) -> str:
    try:
        resp = session.get(detail_url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Detail page fetch failed: %s", exc)
        return ""
    soup = BeautifulSoup(resp.text, "lxml")
    label = soup.find("span", string=lambda t: t and "Sprache" in t)
    if label:
        sib = label.find_next_sibling()
        if sib:
            return sib.get_text(strip=True)
    return ""


def _parse_item_shallow(item) -> dict:
    title_link = item.find("a", attrs={"aria-label": lambda x: x and "Detailanzeige" in x})
    if not title_link:
        return {}
    title = title_link.get_text(strip=True)
    url = title_link.get("href", "")
    if url and not url.startswith("http"):
        url = BASE_URL + url

    author_el = item.find(class_="author")
    author = author_el.get_text(strip=True) if author_el else ""
    if "," in author and author.count(",") == 1:
        parts = [p.strip() for p in author.split(",", 1)]
        author = f"{parts[1]} {parts[0]}"

    avail_region = item.find(class_="availRegion")
    mednr_input = (
        avail_region.find("input", attrs={"name": lambda x: x and x.endswith("mednr")})
        if avail_region else None
    )
    mednr = mednr_input["value"] if mednr_input else None
    return {"title": title, "author": author, "url": url, "mednr": mednr}


def _run_search(session: requests.Session, query: str, max_pages: int = 1) -> list[dict]:
    items = []
    for page in range(1, max_pages + 1):
        extra = f"&page={page}" if page > 1 else ""
        params = urlencode({"search": query, "top": "y", "focusModule": "searchmodule"}, quote_via=quote_plus) + extra
        try:
            resp = session.get(f"{SEARCH_URL}?{params}", timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Library search failed for %r page %d: %s", query, page, exc)
            break
        if "Keine Verbindung" in resp.text and "oclc-searchmodule-mediumview" not in resp.text:
            break
        soup = BeautifulSoup(resp.text, "lxml")
        page_items = [p for div in soup.find_all("div", class_="oclc-searchmodule-mediumview")
                      if (p := _parse_item_shallow(div))]
        items.extend(page_items)
        if len(page_items) < 10:
            break  # last page reached
    return items


def check_availability(books: list[dict]) -> list[dict]:
    results = []
    for book in books:
        title  = book.get("title", "")
        author = book.get("author", "")
        isbn   = book.get("isbn", "")
        results.extend(_check_one(title, author, isbn))
    return results


def _check_one(title: str, author: str, isbn: str = "") -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # Try ISBN first for precision, then fall back to title+author
    candidates = []
    if isbn:
        candidates = _run_search(session, isbn)
    if not candidates:
        query = f"{title} {author}".strip() if author else title
        candidates = _run_search(session, query)
    if not candidates and author:
        candidates = _run_search(session, author, max_pages=5)

    if not candidates:
        return [_not_found(title, author)]

    scored = sorted(
        [(c, _title_score(title, c["title"])) for c in candidates],
        key=lambda x: -x[1],
    )
    kept = [scored[0][0]]
    kept += [c for c, s in scored[1:] if s >= 0.3]

    seen_mednr: set = set()
    unique = []
    for c in kept:
        key = c.get("mednr") or c.get("url")
        if key and key not in seen_mednr:
            seen_mednr.add(key)
            unique.append(c)
        if len(unique) == 8:
            break

    by_lang: dict[str, dict] = {}

    for c in unique:
        if not c.get("mednr"):
            continue
        avail = _get_availability(session, c["mednr"])
        if not avail["at_hauptstelle"]:
            continue

        lang = _get_language(session, c["url"]) if c.get("url") else ""

        existing = by_lang.get(lang)
        if existing is None or (avail["available"] and not existing["available"]):
            by_lang[lang] = {
                "available": avail["available"],
                "locations": avail["locations"],
                "url": c["url"],
                "library_title": c["title"],
                "library_author": c["author"],
            }

    if not by_lang:
        return [_not_found(title, author, status="not_at_hauptstelle")]

    lang_order = {"Englisch": 0, "Deutsch": 1}
    rows = []
    for lang, info in sorted(by_lang.items(), key=lambda kv: lang_order.get(kv[0], 99)):
        rows.append({
            "title": title,
            "author": author,
            "language": lang,
            "status": "available" if info["available"] else "unavailable",
            "locations": info["locations"],
            "url": info["url"],
            "library_title": info["library_title"],
            "library_author": info["library_author"],
        })
    return rows


def _not_found(title: str, author: str, status: str = "not_found") -> dict:
    return {
        "title": title,
        "author": author,
        "language": "",
        "status": status,
        "locations": [],
        "url": "",
        "library_title": "",
        "library_author": "",
    }
