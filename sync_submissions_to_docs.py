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
from selenium import webdriver
from selenium.webdriver.common.by import By
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

LIST_URL = os.getenv("LIST_URL", "").strip()
COOKIE_STRING = os.getenv("COOKIE_STRING", "").strip()  # "k1=v1; k2=v2"
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36").strip()
# Selenium login variables
AUTO_LOGIN = os.getenv("AUTO_LOGIN", "false").lower() in ("1", "true", "yes", "y")
LOGIN_URL = os.getenv("LOGIN_URL", "").strip()
LOGIN_USERNAME = os.getenv("LOGIN_USERNAME", "").strip()
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "").strip()
USERNAME_SELECTOR = os.getenv("USERNAME_SELECTOR", "").strip()
PASSWORD_SELECTOR = os.getenv("PASSWORD_SELECTOR", "").strip()
SUBMIT_SELECTOR = os.getenv("SUBMIT_SELECTOR", "").strip()

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
BATCH_FILE = os.getenv("BATCH_FILE", "batch_result.json").strip()

DOC_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


def get_docs_service():
    if not GOOGLE_APPLICATION_CREDENTIALS:
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS is required for Google Docs integration")
    creds = service_account.Credentials.from_service_account_file(GOOGLE_APPLICATION_CREDENTIALS, scopes=DOC_SCOPES)
    return build("docs", "v1", credentials=creds)


