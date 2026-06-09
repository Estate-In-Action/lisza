#!/usr/bin/env python3
"""
LISZA sheet_sync.py
---------------------
Pushes a read-only mirror of the local ledger.db into three system tabs
on the "Personal Accounts" Google Sheet:

  _Pending      — pending_inbox rows awaiting review
  _Ledger_Live  — all posted journal entries + splits
  _Sync_Status  — last-run metadata / health summary

Auth: Google Service Account JSON stored in the LISZA_SHEETS_SA_JSON
      environment variable (paste the full JSON content as a secret in
      Zo Settings > Advanced).

      One-time setup — see SETUP at the bottom of this file.

Usage:
  python3 LISZA/scripts/sheet_sync.py [--dry-run]
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "ledger.db")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TAB_PENDING = "_Pending"
TAB_LEDGER  = "_Ledger_Live"
TAB_STATUS  = "_Sync_Status"

DRY_RUN = "--dry-run" in sys.argv


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_pending(conn):
    rows = conn.execute("""
        SELECT
            id,
            received_at,
            source,
            status,
            suggested_account,
            CAST(suggested_amount AS TEXT) AS suggested_amount,
            notes,
            raw_path
        FROM pending_inbox
        ORDER BY received_at DESC
    """).fetchall()
    headers = ["ID", "Received At", "Source", "Status",
               "Suggested Account", "Suggested Amount", "Notes", "Raw Path"]
    data = [list(headers)]
    for r in rows:
        data.append([
            r["id"],
            r["received_at"] or "",
            r["source"] or "",
            r["status"] or "",
            r["suggested_account"] or "",
            r["suggested_amount"] or "",
            r["notes"] or "",
            r["raw_path"] or "",
        ])
    return data


def fetch_ledger(conn):
    rows = conn.execute("""
        SELECT
            e.id           AS entry_id,
            e.entry_date,
            e.description,
            e.payee,
            e.source,
            e.source_ref,
            e.status,
            e.posted_at,
            s.account      AS acct_code,
            a.name         AS acct_name,
            CAST(s.dr AS TEXT) AS dr,
            CAST(s.cr AS TEXT) AS cr,
            s.memo
        FROM entries e
        JOIN splits s ON s.entry_id = e.id
        LEFT JOIN accounts a ON a.code = s.account
        WHERE e.status = 'posted'
        ORDER BY e.entry_date DESC, e.id DESC, s.id ASC
    """).fetchall()
    headers = ["Entry ID", "Date", "Description", "Payee", "Source",
               "Source Ref", "Status", "Posted At",
               "Acct Code", "Acct Name", "Debit", "Credit", "Memo"]
    data = [list(headers)]
    for r in rows:
        data.append([
            r["entry_id"],
            r["entry_date"] or "",
            r["description"] or "",
            r["payee"] or "",
            r["source"] or "",
            r["source_ref"] or "",
            r["status"] or "",
            r["posted_at"] or "",
            r["acct_code"] or "",
            r["acct_name"] or "",
            r["dr"],
            r["cr"],
            r["memo"] or "",
        ])
    return data


def fetch_status(conn):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    entry_count  = conn.execute("SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
    split_count  = conn.execute("SELECT COUNT(*) FROM splits").fetchone()[0]
    pending_count = conn.execute("SELECT COUNT(*) FROM pending_inbox").fetchone()[0]
    pending_new  = conn.execute("SELECT COUNT(*) FROM pending_inbox WHERE status='new'").fetchone()[0]

    # Balance check: total DR = total CR across all posted splits
    bal = conn.execute("""
        SELECT ROUND(SUM(s.dr),2) AS total_dr, ROUND(SUM(s.cr),2) AS total_cr
        FROM splits s
        JOIN entries e ON e.id = s.entry_id
        WHERE e.status = 'posted'
    """).fetchone()
    balance_ok = "OK" if abs((bal["total_dr"] or 0) - (bal["total_cr"] or 0)) < 0.01 else "FAIL"

    data = [
        ["Key", "Value"],
        ["Last Sync",           now],
        ["Posted Entries",      entry_count],
        ["Total Splits",        split_count],
        ["Pending Inbox Total", pending_count],
        ["Pending Inbox New",   pending_new],
        ["Balance Check",       balance_ok],
        ["Total DR",            f"${bal['total_dr']:,.2f}"],
        ["Total CR",            f"${bal['total_cr']:,.2f}"],
        ["Ledger DB Path",      os.path.abspath(DB_PATH)],
        ["Spreadsheet ID",      SPREADSHEET_ID],
    ]
    return data


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------

def get_client():
    sa_json = os.environ.get("LISZA_SHEETS_SA_JSON")
    if not sa_json:
        print("ERROR: LISZA_SHEETS_SA_JSON env var not set.")
        print("       See SETUP at the bottom of this file.")
        sys.exit(1)

    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(sa_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def ensure_tab(sheet, title, headers):
    """Create the tab if it doesn't exist; return the worksheet."""
    try:
        ws = sheet.worksheet(title)
        print(f"  Tab '{title}' already exists.")
    except Exception:
        ws = sheet.add_worksheet(title=title, rows=2000, cols=len(headers))
        print(f"  Tab '{title}' created.")
    return ws


