from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import time
import json
from dotenv import load_dotenv
from typing import Optional, List, Dict

# This script locates a table in a Google Doc by looking for a nearby section
# heading text (e.g. "CHUONG 1" / "CHUONG 2"), appends rows to that table,
# then fills the new cells. It uses a safe two-step pattern:
# 1) batchUpdate to insert empty rows
# 2) fetch document to get new cell indices
# 3) batchUpdate to insert text into those cell ranges

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]

# Load .env and let its values override OS environment variables so the script
# uses the .env file first (per user request).
load_dotenv(override=True)


def get_docs_service():
    keyfile = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not keyfile:
        raise SystemExit("Please set GOOGLE_APPLICATION_CREDENTIALS env var to the service account json")
    creds = service_account.Credentials.from_service_account_file(keyfile, scopes=SCOPES)
    return build("docs", "v1", credentials=creds)


def extract_paragraph_text(el: Dict) -> str:
    # Combine text runs inside a paragraph element
    if not el or "paragraph" not in el:
        return ""
    parts = []
    for pe in el["paragraph"].get("elements", []):
        txt_run = pe.get("textRun")
        if txt_run and txt_run.get("content"):
            parts.append(txt_run.get("content"))
    return "".join(parts).strip()


def find_tables_with_context(doc: Dict) -> List[Dict]:
    # Return a list of dicts with keys: element (the table element),
    # index (index in body.content), and context_text (closest preceding paragraph)
    out = []
    content = doc.get("body", {}).get("content", [])
    for i, el in enumerate(content):
        if "table" in el:
            # Find preceding non-empty paragraph/heading
            ctx = ""
            for j in range(i - 1, -1, -1):
                text = extract_paragraph_text(content[j])
                if text:
                    ctx = text
                    break
            out.append({"element": el, "index": i, "context": ctx})
    return out


def choose_table_by_section(doc: Dict, section_name: str) -> Optional[Dict]:
    # Try to match section_name in the context text (case-insensitive)
    tables = find_tables_with_context(doc)
    for t in tables:
        if section_name.lower() in (t.get("context", "").lower()):
            return t
    # Fallback: return first table
    return tables[0] if tables else None


def append_rows_and_fill(doc_id: str, docs_service, table_element: Dict, rows_data: List[List[str]]):
    # rows_data: list of rows where each row is a list of column strings
    table = table_element.get("element", {}).get("table")
    if not table:
        raise SystemExit("table element missing")

    start_index = table_element.get("element", {}).get("startIndex")
    if start_index is None:
        raise SystemExit("table startIndex not available")

    old_row_count = len(table.get("tableRows", []))
    cols = table.get("columns", 0)

    num_to_add = len(rows_data)
    if num_to_add == 0:
        print("No rows to add")
        return

    # Step 1: insert empty rows (append)
    requests = []
    for _ in range(num_to_add):
        requests.append({
            "insertTableRow": {
                "tableCellLocation": {"tableStartLocation": {"index": start_index}, "rowIndex": old_row_count - 1},
                "insertBelow": True,
            }
        })

    docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    print(f"Inserted {num_to_add} empty rows")

    # Small delay to ensure doc indexes update on Google's side
    time.sleep(0.5)

    # Step 2: fetch document again to find the newly created cells
    doc2 = docs_service.documents().get(documentId=doc_id).execute()
    table2 = choose_table_by_section(doc2, extract_paragraph_text(table_element.get("element")))
    if not table2:
        raise SystemExit("table disappeared after insert")

    tbl = table2.get("element", {}).get("table")
    new_row_count = len(tbl.get("tableRows", []))
    inserted = new_row_count - old_row_count
    if inserted < num_to_add:
        print(f"Warning: expected to insert {num_to_add} rows but found {inserted}")

    # We'll fill the last `num_to_add` rows
    start_fill = new_row_count - num_to_add

    # Collect insertText requests. Use the first content element's startIndex inside
    # each cell (paragraph start) when available; fall back to the cell startIndex.
    insert_requests = []
    for r_idx in range(start_fill, new_row_count):
        row = tbl["tableRows"][r_idx]
        cells = row.get("tableCells", [])
        # For each column, find the cell insertion index and insert the corresponding text
        for c_idx in range(min(len(cells), len(rows_data[0]))):
            cell = cells[c_idx]
            # Determine a safe insertion index inside the first paragraph of the cell.
            insert_index = None
            contents = cell.get("content") or []
            # Look for the first content entry that contains a paragraph and has endIndex
            for content_entry in contents:
                if "paragraph" in content_entry:
                    start_i = content_entry.get("startIndex")
                    end_i = content_entry.get("endIndex")
                    # Prefer to insert just before the paragraph's endIndex to append text
                    if isinstance(end_i, int) and isinstance(start_i, int) and end_i > start_i:
                        insert_index = end_i - 1
                    elif isinstance(start_i, int):
                        insert_index = start_i + 1
                    break

            # Fallback to the cell's startIndex if nothing else is available
            if insert_index is None:
                cell_start = cell.get("startIndex")
                if isinstance(cell_start, int):
                    insert_index = cell_start + 1

            if insert_index is None:
                # skip if no valid index
                print(f"[debug] skipping cell r={r_idx} c={c_idx}: no insertion index")
                continue

            text_to_insert = rows_data[r_idx - start_fill][c_idx]
            if not text_to_insert:
                continue

            # Append an insertText request at the computed safe index
            insert_requests.append({
                "insertText": {"location": {"index": insert_index}, "text": text_to_insert}
            })

    if insert_requests:
        # Important: insertText requests change document indices as they run.
        # To avoid shifting later insertion targets, sort requests by descending index
        # so we insert at higher indices first.
        def req_index(req):
            return req["insertText"]["location"]["index"]

        insert_requests.sort(key=req_index, reverse=True)

        # send in batches of reasonable size to avoid huge requests
        for i in range(0, len(insert_requests), 50):
            chunk = insert_requests[i : i + 50]
            docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": chunk}).execute()
        print(f"Filled {len(insert_requests)} cells")
    else:
        print("No cell insertions necessary")


def main():
    docs = get_docs_service()
    doc_id = os.getenv("GOOGLE_DOC_ID")
    if not doc_id:
        raise SystemExit("Please set GOOGLE_DOC_ID environment variable")

    # Section name to target, default to "CHUONG 1" if not provided
    section = os.getenv("DOC_SECTION", "CHUONG 1")

    doc = docs.documents().get(documentId=doc_id).execute()
    print("Document title:", doc.get("title"))

    table_info = choose_table_by_section(doc, section)
    if not table_info:
        print("No tables found in document")
        return

    print(f"Found table near section: '{table_info.get('context')}' (startIndex={table_info['element'].get('startIndex')})")

    # Example rows to append — you can replace this with scraped data
    # The table in your screenshot has columns: Submission ID, Submission time, Problem, Result, Problem URL, Compiler
    sample_row = [
        "123456",
        "2025-08-19 12:34:56",
        "Example Problem",
        "AC",
        "https://example.com/problem/123",
        "Java",
    ]

    # Insert one sample row. To insert multiple, provide multiple lists inside rows_data.
    append_rows_and_fill(doc_id, docs, table_info, [sample_row])


if __name__ == "__main__":
    main()