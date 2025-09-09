
# Submissions sync and exports

This repository contains small automation tools that scrape submissions from a Code.ptit judge and can:
- Sync accepted Java submissions into a Google Doc table (`sync_submissions_to_docs.py`).
- Sync submissions into a Notion database (`sync_submissions_to_notion.py`).
- Export problem topic metadata from the public problem list pages (`export_problem_topics.py`).

Key changes since the original version:
- Google Docs integration (service account + Docs API).
- Selenium-based optional auto-login to obtain cookies automatically.
- Docs table sync now uses a column layout: Submission time | Topic | Problem | Result | Problem URL.
- Notion sync remains available and now supports Selenium auto-login too.

## Quick overview
- `sync_submissions_to_docs.py` — scrape submissions, filter AC + Java, append new rows to a specific table in a Google Doc (avoids duplicates by Problem URL).
- `sync_submissions_to_notion.py` — original Notion sync, now with optional auto-login.
- `export_problem_topics.py` — scrape problem list pages and save `problem_topics.json` for topic lookup.

## Prerequisites
- Python 3.8+
- Install runtime deps:

```powershell
pip install -r requirements.txt
pip install selenium google-api-python-client google-auth
```

- Selenium requires a browser driver (ChromeDriver/EdgeDriver):
  - Download ChromeDriver matching your Chrome version and put it on PATH or specify webdriver path.
  - Or install the `webdriver-manager` package and adapt the helper to use it.

## Google Docs setup (for `sync_submissions_to_docs.py`)
1. Create or pick a Google Cloud project.
2. Enable Google Docs API and Google Drive API.
3. Create a service account and generate a JSON key.
4. Share the target Google Doc with the service account email (Editor role).

Document ID: the long id in a Docs URL: `https://docs.google.com/document/d/DOC_ID/edit`

## Selenium auto-login (optional)
The scripts can optionally use Selenium to auto-login and extract cookies. Configure these variables in `.env` (AUTO_LOGIN=true). If Selenium auto-login fails, the scripts fall back to `COOKIE_STRING`.

Notes:
- Headless mode is used by default. If login requires a visible browser or captcha, headless may fail.
- Keep credentials secure and avoid committing them.

## .env variables (important ones)
Fill a `.env` in the repository root. The most relevant variables:

- Authentication / site
  - LIST_URL — submissions page (e.g. https://code.ptit.edu.vn/student/history)
  - AUTO_LOGIN — true/false (use Selenium to auto-login)
  - LOGIN_URL — login page URL
  - LOGIN_USERNAME / LOGIN_PASSWORD — credentials for auto-login
  - USERNAME_SELECTOR / PASSWORD_SELECTOR / SUBMIT_SELECTOR — CSS selectors for the login form
  - COOKIE_STRING — manual cookie string fallback (copy from browser DevTools)

- Google Docs
  - ENABLE_DOCS — true/false
  - GOOGLE_APPLICATION_CREDENTIALS — path to service account JSON
  - GOOGLE_DOC_ID — target document id
  - DOC_SECTION — section path (e.g., "Java programming language > Bài tập > Bài tập trên Code.ptit") used to find the correct table
  - DRY_RUN — true/false (preview mode; no writes)

- Notion (if using Notion sync)
  - NOTION_API_KEY
  - NOTION_DATABASE_ID

- Scraper selectors (optional tuning)
  - ROW_SELECTOR, COL_INDEXES, PROBLEM_LINK_SELECTOR, etc.

Example `.env` (already provided in repo):

```
LIST_URL=https://code.ptit.edu.vn/student/history
AUTO_LOGIN=true
LOGIN_URL=https://code.ptit.edu.vn/login
LOGIN_USERNAME=B23DCCN026
LOGIN_PASSWORD=supersecret
USERNAME_SELECTOR=#login__user
PASSWORD_SELECTOR=#login__pw
SUBMIT_SELECTOR=button[type='submit']
COOKIE_STRING=

ENABLE_DOCS=true
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
GOOGLE_DOC_ID=1xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DOC_SECTION='Java programming language > Bài tập > Bài tập trên Code.ptit'
DRY_RUN=true
```

## How the Docs table is expected
The target Google Doc should contain a table with a header row matching (order matters for insertion):

- Submission time | Topic | Problem | Result | Problem URL

The sync will append rows below the table and fill cells. The code attempts to find the correct table by locating tables under headings specified by `DOC_SECTION` (supports H1/H2/H3 style matching).

## Run the scripts

Preview (dry-run) with Selenium auto-login (PowerShell):

```powershell
python .\sync_submissions_to_docs.py
```

To actually write to the Doc, set `ENABLE_DOCS=true` and `DRY_RUN=false` in `.env`.

Run Notion sync (preview mode will still honor DRY_RUN logic inside the script):

```powershell
python .\sync_submissions_to_notion.py
```

Export problem topics (creates/updates `problem_topics.json`):

```powershell
python .\export_problem_topics.py
```

## Troubleshooting
- Selenium import error: install `selenium` and ensure ChromeDriver is available on PATH.
- Auto-login fails: try running without `--headless`, verify selectors, or copy `COOKIE_STRING` manually.
- Docs API errors: verify service account JSON path and that the Doc is shared with the service account email.

## Security
- Never commit `.env` or service account JSON keys to source control.
- Use `DRY_RUN=true` while testing to avoid accidental writes.

## Next steps / improvements
- Batch-create rows (to reduce API calls) — currently script inserts rows then fills cells.
- Support richer formatting (hyperlinks) when inserting Problem URL into Docs.
- Add automated tests for selector parsing and table selection logic.
