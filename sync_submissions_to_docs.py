#!/usr/bin/env python3
"""
Sync submissions from CodeP**** (HTML table) into a Notion database.

Features
- Auth via browser cookie string (copy from devtools -> "cookie" header).
- Scrapes a submissions table (row-wise) using CSS selectors.
- De-duplicates by "Submission ID" using Notion query.
- Validate data to insert to database (correct Java compiler, AC result only)
- Optional pagination.
- Can be run on a schedule (cron / GitHub Actions).
- Idempotent: safe to re-run.

Requirements
- NOTION_DATABASE must have these properties (case-sensitive):
  - Problem (title)
  - Submission ID (rich_text)
  - Submission time (date)      # optional but recommended
  - Result (select)             # optional
  - Problem URL (url)           # optional

Usage
1) Create a Notion Internal Integration, share the target database with it.
2) Fill .env (see .env.example) or set env vars directly.
3) pip install -r requirements.txt
4) python sync_submissions_to_notion.py
"""

import os, time, re, json, urllib.parse, requests
from dotenv import load_dotenv
from datetime import datetime
from bs4 import BeautifulSoup

# Optional Google Docs integration
from google.oauth2 import service_account
from googleapiclient.discovery import build
from typing import Optional, List, Dict

# ----------------------
# Config via env vars
# ----------------------
load_dotenv()

# Site config
LIST_URL = os.getenv("LIST_URL", "").strip()
COOKIE_STRING = os.getenv("COOKIE_STRING", "").strip()  # "k1=v1; k2=v2"
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36").strip()

# Scraper selectors
# By default, we assume a <table> with <tr> for rows and <td> columns in order: ID, time, problem, result.
ROW_SELECTOR = os.getenv("ROW_SELECTOR", "table tr").strip()
# If you want to pick exact <td> indexes (0-based), set COL_INDEXES="0,1,2,3"
COL_INDEXES = os.getenv("COL_INDEXES", "0,1,2,3,4,5,6").strip()

# Alternative: use CSS selectors relative to each row; leave blank to use COL_INDEXES
ID_CELL_SELECTOR = os.getenv("ID_CELL_SELECTOR", "").strip()
TIME_CELL_SELECTOR = os.getenv("TIME_CELL_SELECTOR", "").strip()
PROBLEM_CELL_SELECTOR = os.getenv("PROBLEM_CELL_SELECTOR", "").strip()
RESULT_CELL_SELECTOR = os.getenv("RESULT_CELL_SELECTOR", "").strip()
PROBLEM_LINK_SELECTOR = os.getenv("PROBLEM_LINK_SELECTOR", "a").strip()  # relative to the problem cell

# Pagination (optional)
ENABLE_PAGINATION = os.getenv("ENABLE_PAGINATION", "false").lower() in ("1","true","yes","y")
PAGE_PARAM = os.getenv("PAGE_PARAM", "page").strip()  # e.g., "page"
MAX_PAGES = int(os.getenv("MAX_PAGES", "1"))

# Safety / performance
TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
]

# ----------------------
# Google Docs config
# ----------------------
load_dotenv(override=True)
GOOGLE_DOC_ID = os.getenv("GOOGLE_DOC_ID", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
DOC_SECTION = os.getenv("DOC_SECTION", "CHUONG 2 > Bai tap > codeptit").strip()
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "false").lower() in ("1", "true", "yes", "y")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes", "y")

DOC_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


def get_docs_service():
    if not GOOGLE_APPLICATION_CREDENTIALS:
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS is required for Google Docs integration")
    creds = service_account.Credentials.from_service_account_file(GOOGLE_APPLICATION_CREDENTIALS, scopes=DOC_SCOPES)
    return build("docs", "v1", credentials=creds)


def extract_paragraph_text(el: Dict) -> str:
    if not el or "paragraph" not in el:
        return ""
    parts = []
    for pe in el["paragraph"].get("elements", []):
        txt_run = pe.get("textRun")
        if txt_run and txt_run.get("content"):
            parts.append(txt_run.get("content"))
    return "".join(parts).strip()


## MODIFICATION: New helper function to extract all text from a table cell.
def extract_cell_text(cell: Dict) -> str:
    """Extracts all text from a single table cell."""
    if not cell or "content" not in cell:
        return ""
    
    cell_parts = []
    for content_element in cell.get("content", []):
        cell_parts.append(extract_paragraph_text(content_element))
        
    return "".join(cell_parts).strip()


