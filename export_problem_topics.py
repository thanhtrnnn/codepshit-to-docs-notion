#!/usr/bin/env python3
"""Export problem topic mapping (Code, Title, Sub group) from code.ptit.edu.vn.

Grabs the problems list across 3 pages (page=1..3) and outputs a JSON file
with an array of objects: {"code": ..., "title": ..., "sub_group": ...}.

Authentication: if you need to be logged in to view the list, set COOKIE_STRING
in a .env file (same format as browser cookie header: k1=v1; k2=v2).

Environment variables (optional):
  PROBLEMS_BASE_URL   Default: https://code.ptit.edu.vn/student/question
  PAGES               Comma-separated page numbers (default: 1,2,3)
  COOKIE_STRING       Browser cookies if required
  USER_AGENT          Override UA string
  OUTPUT_FILE         File to write JSON (default: problem_topics.json)

Usage:
  python export_problem_topics.py
"""

from __future__ import annotations
import os, json, urllib.parse, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("PROBLEMS_BASE_URL", "https://code.ptit.edu.vn/student/question").strip()
PAGES = [p.strip() for p in os.getenv("PAGES", "1,2,3").split(",") if p.strip()]
COOKIE_STRING = os.getenv("COOKIE_STRING", "").strip()
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "problem_topics.json").strip()


def parse_cookie_string(s: str):
    jar = requests.cookies.RequestsCookieJar()
    if not s:
        return jar
    for part in s.split(";"):
        if not part.strip():
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            jar.set(k.strip(), v.strip())
    return jar


def build_session():
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    if COOKIE_STRING:
        sess.cookies.update(parse_cookie_string(COOKIE_STRING))
    else:
        print("[warn] COOKIE_STRING not set – if the site requires auth you'll get empty results.")
    return sess


def make_page_url(base: str, page: str) -> str:
    parts = list(urllib.parse.urlparse(base))
    qs = urllib.parse.parse_qs(parts[4])
    qs["page"] = [page]
    parts[4] = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parts)


def fetch_html(sess: requests.Session, url: str) -> str:
    r = sess.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def extract_rows(html: str):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    data = []
    for tr in table.find_all("tr"):
        ths = tr.find_all("th")
        if ths:
            continue  # header
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        # Column order (per screenshot): No, Code, Title, Group, Sub group, Level
        code = tds[1].get_text(strip=True)
        title = tds[2].get_text(" ", strip=True)
        sub_group = tds[5].get_text(" ", strip=True)
        if not code:
            continue
        data.append({
            "code": code,
            "title": title,
            "sub_group": sub_group,
        })
    return data


def dedupe(items):
    seen = {}
    for it in items:
        # If code repeats with different title/sub_group, keep the first; could be adjusted.
        if it['code'] not in seen:
            seen[it['code']] = it
    return list(seen.values())


def main():
    sess = build_session()
    all_rows = []
    for p in PAGES:
        url = make_page_url(BASE_URL, p)
        print(f"[fetch] {url}")
        try:
            html = fetch_html(sess, url)
        except Exception as e:
            print(f"[warn] failed fetching page {p}: {e}")
            continue
        rows = extract_rows(html)
        if not rows and 'login' in html.lower():
            print("[error] Received what looks like a login page. Set COOKIE_STRING with your browser cookies.")
            break
        print(f"[parse] page {p} -> {len(rows)} rows")
        all_rows.extend(rows)

    final_rows = dedupe(all_rows)
    print(f"[total] {len(final_rows)} unique problems")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_rows, f, ensure_ascii=False, indent=2)
    print(f"[write] {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
