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
  - Problem (title, DEFAULT field)
  - No (rich_text)
  - Topic (select)
  - Submission ID (rich_text)
  - Submission time (date)
  - Result (select)
  - Problem URL (url)

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
from datetime import datetime, timezone, timedelta
from typing import Optional
from functools import lru_cache
from bs4 import BeautifulSoup
from notion_client import Client, APIResponseError
import dns.resolver

# ----------------------
# Config via env vars
# ----------------------
load_dotenv()
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()

LIST_URL = os.getenv("LIST_URL", "").strip()
COOKIE_STRING = os.getenv("COOKIE_STRING", "").strip()  # "k1=v1; k2=v2"
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36").strip()
# Selenium login variables
AUTO_LOGIN = os.getenv("AUTO_LOGIN", "false").lower() in ("1", "true", "yes", "y")
LOGIN_URL = os.getenv("LOGIN_URL", "").strip()
LOGIN_USERNAME = os.getenv("LOGIN_USERNAME_THANH", "").strip()
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD_THANH", "").strip()
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
NOTION_RATE_DELAY = float(os.getenv("NOTION_RATE_DELAY", "0"))  # seconds between Notion writes
TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
]

TARGET_TZ = timezone(timedelta(hours=7))
TIME_SOURCE_URL = os.getenv("TIME_SOURCE_URL", "https://worldtimeapi.org/api/timezone/Etc/UTC")


