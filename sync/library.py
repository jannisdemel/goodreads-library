"""
Scraper for Stadtbücherei Heidelberg (https://bibli-open.heidelberg.de).

Reworked: media-type awareness, multi-edition support, due dates,
reservations, Bestseller flag, broader candidate search.
"""

import json
import logging
import re
from collections import defaultdict
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

# Mediengruppe → (media_type, match_kind_default)
_MEDIENGRUPPE_MAP = {
    "schöne literatur": ("book",      "work"),
    "sachliteratur":    ("book",      "about"),
    "sachbuch":         ("book",      "about"),
    "virtuelle medien": ("ebook",     "work"),
    "e-medium":         ("ebook",     "work"),
    "tonträger":        ("audiobook", "work"),
    "hörbuch":          ("audiobook", "work"),
    "spielfilm":        ("film",      "unrelated"),
    "dvd":              ("film",      "unrelated"),
    "blu-ray":          ("film",      "unrelated"),
    "munzingerdaten":   (None,        None),
    "zeitschrift":      (None,        None),
    "spiel":            (None,        None),
}

# Minimum title-score threshold per media type to keep a candidate.
_TYPE_THRESHOLD = {
    "book":      0.3,
    "ebook":     0.3,
    "audiobook": 0.4,
    "film":      0.7,
    "other":     0.5,
}

_GLOBAL_MIN_SCORE = 0.25


def _best_word_similarity(a: str, b: str) -> float:
    """Max SequenceMatcher ratio between any long-word pair from two strings."""
    a_words = [w for w in _normalize(a).split() if len(w) >= 4]
    b_words = [w for w in _normalize(b).split() if len(w) >= 4]
    if not a_words or not b_words:
        return 0.0
    return max(SequenceMatcher(None, aw, bw).ratio()
               for aw in a_words for bw in b_words)

logger = logging.getLogger(__name__)


# ── Normalisation & scoring ────────────────────────────────────────────────

def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(the|a|an|der|die|das|ein|eine)\b", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def _clean_title(title: str) -> str:
    """Strip Goodreads series suffix '(Series, #N)' and subtitle after ':'."""
    title = re.sub(r"\s*\([^)]*#\d+[^)]*\)\s*$", "", title).strip()
    return title


def _author_search_term(author: str) -> str:
    """Remove initials like J.D. or R.F. so the OPAC returns full result sets."""
    tokens = [t for t in author.split() if not re.match(r"^([A-Z]\.)+$", t)]
    return " ".join(tokens).strip() or author


def _title_score(search_title: str, found_title: str) -> float:
    # Score once with the full title and once with just the part before ':'
    # (handles subtitles like "Bloodlands: Europe Between Hitler and Stalin")
    best = _score_pair(search_title, found_title)
    if ":" in search_title:
        main = search_title.split(":")[0].strip()
        best = max(best, _score_pair(main, found_title))
    return best


def _score_pair(search_title: str, found_title: str) -> float:
    a_words = _normalize(search_title).split()
    b_words = _normalize(found_title).split()
    if not a_words or not b_words:
        return 0.0

    a_set, b_set = set(a_words), set(b_words)
    word_score = len(a_set & b_set) / len(a_set | b_set)

    long_a = [w for w in a_words if len(w) >= 4]
    if long_a:
        best_per_word = [
            max((SequenceMatcher(None, aw, bw).ratio()
                 for bw in b_words if len(bw) >= 4), default=0.0)
            for aw in long_a
        ]
        word_char_score = sum(best_per_word) / len(best_per_word)
    else:
        word_char_score = 0.0

    return max(word_score, word_char_score * 0.6)


# ── OPAC HTML parsing ──────────────────────────────────────────────────────

def _parse_item_shallow(item) -> dict | None:
    """Parse one search-result div. Returns None for items to skip."""
    title_link = item.find("a", attrs={"aria-label": lambda x: x and "Detailanzeige" in x})
    if not title_link:
        return None
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

    # Media type from Mediengruppe
    itemtype_el = item.find("span", class_="itemtype")
    mediengruppe = ""
    if itemtype_el:
        mediengruppe = itemtype_el.get_text(strip=True).replace("Mediengruppe:", "").strip().lower()

    media_type, match_kind = _MEDIENGRUPPE_MAP.get(mediengruppe, ("book", "work"))
    if media_type is None:
        return None  # Munzingerdaten, magazines, etc. — skip

    return {
        "title": title,
        "author": author,
        "url": url,
        "mednr": mednr,
        "media_type": media_type,
        "match_kind": match_kind,
    }