def find_tables_with_context(doc: Dict) -> List[Dict]:
    out = []
    content = doc.get("body", {}).get("content", [])
    for i, el in enumerate(content):
        if "table" in el:
            ctx = ""
            for j in range(i - 1, -1, -1):
                text = extract_paragraph_text(content[j])
                if text:
                    ctx = text
                    break
            out.append({"element": el, "index": i, "context": ctx})
    return out


def choose_table_by_section(doc: Dict, section_name: str) -> Optional[Dict]:
    content = doc.get("body", {}).get("content", [])
    tables = []
    for i, el in enumerate(content):
        if "table" in el:
            h1 = h2 = h3 = ""
            for j in range(i - 1, max(i - 100, -1), -1):
                c = content[j]
                if "paragraph" not in c:
                    continue
                style = c["paragraph"].get("paragraphStyle", {})
                named = style.get("namedStyleType", "")
                txt = extract_paragraph_text(c)
                if not txt:
                    continue
                if named == "HEADING_3" and not h3:
                    h3 = txt
                elif named == "HEADING_2" and not h2:
                    h2 = txt
                elif named == "HEADING_1" and not h1:
                    h1 = txt
                if h1 and h2 and h3:
                    break
            ctx = " ".join(x for x in (h1, h2, h3) if x)
            tables.append({"element": el, "index": i, "h1": h1, "h2": h2, "h3": h3, "context": ctx})

    def normalize(s: str) -> str:
        return re.sub(r"[^0-9a-z]+", " ", (s or "").lower()).strip()

    parts = [p.strip() for p in re.split(r">|\||->|/", (section_name or "")) if p.strip()]
    parts = [normalize(p) for p in parts]

    for t in tables:
        nh1 = normalize(t.get("h1", ""))
        nh2 = normalize(t.get("h2", ""))
        nh3 = normalize(t.get("h3", ""))
        if len(parts) == 0:
            continue
        if len(parts) == 1:
            if parts[0] and parts[0] in nh1:
                return t
        elif len(parts) == 2:
            if parts[0] and parts[0] in nh1 and parts[1] and parts[1] in nh2:
                return t
        else:
            if parts[0] and parts[0] in nh1 and parts[1] and parts[1] in nh2 and parts[2] and parts[2] in nh3:
                return t

    m = re.search(r"\d+", section_name or "")
    if m:
        num = m.group(0)
        for t in tables:
            if re.search(rf"\b{re.escape(num)}\b", t.get("h1", "")):
                return t

    if tables and DRY_RUN:
        print("[docs debug] no exact heading match; available table headings:")
        for t in tables:
            print(f" - h1={t.get('h1')!r}, h2={t.get('h2')!r}, h3={t.get('h3')!r}")

    return tables[0] if tables else None


## MODIFICATION: New function to read the Doc and get existing submission IDs.
def get_existing_submission_ids(docs_service, doc_id: str, section: str) -> set:
    """Reads a table in a Google Doc and returns a set of Submission IDs from the first column."""
    if not doc_id:
        return set()
    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        table_info = choose_table_by_section(doc, section)

        if not table_info:
            print(f"[docs warning] Could not find table for section '{section}'. Assuming no existing submissions.")
            return set()

        table = table_info.get("element", {}).get("table", {})
        table_rows = table.get("tableRows", [])
        
        existing_ids = set()
        # Start from index 1 to skip a potential header row
        for row in table_rows[1:]:
            cells = row.get("tableCells", [])
            if not cells:
                continue
            
            # Submission ID is assumed to be in the first cell
            first_cell = cells[0]
            submission_id = extract_cell_text(first_cell)
            if submission_id.isdigit():
                existing_ids.add(submission_id)
        return existing_ids
    except Exception as e:
        print(f"[docs error] Failed to get existing submission IDs: {e}")
        return set()