@lru_cache(maxsize=1)
def get_local_timezone() -> timezone:
    try:
        resp = requests.get(TIME_SOURCE_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        remote_iso = data.get("datetime")
        if remote_iso:
            remote_dt = datetime.fromisoformat(remote_iso.replace("Z", "+00:00"))
            remote_dt = remote_dt.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            remote_dt = datetime.utcnow()
    except Exception:
        remote_dt = datetime.utcnow()

    local_now = datetime.now()
    offset = local_now - remote_dt
    offset_minutes = int(round(offset.total_seconds() / 60))
    offset_minutes = max(min(offset_minutes, 14 * 60), -12 * 60)
    return timezone(timedelta(minutes=offset_minutes))


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
    # Prefer link text if available
    a = el.find("a")
    return (a.get_text(" ", strip=True) if a else el.get_text(" ", strip=True)).strip()


def try_parse_time(s):
    s = s.strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Fallback: try to extract ISO-like datetime
    m = re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", s)
    if m:
        try:
            return datetime.fromisoformat(m.group(0).replace(" ", "T"))
        except Exception:
            pass
    return None


def convert_to_gmt7(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    local_tz = get_local_timezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    else:
        dt = dt.astimezone(local_tz)
    return dt.astimezone(TARGET_TZ)


def get_cookie_string_auto():
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        driver = webdriver.Chrome(options=options)
        driver.get(LOGIN_URL)
        time.sleep(2)
        driver.find_element(By.CSS_SELECTOR, USERNAME_SELECTOR).send_keys(LOGIN_USERNAME)
        driver.find_element(By.CSS_SELECTOR, PASSWORD_SELECTOR).send_keys(LOGIN_PASSWORD)
        driver.find_element(By.CSS_SELECTOR, SUBMIT_SELECTOR).click()
        time.sleep(3)
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
    # Parse URL to get hostname
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    
    # Use custom DNS resolver with 8.8.8.8
    resolver = dns.resolver.Resolver()
    resolver.nameservers = ['8.8.8.8']
    
    try:
        # Resolve hostname using 8.8.8.8
        answers = resolver.resolve(hostname, 'A')
        ip_address = str(answers[0])
        
        # Replace hostname with resolved IP in URL
        url_with_ip = url.replace(hostname, ip_address)
        
        # Make request with resolved IP, but keep original hostname in Host header
        headers = {'Host': hostname}
        r = session.get(url_with_ip, headers=headers, timeout=30)
    except Exception as e:
        # Fallback to original URL if DNS resolution fails
        print(f"[dns] DNS resolution failed for {hostname}: {e}, using original URL")
        r = session.get(url, timeout=30)
    
    r.raise_for_status()
    return r.text


def make_page_url(base_url, page_index):
    # Append or replace the page query param
    parts = list(urllib.parse.urlparse(base_url))
    qs = urllib.parse.parse_qs(parts[4])
    qs[PAGE_PARAM] = [str(page_index)]
    parts[4] = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parts)


def parse_rows(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(ROW_SELECTOR)
    out = []
    # Skip header-like rows if they have <th>
    for row in rows:
        if row.find_all(["th"]):
            continue

        if ID_CELL_SELECTOR or TIME_CELL_SELECTOR or PROBLEM_CELL_SELECTOR or RESULT_CELL_SELECTOR:
            id_cell = row.select_one(ID_CELL_SELECTOR) if ID_CELL_SELECTOR else None
            time_cell = row.select_one(TIME_CELL_SELECTOR) if TIME_CELL_SELECTOR else None
            prob_cell = row.select_one(PROBLEM_CELL_SELECTOR) if PROBLEM_CELL_SELECTOR else None
            res_cell = row.select_one(RESULT_CELL_SELECTOR) if RESULT_CELL_SELECTOR else None
        else:
            # td index approach
            tds = row.find_all("td")
            if not tds:
                continue
            try:
                idx = [int(x.strip()) for x in COL_INDEXES.split(",")]
            except Exception:
                idx = [0,1,2,3,4,5,6]
            # guard
            if max(idx) >= len(tds):
                continue
            id_cell, time_cell, prob_cell, res_cell, compiler_cell = (tds[idx[0]], tds[idx[1]], tds[idx[2]], tds[idx[3]], tds[idx[6]])

        sid = pick_text(id_cell)
        stime_text = pick_text(time_cell)
        prob_text = pick_text(prob_cell)
        res_text = pick_text(res_cell)
        compiler_text = pick_text(compiler_cell)

        # Problem URL if any
        prob_url = None
        if prob_cell:
            a = prob_cell.select_one(PROBLEM_LINK_SELECTOR) or prob_cell.find("a")
            if a and a.has_attr("href"):
                prob_url = urllib.parse.urljoin(base_url, a["href"])

        # Skip empty rows
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


def notion_client():
    if not NOTION_API_KEY:
        die("NOTION_API_KEY is empty")
    if not NOTION_DATABASE_ID:
        die("NOTION_DATABASE_ID is empty")
    return Client(auth=NOTION_API_KEY)


def find_page_by_number(notion: Client, number: str):
    # Query database for a page where "No" rich_text equals number
    try:
        resp = notion.databases.query(
            **{
                "database_id": NOTION_DATABASE_ID,
                "filter": {
                    "property": "No",
                    "rich_text": {"equals": number},
                },
                "page_size": 1,
            }
        )
        results = resp.get("results", [])
        return results[0] if results else None
    except APIResponseError as e:
        print(f"[warn] Notion query failed for problem number {number}: {e}")
        return None


def getCodeAndTopic(problem_url: str):
    # Extract topic from problem URL
    problem_id = problem_url.split("/")[-1]
    db = json.load(open("problem_topics.json", "r", encoding="utf-8")) # list of dicts
    # convert db to dict for faster lookup
    db = {item["title"]: [item["code"], item["sub_group"]] for item in db}

    if problem_id:
        code, topic = db.get(problem_id, ["Unknown", "Unknown"])
    return code, topic

def upsert_submission(notion: Client, item: dict):
    sid = item["id"].strip()
    if not sid:
        return False

    # Prepare properties
    props = {
        "Problem": {"title": [{"text": {"content": item.get("problem") or ""}}]},
        "Submission ID": {"rich_text": [{"text": {"content": sid}}]},
    }

    # Result (select)
    res = (item.get("result") or "").strip()
    if res:
        props["Result"] = {"select": {"name": res}}

    # Problem URL
    if item.get("problem_url"):
        props["Problem URL"] = {"url": item["problem_url"]}
        code, topic = getCodeAndTopic(item["problem_url"])
        props["Topic"] = {"select": {"name": topic}}
        props["No"] = {"rich_text": [{"text": {"content": code}}]}

    existing = find_page_by_number(notion, props["No"]["rich_text"][0]["text"]["content"])

    # Compiler
    if item.get("compiler"):
        isCompilerJava = (item["compiler"].strip().lower() == "java")

    # Submission time (date)
    dt = try_parse_time(item.get("time_text") or "")
    dt_gmt7 = convert_to_gmt7(dt)
    if dt_gmt7:
        # Notion expects ISO8601
        props["Submission time"] = {"date": {"start": dt_gmt7.isoformat()}}

    try:
        # existing = ignore submission duplicate problem/ already in database cases
        if not existing:
            if res == "AC" and isCompilerJava:
                notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=props)
                print(f"[create] {sid} – {item.get('problem')}")
        time.sleep(NOTION_RATE_DELAY)
        return True
    except APIResponseError as e:
        print(f"[error] Notion write failed for {sid}: {e}")
        return False


def sync():
    if not LIST_URL:
        die("LIST_URL is empty")
    session = build_session()
    notion = notion_client()

    total = 0
    pages = [LIST_URL]

    if ENABLE_PAGINATION and MAX_PAGES > 1:
        pages = [make_page_url(LIST_URL, i) for i in range(1, MAX_PAGES + 1)]

    for url in pages:
        print(f"[fetch] {url}")
        html = fetch_page(session, url)
        rows = parse_rows(html, LIST_URL)
        print(f"[parse] found {len(rows)} rows")
        for item in rows:
            ok = upsert_submission(notion, item)
            if ok:
                total += 1

    print(f"[done] processed {total} rows")


if __name__ == "__main__":
    sync()
