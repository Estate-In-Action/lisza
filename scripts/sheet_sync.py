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
  python3 LISZA/scripts/sheet_sync.py [--dry-run] [--bidirectional]
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import post_pending

SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "ledger.db")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TAB_PENDING = "_Pending"
TAB_LEDGER  = "_Ledger_Live"
TAB_STATUS  = "_Sync_Status"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    conn.row_factory = sqlite3.Row
    post_pending.ensure_pending_inbox(conn)
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
               "Suggested Account", "Suggested Amount", "Notes", "Raw Path",
               "Action", "Action Result"]
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
            "",
            "",
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


def parse_action(text):
    text = (text or "").strip()
    if not text:
        return None, {}
    verb, _, rest = text.partition(":")
    verb = verb.strip().lower()
    fields = {}
    rest = rest.strip()
    if rest:
        if verb == "reject" and "=" not in rest:
            fields["reason"] = rest
        else:
            for part in rest.split(","):
                key, sep, value = part.partition("=")
                if not sep:
                    raise ValueError(f"malformed action part '{part.strip()}'")
                fields[key.strip().lower()] = value.strip()
    return verb, fields


def _header_map(headers):
    return {str(h).strip().lower().replace("_", " "): i for i, h in enumerate(headers)}


def _action_result(ws, row_num, action_col, result_col, message, *, clear_action):
    ws.update_cell(row_num, result_col + 1, message)
    if clear_action:
        ws.update_cell(row_num, action_col + 1, "")


def process_sheet_actions(ws, conn) -> dict[str, int]:
    """Read _Pending action cells and dispatch approved review actions."""
    values = ws.get_all_values()
    counters = {"approved": 0, "rejected": 0, "edited": 0, "errors": 0, "skipped": 0}
    if not values:
        return counters

    headers = _header_map(values[0])
    required = ["id", "action", "action result"]
    missing = [h for h in required if h not in headers]
    if missing:
        counters["errors"] += 1
        print(f"  _Pending headers missing: {', '.join(missing)}")
        return counters

    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    id_col = headers["id"]
    action_col = headers["action"]
    result_col = headers["action result"]

    for row_num, row in enumerate(values[1:], start=2):
        action = row[action_col].strip() if len(row) > action_col else ""
        existing_result = row[result_col].strip() if len(row) > result_col else ""
        if not action or existing_result:
            counters["skipped"] += 1
            continue
        try:
            row_id = int(row[id_col])
            verb, fields = parse_action(action)
            if verb == "approve":
                account = fields.pop("account", None)
                amount = fields.pop("amount", None)
                payment_account = fields.pop("payment_account", fields.pop("payment-account", None))
                if fields:
                    raise ValueError(f"unknown field(s): {', '.join(sorted(fields))}")
                entry_id = post_pending.approve_row(
                    row_id,
                    db_path=db_path,
                    account_override=account,
                    amount_override=float(amount) if amount else None,
                    payment_account=payment_account,
                    actor="sheet",
                )
                if entry_id:
                    counters["approved"] += 1
                    _action_result(
                        ws, row_num, action_col, result_col,
                        f"POSTED as #{entry_id} at {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
                        clear_action=True,
                    )
                else:
                    counters["skipped"] += 1
                    _action_result(
                        ws, row_num, action_col, result_col,
                        "Already actioned or not reviewed",
                        clear_action=True,
                    )
            elif verb == "reject":
                reason = fields.pop("reason", "")
                if fields:
                    raise ValueError(f"unknown field(s): {', '.join(sorted(fields))}")
                rejected = post_pending.reject_row(row_id, db_path=db_path, reason=reason, actor="sheet")
                if rejected:
                    counters["rejected"] += 1
                    _action_result(ws, row_num, action_col, result_col, "REJECTED", clear_action=True)
                else:
                    counters["skipped"] += 1
                    _action_result(
                        ws, row_num, action_col, result_col,
                        "Already actioned or not reviewed",
                        clear_action=True,
                    )
            elif verb == "edit":
                if len(fields) != 1:
                    raise ValueError("edit expects exactly one field=value pair")
                field, value = next(iter(fields.items()))
                edited = post_pending.edit_row(row_id, db_path=db_path, field=field, value=value, actor="sheet")
                if edited:
                    counters["edited"] += 1
                    _action_result(ws, row_num, action_col, result_col, f"EDITED {field}={value}", clear_action=True)
                else:
                    counters["skipped"] += 1
                    _action_result(
                        ws, row_num, action_col, result_col,
                        "Already actioned or not reviewed",
                        clear_action=True,
                    )
            else:
                raise ValueError(f"unknown action '{verb}'")
        except Exception as exc:
            counters["errors"] += 1
            _action_result(ws, row_num, action_col, result_col, f"ERROR: {exc}", clear_action=False)
    return counters


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync LISZA ledger tabs to Google Sheets.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bidirectional", action="store_true",
                        help="process _Pending action cells before pushing the refreshed mirror")
    args = parser.parse_args()

    print("=== LISZA sheet_sync.py ===")
    if args.dry_run:
        print("  [DRY RUN — no writes to Google Sheets]")

    gc = sheet = ws_pending = None
    if not args.dry_run:
        print("\nConnecting to Google Sheets …")
        gc = get_client()
        sheet = gc.open_by_key(SPREADSHEET_ID)
        try:
            ws_pending = sheet.worksheet(TAB_PENDING)
        except Exception:
            ws_pending = None

    conn = get_db()
    if args.bidirectional and not args.dry_run and ws_pending is not None:
        print("Processing _Pending actions …")
        action_counts = process_sheet_actions(ws_pending, conn)
        print(f"  Actions: {action_counts}")

    print("Querying ledger.db …")
    pending_data = fetch_pending(conn)
    ledger_data  = fetch_ledger(conn)
    status_data  = fetch_status(conn)
    conn.close()

    print(f"  Pending rows   : {len(pending_data)-1}")
    print(f"  Ledger rows    : {len(ledger_data)-1}")

    if args.dry_run:
        print("\nDry run complete — exiting without writing to Sheets.")
        return

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