# ----------------------
# Batch helpers (JSON store keyed by numeric problem "number")
# ----------------------
def load_batch() -> Dict[str, Dict]:
    try:
        if not os.path.exists(BATCH_FILE):
            return {}
        with open(BATCH_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_batch(data: Dict[str, Dict]):
    try:
        with open(BATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[batch] saved {len(data)} entries -> {BATCH_FILE}")
    except Exception as e:
        print(f"[batch warn] failed saving {BATCH_FILE}: {e}")


def problem_number_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        last = url.rstrip('/').split('/')[-1]
        m = re.search(r"(\d+)$", last)
        if not m:
            return None
        return str(int(m.group(1)))
    except Exception:
        return None


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
    """Locate the table whose heading path exactly matches DOC_SECTION.

    DOC_SECTION is split by >, |, ->, / and compared (case-insensitive, trimmed) against
    the surrounding Heading1/2/3 text. We require an exact match for available levels;
    no fuzzy fallback to avoid picking a lookalike table in a different section.
    """

    def normalize(s: str) -> str:
        if not s:
            return ""
        s_norm = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in s.casefold())
        return re.sub(r"\s+", " ", s_norm).strip()

    def split_section_path(path: str) -> List[str]:
        if not path:
            return []
        parts_raw = re.split(r">|\||->|/", path)
        out = []
        for part in parts_raw:
            norm = normalize(part)
            if norm:
                out.append(norm)
        return out

    target_parts = split_section_path(section_name)
    if not target_parts:
        return None

    content = doc.get("body", {}).get("content", [])
    candidates = []
    for i, el in enumerate(content):
        if "table" not in el:
            continue
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
        candidates.append({"element": el, "index": i, "h1": h1, "h2": h2, "h3": h3})
        print(f"[docs debug] found table with headings: h1={h1!r}, h2={h2!r}, h3={h3!r}")

    matches = []
    for c in candidates:
        parts = [normalize(c.get("h1", "")), normalize(c.get("h2", "")), normalize(c.get("h3", ""))]
        parts = [p for p in parts if p]
        if any(p in parts for p in target_parts):
            matches.append(c)

    if not matches:
        if DRY_RUN:
            print("[docs debug] no table matched DOC_SECTION; available heading paths:")
            for c in candidates:
                print(f" - h1={c.get('h1')!r}, h2={c.get('h2')!r}, h3={c.get('h3')!r}")
        return None

    if len(matches) > 1:
        print("[docs warn] multiple tables matched DOC_SECTION path; selecting the first. Paths:")
        for c in matches:
            print(f" - h1={c.get('h1')!r}, h2={c.get('h2')!r}, h3={c.get('h3')!r}")
    return matches[0]


## MODIFICATION: New function to read existing Problem URLs to avoid duplicates (since we no longer store Submission ID).
def get_existing_problem_urls(docs_service, doc_id: str, section: str) -> set:
    """Reads target table and returns a set of existing Problem URLs (assumed column order: time | topic | problem | result | problem url)."""
    if not doc_id:
        return set()
    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        table_info = choose_table_by_section(doc, section)
        if not table_info:
            print(f"[docs warning] Could not find table for section '{section}'. Assuming no existing rows.")
            return set()
        table = table_info.get("element", {}).get("table", {})
        table_rows = table.get("tableRows", [])
        urls = set()
        for row in table_rows[1:]:  # skip header
            cells = row.get("tableCells", [])
            if len(cells) < 5:
                continue
            url_text = extract_cell_text(cells[4])  # 5th column
            if url_text:
                urls.add(url_text.strip())
        print(f"[docs] Retrieved {len(urls)} existing problem URLs from the document.")
        return urls
    except Exception as e:
        print(f"[docs error] Failed to get existing problem URLs: {e}")
        return set()
    

def getCodeAndTopic(problem_url: str):
    """Return (number, code, topic) using problem_topics.json keyed by code like J03007."""
    try:
        code_key = (problem_url or '').rstrip('/').split('/')[-1]
        db = json.load(open("problem_topics.json", "r", encoding="utf-8"))
        by_code = {it["title"]: [it["code"], it["sub_group"]] for it in db}
        number, topic = by_code.get(code_key, "Unknown")
        return number, code_key, topic
    except Exception:
        return "", ""


def make_batch_entry(item: Dict) -> Optional[Dict]:
    """Build a JSON entry from a submission if it is AC + Java and has a numeric code."""
    res = (item.get("result") or "").strip()
    is_java = (item.get("compiler", "").strip().lower() == "java")
    if res != "AC" or not is_java:
        return None
    url = item.get("problem_url") or ""
    if not url:
        return None
    dt = try_parse_time(item.get("time_text") or "")
    date_text = dt.strftime("%d-%m-%Y") if dt else (item.get("time_text") or "")
    number, code, topic = getCodeAndTopic(url)
    return {
        "date": date_text,
        "topic": topic,
        "number": number,
        "problem": item.get("problem") or "",
        "result": res,
    }


def append_rows_and_fill_docs(doc_id: str, docs_service, table_element: Dict, rows_data: List[List[str]]):
    table = table_element.get("element", {}).get("table")
    if not table:
        print("[docs] table element missing")
        return False

    start_index = table_element.get("element", {}).get("startIndex")
    if start_index is None:
        print("[docs] table startIndex not available")
        return False

    num_to_add = len(rows_data)
    if num_to_add == 0:
        return True

    # Re-fetch current table state to avoid stale row count (especially after deletes)
    try:
        fresh_doc = docs_service.documents().get(documentId=doc_id).execute()
        fresh_table_info = choose_table_by_section(fresh_doc, DOC_SECTION)
        if fresh_table_info:
            table = fresh_table_info.get("element", {}).get("table", table)
            start_index = fresh_table_info.get("element", {}).get("startIndex", start_index)
    except Exception as e:
        print(f"[docs warn] could not refresh table before insert: {e}")

    current_row_count = len(table.get("tableRows", []))
    if current_row_count == 0:
        print("[docs warn] table has zero rows (no header?) — cannot append.")
        return False

    # Always append after the last existing row (current_row_count - 1)
    base_row_index = max(current_row_count - 1, 0)
    requests_payload = []
    for i in range(num_to_add):
        requests_payload.append({
            "insertTableRow": {
                "tableCellLocation": {"tableStartLocation": {"index": start_index}, "rowIndex": base_row_index + i},
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


def clear_table_body(doc_id: str, docs_service, section: str, max_attempts: int = 200) -> bool:
    """Remove all rows except the header from the target table.
    Deletes row index 1 repeatedly, refreshing the table each time to avoid stale indices.
    """
    attempts = 0
    while True:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        table_info = choose_table_by_section(doc, section)
        if not table_info:
            print(f"[docs error] Could not re-locate table for section '{section}' during clear.")
            return False
        table = table_info.get("element", {}).get("table", {})
        start_index = table_info.get("element", {}).get("startIndex")
        if not table or start_index is None:
            print("[docs error] Table or startIndex missing during clear.")
            return False

        rows = len(table.get("tableRows", []))
        if rows <= 1:
            return True

        req = [{
            "deleteTableRow": {
                "tableCellLocation": {
                    "tableStartLocation": {"index": start_index},
                    "rowIndex": 1,
                }
            }
        }]
        try:
            docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": req}).execute()
            time.sleep(0.15)
        except Exception as e:
            attempts += 1
            print(f"[docs warn] delete row attempt {attempts} failed: {e}")
            time.sleep(0.3)
            if attempts >= max_attempts:
                print("[docs error] too many delete attempts; aborting clear.")
                return False


def rebuild_table_from_batch(doc_id: str, docs_service, section: str, batch: Dict[str, Dict]):
    """Clear data rows and rebuild table from JSON entries.
    Columns expected: date | topic | number | problem | result
    """
    if not clear_table_body(doc_id, docs_service, section):
        return False

    def date_key(s: str):
        try:
            return datetime.strptime(s, "%d-%m-%Y")
        except Exception:
            return datetime.min

    items = list(batch.values())
    items.sort(key=lambda it: (date_key(it.get("date", "01-01-1970")), int(it.get("number", "0"))), reverse=True)
    rows = [[it.get("date", ""), it.get("topic", ""), it.get("number", ""), it.get("problem", ""), it.get("result", "")] for it in items]

    # Re-fetch table info after clearing to ensure fresh metadata
    doc = docs_service.documents().get(documentId=doc_id).execute()
    table_info = choose_table_by_section(doc, section)
    if not table_info:
        print(f"[docs error] Could not find table for section '{section}' after clearing.")
        return False

    return append_rows_and_fill_docs(doc_id, docs_service, table_info, rows)



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


def get_cookie_string_auto():
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        driver = webdriver.Chrome(options=options)
        driver.get(LOGIN_URL)
        driver.find_element(By.CSS_SELECTOR, USERNAME_SELECTOR).send_keys(LOGIN_USERNAME)
        driver.find_element(By.CSS_SELECTOR, PASSWORD_SELECTOR).send_keys(LOGIN_PASSWORD)
        driver.find_element(By.CSS_SELECTOR, SUBMIT_SELECTOR).click()
        cookies = driver.get_cookies()
        cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        driver.quit()
        return cookie_string
    except Exception as e:
        print(f"[auto-login] failed: {e}")
        return None

def build_session():
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    cookie = COOKIE_STRING
    if os.getenv("AUTO_LOGIN", "false").lower() in ("1", "true", "yes", "y"):
        auto_cookie = get_cookie_string_auto()
        if auto_cookie:
            cookie = auto_cookie
            print("[auto-login] using auto-fetched cookie string")
        else:
            print("[auto-login] fallback to manual COOKIE_STRING")
    sess.cookies.update(parse_cookie_string(cookie))
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
    batch = load_batch()
    pages = [LIST_URL]

    if ENABLE_PAGINATION and MAX_PAGES > 1:
        pages = [make_page_url(LIST_URL, i) for i in range(1, MAX_PAGES + 1)]

    for url in pages:
        print(f"[fetch] {url}")
        html = fetch_page(session, url)
        rows = parse_rows(html)
        print(f"[parse] found {len(rows)} rows")
        for item in rows:
            entry = make_batch_entry(item)
            if not entry:
                continue
            key = entry["number"]
            if key not in batch:
                batch[key] = entry

    # DRY_RUN: fill JSON only
    if DRY_RUN:
        save_batch(batch)
        print(f"[dry-run] JSON populated with {len(batch)} entries; Docs unchanged.")
        return

    # Non-dry: update JSON and rebuild Docs from JSON
    save_batch(batch)
    if ENABLE_DOCS:
        if not GOOGLE_DOC_ID:
            die("ENABLE_DOCS is true but GOOGLE_DOC_ID is empty")
        docs = get_docs_service()
        if rebuild_table_from_batch(GOOGLE_DOC_ID, docs, DOC_SECTION, batch):
            print("[docs] Table rebuilt from JSON.")
        else:
            die(f"Failed to locate or rebuild table for section '{DOC_SECTION}'.")
    else:
        print("[info] ENABLE_DOCS=false; JSON updated but Docs not modified.")

# 2332323232
if __name__ == "__main__":
    sync()