def append_rows_and_fill_docs(doc_id: str, docs_service, table_element: Dict, rows_data: List[List[str]]):
    table = table_element.get("element", {}).get("table")
    if not table:
        print("[docs] table element missing")
        return False

    start_index = table_element.get("element", {}).get("startIndex")
    if start_index is None:
        print("[docs] table startIndex not available")
        return False

    old_row_count = len(table.get("tableRows", []))
    num_to_add = len(rows_data)
    if num_to_add == 0: 
        return True

    requests_payload = []
    for _ in range(num_to_add):
        requests_payload.append({
            "insertTableRow": {
                "tableCellLocation": {"tableStartLocation": {"index": start_index}, "rowIndex": old_row_count - 1},
                "insertBelow": True,
            }
        })

    docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()
    time.sleep(0.34)

    doc2 = docs_service.documents().get(documentId=doc_id).execute()
    table2 = choose_table_by_section(doc2, DOC_SECTION)
    if not table2:
        print("[docs] table disappeared after insert")
        return False

    tbl = table2.get("element", {}).get("table")
    new_row_count = len(tbl.get("tableRows", []))
    start_fill = new_row_count - num_to_add

    insert_requests = []
    for r_idx in range(start_fill, new_row_count):
        row = tbl["tableRows"][r_idx]
        cells = row.get("tableCells", [])
        for c_idx in range(min(len(cells), len(rows_data[0]))):
            cell = cells[c_idx]
            insert_index = None
            contents = cell.get("content") or []
            for content_entry in contents:
                if "paragraph" in content_entry:
                    start_i = content_entry.get("startIndex")
                    end_i = content_entry.get("endIndex")
                    if isinstance(end_i, int) and isinstance(start_i, int) and end_i > start_i:
                        insert_index = end_i - 1
                    elif isinstance(start_i, int):
                        insert_index = start_i + 1
                    break

            if insert_index is None:
                cell_start = cell.get("startIndex")
                if isinstance(cell_start, int):
                    insert_index = cell_start + 1

            if insert_index is None:
                print(f"[docs debug] skipping cell r={r_idx} c={c_idx}: no insertion index")
                continue

            text_to_insert = rows_data[r_idx - start_fill][c_idx]
            if not text_to_insert:
                continue

            insert_requests.append({"insertText": {"location": {"index": insert_index}, "text": text_to_insert}})

    if insert_requests:
        insert_requests.sort(key=lambda r: r["insertText"]["location"]["index"], reverse=True)
        for i in range(0, len(insert_requests), 50):
            chunk = insert_requests[i : i + 50]
            docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": chunk}).execute()
        print(f"[docs] Filled {len(rows_data)} new cells")
    return True



def die(msg):
    raise SystemExit(f"[fatal] {msg}")


def parse_cookie_string(s: str):
    jar = requests.cookies.RequestsCookieJar()
    if not s:
        return jar
    parts = [p.strip() for p in s.split(";") if p.strip()]
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            jar.set(k.strip(), v.strip())
    return jar


def pick_text(el):
    if not el:
        return ""
    a = el.find("a")
    return (a.get_text(" ", strip=True) if a else el.get_text(" ", strip=True)).strip()


def try_parse_time(s):
    s = s.strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", s)
    if m:
        try:
            return datetime.fromisoformat(m.group(0).replace(" ", "T"))
        except Exception:
            pass
    return None


def build_session():
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    sess.cookies.update(parse_cookie_string(COOKIE_STRING))
    return sess


def fetch_page(session: requests.Session, url: str):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def make_page_url(base_url, page_index):
    parts = list(urllib.parse.urlparse(base_url))
    qs = urllib.parse.parse_qs(parts[4])
    qs[PAGE_PARAM] = [str(page_index)]
    parts[4] = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parts)


