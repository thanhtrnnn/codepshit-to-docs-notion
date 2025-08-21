
# Notion Submissions Sync (HTML scraper)

Sync your submissions from a Codeforces‑like judge into a Notion database by scraping the submissions page.

## 1) Prepare Notion
- Create a Notion internal integration and copy the secret token.
- Share your target database (table) with the integration.
- Ensure the database has these properties (case‑sensitive):
  - **Problem** (title) — will store problem name
  - **Submission ID** (rich_text) — used for de‑duplication
  - **Submission time** (date) — optional
  - **Result** (select) — optional
  - **Problem URL** (url) — optional

## 2) Configure
### Pre-running
1) Create Google Cloud project & enable APIs
Open https://console.cloud.google.com and create or pick a project.
Open “APIs & Services → Library” and enable:
Google Docs API
Google Drive API
Billing is not required just to call these APIs (for ordinary usage).
2) Create a Service Account and key (recommended for automation)
Console → IAM & Admin → Service Accounts → Create Service Account.
Name it (e.g., codepshit-sync-sa). No special project role is required for the Doc operations if you share the Doc itself with the service account email, but you can add Viewer if desired.
After creating, go to the Service Account → Keys → Add Key → Create new key → JSON — download and keep it safe.
Security note: never commit the JSON key to source control. Add its path to .gitignore.

3) Share the target Google Doc with the service account
Open the Google Doc in the browser.
Click Share → invite the service account email you saw in the Service Account details (looks like: my-sa@project.iam.gserviceaccount.com). Give Editor permission.
How to get the Document ID:

It’s the long string in the document URL: https://docs.google.com/document/d/DOC_ID/edit

### Running
Copy `.env.example` to `.env` and fill:
- `NOTION_API_KEY`, `NOTION_DATABASE_ID`
- `LIST_URL` — your personal submissions URL (must be accessible when logged in)
- `COOKIE_STRING` — copy from your browser DevTools → Network → pick any request to the site → **Request Headers → Cookie**

If the table layout differs, tune the selectors:
- Default works for `<table><tr><td>` with columns: ID, time, problem, result.
- Otherwise use per‑cell CSS selectors (examples included in `.env.example`).

Pagination: enable and set `MAX_PAGES` to walk multiple list pages.

## 3) Install & Run
```bash
pip install -r requirements.txt
# then create .env file
python sync_submissions_to_notion.py
```

> Tip: run it on a schedule via cron/Task Scheduler/GitHub Actions to keep Notion updated.

## Notes
- De‑duplication: queries Notion by **Submission ID** before inserting.
- Time parsing: supports several common formats; adjust inside `TIME_FORMATS` if needed.