def _run_search(session: requests.Session, query: str, max_pages: int = 1) -> list[dict]:
    items = []
    for page in range(1, max_pages + 1):
        extra = f"&page={page}" if page > 1 else ""
        params = (
            urlencode({"search": query, "top": "y", "focusModule": "searchmodule"}, quote_via=quote_plus)
            + extra
        )
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
                      if (p := _parse_item_shallow(div)) is not None]
        items.extend(page_items)
        if len(page_items) < 10:
            break
    return items


# ── Availability (Hauptstelle-only) ──────────────────────────────────────

def _parse_frist(s: str) -> str | None:
    """DD.MM.YYYY → YYYY-MM-DD, or None."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s.strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def _cell_value(cells, idx: int) -> str:
    if idx >= len(cells):
        return ""
    spans = cells[idx].find_all("span")
    val = spans[-1].get_text(strip=True) if spans else cells[idx].get_text(strip=True)
    for prefix in ("Zweigstelle:", "Standorte:", "Status:", "Frist:", "Barcode:", "Vorbestellungen:"):
        val = val.removeprefix(prefix).strip()
    # Collapse internal whitespace/newlines (shelf codes sometimes have \n)
    return re.sub(r"\s+", " ", val).strip()


def _get_availability(session: requests.Session, mednr: str) -> dict:
    """
    Returns physical Hauptstelle availability AND digital (eAusleihe) availability.
    Callers use `at_hauptstelle` for physical media and `at_eausleihe` for ebooks.
    """
    payload = json.dumps({
        "portalId": 0, "mednr": mednr, "culture": "de-DE",
        "branchFilter": "", "requestCopyData": True,
    })
    req_headers = {**HEADERS, "Content-Type": "application/json;charset=utf-8", "Referer": BASE_URL + "/"}
    try:
        resp = session.post(AVAIL_URL, data=payload, headers=req_headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("GetAvailability failed for mednr %s: %s", mednr, exc)
        return _empty_avail()

    copy_html = data.get("d", {}).get("CopyData", "")
    if not copy_html:
        return _empty_avail()

    soup = BeautifulSoup(copy_html, "lxml")

    hs_groups: dict[str, dict] = defaultdict(lambda: {"copies": 0, "due_dates": []})
    ea_available = False
    reservations = 0
    hs_available = False
    is_bestseller = False
    parsed_reservations = False

    for row in soup.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        branch = _cell_value(cells, 0)
        shelf  = _cell_value(cells, 1)
        status = _cell_value(cells, 2)
        frist  = _cell_value(cells, 3)
        res_raw = _cell_value(cells, 5)

        branch_l = branch.lower()

        # eAusleihe: track digital availability separately.
        # "Virtuelles Medium" means the digital item exists and is accessible.
        if branch_l == "eausleihe":
            if status.lower() in ("verfügbar", "virtuelles medium"):
                ea_available = True
            continue

        # Skip Bücherbus and other non-Hauptstelle
        if branch_l in _EXCLUDED_BRANCHES or branch_l != "hauptstelle":
            continue

        if "bestseller" in shelf.lower():
            is_bestseller = True

        hs_groups[shelf]["copies"] += 1
        due = _parse_frist(frist)
        if due:
            hs_groups[shelf]["due_dates"].append(due)

        if not parsed_reservations and res_raw.isdigit():
            reservations = int(res_raw)
            parsed_reservations = True

        if status.lower() == "verfügbar":
            hs_available = True

    locations = [
        {"shelf": shelf, "copies": g["copies"]}
        for shelf, g in hs_groups.items()
    ]
    all_due_dates = [d for g in hs_groups.values() for d in g["due_dates"]]
    due_date = min(all_due_dates) if all_due_dates and not hs_available else None

    return {
        "at_hauptstelle": bool(hs_groups),
        "available": hs_available,
        "at_eausleihe": ea_available,
        "is_bestseller": is_bestseller,
        "due_date": due_date,
        "reservations": reservations,
        "locations": locations,
    }


def _empty_avail() -> dict:
    return {"at_hauptstelle": False, "available": False, "at_eausleihe": False,
            "is_bestseller": False, "due_date": None, "reservations": 0, "locations": []}


# ── Language ──────────────────────────────────────────────────────────────

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


# ── Public API ────────────────────────────────────────────────────────────

def check_availability(books: list[dict]) -> list[dict]:
    """
    For each book dict {title, author, isbn}, return a result dict:
      {rows: list[library_row], search_url: str}
    where library_row has the richer schema (match_kind, media_type,
    is_bestseller, due_date, reservations, locations, ...).
    """
    return [
        _check_one(b.get("title", ""), b.get("author", ""), b.get("isbn", ""))
        for b in books
    ]


def _check_one(title: str, author: str, isbn: str = "") -> dict:
    session = requests.Session()
    session.headers.update(HEADERS)

    # Clean title: strip Goodreads series suffix for cleaner searches + scoring
    clean_title = _clean_title(title)
    author_term = _author_search_term(author)

    query = f"{clean_title} {author_term}".strip() if author_term else clean_title
    search_url = (
        SEARCH_URL + "?"
        + urlencode({"search": query, "top": "y", "focusModule": "searchmodule"}, quote_via=quote_plus)
    )

    # ── Build candidate pool ──────────────────────────────────────────────
    seen_mednr: set = set()
    candidates: list[dict] = []

    def add_results(items):
        for c in items:
            key = c.get("mednr") or c.get("url")
            if key and key not in seen_mednr:
                seen_mednr.add(key)
                candidates.append(c)

    if isbn:
        add_results(_run_search(session, isbn))

    # Title+author (cleaned): always run, 2 pages
    add_results(_run_search(session, query, max_pages=2))

    # Author-only with pagination: run if no confident match yet.
    # Use stripped author term (removes initials like J.D.) so the OPAC
    # returns full result sets instead of truncating at one page.
    if author_term:
        best_so_far = max((_title_score(clean_title, c["title"]) for c in candidates), default=0.0)
        if best_so_far < 0.5:
            add_results(_run_search(session, author_term, max_pages=5))

    # Title-only fallback: catches cases where the author spelling in the
    # OPAC differs strongly (e.g. "Dostoevsky" vs "Dostoevskij") and the
    # author search missed ebook editions of the correct title.
    has_ebook = any(c.get("media_type") == "ebook" and _title_score(clean_title, c["title"]) >= 0.3
                    for c in candidates)
    if not has_ebook:
        add_results(_run_search(session, clean_title, max_pages=2))

    if not candidates:
        return {"rows": [], "search_url": search_url}

    # ── Score & filter ────────────────────────────────────────────────────
    n_title_words = len(_normalize(clean_title).split())

    passing: list[tuple[dict, float]] = []
    for c in candidates:
        score = _title_score(clean_title, c["title"])

        # For short/generic titles (≤2 meaningful words), require author match
        # to prevent false positives like "Babel" (Borges) when searching Kuang.
        if n_title_words <= 2 and author_term and c.get("author"):
            if _best_word_similarity(author_term, c["author"]) < 0.5:
                score *= 0.4  # heavy penalty → drops below threshold

        threshold = _TYPE_THRESHOLD.get(c.get("media_type", "book"), 0.5)
        if score >= _GLOBAL_MIN_SCORE and score >= threshold:
            passing.append((c, score))

    if not passing:
        return {"rows": [], "search_url": search_url}

    # Sort best-first; process up to 15 candidates
    passing.sort(key=lambda x: -x[1])

    # ── Check availability for each passing candidate ─────────────────────
    rows: list[dict] = []
    seen_row_mednr: set = set()

    for c, _score in passing[:15]:
        if not c.get("mednr") or c["mednr"] in seen_row_mednr:
            continue

        avail = _get_availability(session, c["mednr"])
        is_ebook = c.get("media_type") == "ebook"

        # Physical media → must have Hauptstelle copy.
        # Ebooks → accept if available via eAusleihe (digital loan).
        if is_ebook:
            if not avail["at_eausleihe"]:
                continue
        else:
            if not avail["at_hauptstelle"]:
                continue

        seen_row_mednr.add(c["mednr"])
        lang = _get_language(session, c["url"]) if c.get("url") else ""

        if is_ebook:
            ebook_locations = ["eAusleihe"]
            ebook_available = avail["at_eausleihe"]
        else:
            ebook_locations = [loc["shelf"] for loc in avail["locations"]]
            ebook_available = avail["available"]

        rows.append({
            "match_kind":   c.get("match_kind", "work"),
            "media_type":   c.get("media_type", "book"),
            "is_bestseller": avail["is_bestseller"],
            "language":     lang,
            "status":       "available" if ebook_available else "unavailable",
            "due_date":     None if is_ebook else avail["due_date"],
            "reservations": avail["reservations"],
            "copies_total": sum(loc["copies"] for loc in avail["locations"]) if not is_ebook else 1,
            "locations":    ebook_locations,
            "library_title":  c["title"],
            "library_author": c["author"],
            "url":    c["url"],
            "mednr":  c["mednr"],
        })

    return {"rows": rows, "search_url": search_url}