def parse_rows(html: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(ROW_SELECTOR)
    out = []
    for row in rows:
        if row.find_all(["th"]):
            continue

        if ID_CELL_SELECTOR or TIME_CELL_SELECTOR or PROBLEM_CELL_SELECTOR or RESULT_CELL_SELECTOR:
            id_cell = row.select_one(ID_CELL_SELECTOR) if ID_CELL_SELECTOR else None
            time_cell = row.select_one(TIME_CELL_SELECTOR) if TIME_CELL_SELECTOR else None
            prob_cell = row.select_one(PROBLEM_CELL_SELECTOR) if PROBLEM_CELL_SELECTOR else None
            res_cell = row.select_one(RESULT_CELL_SELECTOR) if RESULT_CELL_SELECTOR else None
        else:
            tds = row.find_all("td")
            if not tds:
                continue
            try:
                idx = [int(x.strip()) for x in COL_INDEXES.split(",")]
            except Exception:
                idx = [0,1,2,3,4,5,6]
            if max(idx) >= len(tds):
                continue
            id_cell, time_cell, prob_cell, res_cell, compiler_cell = (tds[idx[0]], tds[idx[1]], tds[idx[2]], tds[idx[3]], tds[idx[6]])

        sid = pick_text(id_cell)
        stime_text = pick_text(time_cell)
        prob_text = pick_text(prob_cell)
        res_text = pick_text(res_cell)
        compiler_text = pick_text(compiler_cell)

        prob_url = None
        if prob_cell:
            a = prob_cell.select_one(PROBLEM_LINK_SELECTOR) or prob_cell.find("a")
            if a and a.has_attr("href"):
                prob_url = urllib.parse.urljoin(LIST_URL, a["href"])

        if not sid and not prob_text:
            continue

        out.append({
            "id": sid,
            "time_text": stime_text,
            "problem": prob_text,
            "result": res_text,
            "problem_url": prob_url,
            "compiler": compiler_text,
        })
    return out


## MODIFICATION: The main sync function is rewritten to support de-duplication.
def sync():
    if not LIST_URL:
        die("LIST_URL is empty")
    session = build_session()
    docs = None
    existing_ids = set()

    if ENABLE_DOCS:
        if not GOOGLE_DOC_ID:
            die("ENABLE_DOCS is true but GOOGLE_DOC_ID is empty")
        docs = get_docs_service()
        print("[docs] Fetching existing submission IDs to prevent duplicates...")
        existing_ids = get_existing_submission_ids(docs, GOOGLE_DOC_ID, DOC_SECTION)
        print(f"[docs] Found {len(existing_ids)} existing submissions in the document.")

    new_rows_to_add = []
    pages = [LIST_URL]

    if ENABLE_PAGINATION and MAX_PAGES > 1:
        pages = [make_page_url(LIST_URL, i) for i in range(1, MAX_PAGES + 1)]

    for url in pages:
        print(f"[fetch] {url}")
        html = fetch_page(session, url)
        rows = parse_rows(html)
        print(f"[parse] found {len(rows)} rows")
        for item in rows:
            sid = (item.get("id") or "").strip()
            if not sid:
                continue
            
            # The core logic change: skip if this ID has already been synced.
            if sid in existing_ids:
                continue

            res = (item.get("result") or "").strip()
            isCompilerJava = (item.get("compiler", "").strip().lower() == "java")

            if res == "AC" and isCompilerJava:
                dt = try_parse_time(item.get("time_text") or "")
                time_text = dt.strftime("%d-%m-%Y") if dt else (item.get("time_text") or "")

                row_data = [
                    sid,
                    time_text,
                    item.get("problem"),
                    item.get("result"),
                    item.get("problem_url") or "",
                    item.get("compiler") or "",
                ]
                
                # Add the new row to a temporary list.
                new_rows_to_add.append(row_data)
                # Add the ID to our set to avoid duplicates if it appears again in the same run.
                existing_ids.add(sid)

    print(f"[sync] Found {len(new_rows_to_add)} new submissions to add.")

    # After checking all pages, add all new rows in a single batch operation.
    if new_rows_to_add:
        # Reverse the list so the newest submissions are added first.
        new_rows_to_add.reverse()
        if DRY_RUN or not ENABLE_DOCS:
            for row in new_rows_to_add:
                print("[dry-run] would append:", row)
        else:
            try:
                print(f"[docs] Appending {len(new_rows_to_add)} rows to the table...")
                doc = docs.documents().get(documentId=GOOGLE_DOC_ID).execute()
                table_info = choose_table_by_section(doc, DOC_SECTION)
                if table_info:
                    append_rows_and_fill_docs(GOOGLE_DOC_ID, docs, table_info, new_rows_to_add)
                else:
                    print(f"[docs error] Could not find the target table section '{DOC_SECTION}'.")
            except Exception as e:
                print(f"[docs error] failed to append rows: {e}")

    print(f"[done] Processed {len(new_rows_to_add)} new submissions.")


if __name__ == "__main__":
    sync()