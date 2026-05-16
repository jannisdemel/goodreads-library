# Goodreads → Stadtbücherei Heidelberg

Syncs your Goodreads "want to read" shelf and checks whether each book is
available at the Stadtbücherei Heidelberg (Hauptstelle).

Live at: https://jannisdemel.github.io/goodreads-library/

## Setup

### 1. Configure your Goodreads user ID

Edit `config.json` and set `goodreads_user_id` to the numeric ID from your
Goodreads profile URL (`goodreads.com/user/show/<ID>-yourname`).

Make sure your profile and the `to-read` shelf are **public** in Goodreads
settings. Verify: `https://www.goodreads.com/review/list_rss/<YOUR_ID>?shelf=to-read`

### 2. Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python sync/run.py          # writes docs/data/books.json
cd docs && python -m http.server 8080   # open localhost:8080
```

### 3. Deploy to GitHub Pages

1. Push this repo to GitHub.
2. Repo Settings → Pages → Deploy from branch `main`, folder `/docs`.
3. Go to Actions → "Sync Goodreads → Library" → Run workflow (first run).

The workflow runs daily at 05:00 UTC and on every manual trigger.

## How it works

- **Goodreads**: reads your shelf via the public RSS feed (no API key needed).
- **Library**: scrapes `bibli-open.heidelberg.de` (OCLC OPEN) — same approach
  as [FindBooks](https://github.com/jannisdemel/FindBooks). Only physical copies
  at Hauptstelle are shown; eAusleihe and Bücherbus are filtered out.
- **GitHub Actions** runs the sync, commits `docs/data/books.json`, and Pages
  serves the static site.