def push_tab(ws, data, label):
    """Clear and rewrite the worksheet with data (list of lists)."""
    ws.clear()
    if len(data) > 1:
        ws.update(data, "A1", value_input_option="RAW")
        # Bold the header row
        ws.format("1:1", {"textFormat": {"bold": True}})
    print(f"  {label}: {len(data)-1} data rows written.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== LISZA sheet_sync.py ===")
    if DRY_RUN:
        print("  [DRY RUN — no writes to Google Sheets]")

    conn = get_db()
    print("Querying ledger.db …")
    pending_data = fetch_pending(conn)
    ledger_data  = fetch_ledger(conn)
    status_data  = fetch_status(conn)
    conn.close()

    print(f"  Pending rows   : {len(pending_data)-1}")
    print(f"  Ledger rows    : {len(ledger_data)-1}")

    if DRY_RUN:
        print("\nDry run complete — exiting without writing to Sheets.")
        return

    print("\nConnecting to Google Sheets …")
    gc = get_client()
    sheet = gc.open_by_key(SPREADSHEET_ID)

    print("Ensuring system tabs exist …")
    ws_pending = ensure_tab(sheet, TAB_PENDING, pending_data[0])
    ws_ledger  = ensure_tab(sheet, TAB_LEDGER,  ledger_data[0])
    ws_status  = ensure_tab(sheet, TAB_STATUS,  status_data[0])

    print("\nPushing data …")
    push_tab(ws_pending, pending_data, TAB_PENDING)
    push_tab(ws_ledger,  ledger_data,  TAB_LEDGER)
    push_tab(ws_status,  status_data,  TAB_STATUS)

    print("\nDone. Sync complete.")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# SETUP — one-time, ~5 minutes
# ---------------------------------------------------------------------------
#
# 1. Go to https://console.cloud.google.com/
#    • Create (or select) a project.
#    • Enable the Google Sheets API: APIs & Services → Enable APIs → search
#      "Google Sheets API" → Enable.
#
# 2. Create a Service Account:
#    APIs & Services → Credentials → Create Credentials → Service Account.
#    Name it "finance-ledger-sync" (or anything). Skip roles (not needed).
#    Open the service account → Keys → Add Key → JSON → Download.
#
# 3. Share the spreadsheet with the service account:
#    Open the downloaded JSON, copy the "client_email" value
#    (looks like finance-ledger-sync@your-project.iam.gserviceaccount.com).
#    In Google Sheets → Share → paste that email → Editor → Send.
#
# 4. Save the JSON content as a Zo secret:
#    Zo Settings → Advanced → Secrets → add key LISZA_SHEETS_SA_JSON,
#    paste the full JSON file content as the value.
#
# 5. Test:
#    python3 LISZA/scripts/sheet_sync.py --dry-run
#    python3 LISZA/scripts/sheet_sync.py
# ---------------------------------------------------------------------------
