
# Submissions sync and exports

This repository contains small automation tools that scrape submissions from a Code.ptit judge and can:
- Sync accepted Java submissions into a Google Doc table (`sync_submissions_to_docs.py`).
- Sync submissions into a Notion database (`sync_submissions_to_notion.py`).
- Export problem topic metadata from the public problem list pages (`export_problem_topics.py`).

## Quick overview
- `sync_submissions_to_docs.py` — two-step workflow: (1) with DRY_RUN=true it scrapes AC+Java submissions and populates a local JSON batch file; (2) with DRY_RUN=false it rebuilds the target Google Docs table from that JSON (no scraping in the second step). This prevents partial state and gives you an authoritative batch-file-driven rebuild.
- `sync_submissions_to_notion.py` — original Notion sync, now with optional auto-login.
- `export_problem_topics.py` — scrape problem list pages and save `problem_topics.json` for topic lookup.

## Prerequisites
- Python 3.8+
- Install runtime deps:

```powershell
pip install -r requirements.txt
```

## Google Docs setup
1. Create or pick a Google Cloud project.
2. Enable Google Docs API and Google Drive API.
3. Create a service account and generate a JSON key.
4. Share the target Google Doc with the service account email (Editor role).

Document ID: the long id in a Docs URL: `https://docs.google.com/document/d/DOC_ID/edit`

## Selenium auto-login
The scripts can optionally use Selenium to auto-login and extract cookies. Configure these variables in `.env` (AUTO_LOGIN=true). If Selenium auto-login fails, the scripts fall back to `COOKIE_STRING`.

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
  - DOC_SECTION — the heading text used to find the correct table. The script matches this text against any single heading near a table (H1, H2 or H3). 
    ```
    It requires the heading text to be unique in the document, the code tries to perform an EXACT MATCH. 
    
    Use separators like `>` or `|` to point at a specific one in the heading hierachy if you prefer.
    ```
  - BATCH_FILE — path to the JSON batch file used to persist scraped rows (default: `batch_result.json`).
  - DOC_SYNC_MODE — optional (legacy); current workflow is driven by `DRY_RUN`.
  - DRY_RUN — true/false. When true the script scrapes and writes to `BATCH_FILE` only. When false the script rebuilds the Docs table from `BATCH_FILE` and will write to the doc if `ENABLE_DOCS=true`.

- Notion (if using Notion sync)
  - NOTION_API_KEY
  - NOTION_DATABASE_ID

- Scraper selectors (optional tuning)
  - ROW_SELECTOR, COL_INDEXES, PROBLEM_LINK_SELECTOR, etc.

(More details on `.docs.env.example` and `.notion.env.example`)

## How the Docs table is expected
The target Google Doc should contain a table with a header row matching (order matters for insertion):

```
Date | Topic | No | Problem | Result
```

## How the Notion database is expected
- NOTION_DATABASE must have these properties (case-sensitive):
  - Problem (title, DEFAULT field)
  - No (rich_text)
  - Topic (select)
  - Submission ID (rich_text)
  - Submission time (date)
  - Result (select)
  - Problem URL (url)

The sync will append rows below the table and fill cells. The code attempts to find the correct table by locating tables under headings specified by `DOC_SECTION` (supports H1/H2/H3 style matching).

## Run the scripts

The Docs sync is a two-step process (recommended):

1) Populate the JSON batch (scrape):

```powershell
$env:DRY_RUN='true'
python .\sync_submissions_to_docs.py
```

This will scrape the configured `LIST_URL` for AC + Java submissions and write/update `BATCH_FILE` (default `batch_result.json`). No Docs writes occur in this mode.

2) Rebuild the Docs table from the JSON (apply to Google Docs):

```powershell
$env:DRY_RUN='false'
$env:ENABLE_DOCS='true'
python .\sync_submissions_to_docs.py
```

This will load the `BATCH_FILE`, locate the table under the `DOC_SECTION` heading, clear existing data rows, then re-insert rows from the JSON in descending date/number order. If the `DOC_SECTION` heading cannot be found (or is ambiguous), the script will error and print available nearby headings in dry-run mode so you can adjust `DOC_SECTION`.

Run Notion sync (preview mode will still honor DRY_RUN logic inside the script):

```powershell
python .\sync_submissions_to_notion.py
```

## Troubleshooting
- Selenium import error: install `selenium` and ensure ChromeDriver is available on PATH.
- Auto-login fails: try running without `--headless`, verify selectors, or copy `COOKIE_STRING` manually.
- Docs API errors: verify service account JSON path and that the Doc is shared with the service account email.
 - Docs API errors: verify service account JSON path and that the Doc is shared with the service account email.
 - Table not found: ensure `DOC_SECTION` exactly matches a heading in the document (matching is accent-aware and compares text against nearby H1/H2/H3 headings).