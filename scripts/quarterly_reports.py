#!/usr/bin/env python3
"""
quarterly_reports.py — Fill Trial Balance and Transaction Journal tabs for every quarter.

Generates data from ledger.db and pushes to the Client Books Google Sheet.
Creates 2026 tabs if they don't exist.

Usage:
  python3 LISZA/scripts/quarterly_reports.py [--dry-run]
"""

import os
import sys
import sqlite3
import json

import gspread
from google.oauth2.service_account import Credentials

DB_PATH = os.environ.get("LISZA_DB", "LISZA/ledger.db")
SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CC_ACCOUNTS = {"210", "211"}

QUARTERS = [
    ("Q1-2025", "2025-01-01", "2025-03-31", "trial balance Q1-25", "trnaxn jrnl Q1-25"),
    ("Q2-2025", "2025-04-01", "2025-06-30", "trial balance Q2-25", "trnaxn jrnl Q2-25"),
    ("Q3-2025", "2025-07-01", "2025-09-30", "trial balance Q3-25", "trnaxn jrnl Q3-25"),
    ("Q4-2025", "2025-10-01", "2025-12-31", "trial balance Q4-25", "trnaxn jrnl Q4-25"),
    ("Q1-2026", "2026-01-01", "2026-03-31", "trial balance Q1-26", "trnaxn jrnl Q1-26"),
    ("Q2-2026", "2026-04-01", "2026-06-30", "trial balance Q2-26", "trnaxn jrnl Q2-26"),
]

DRY_RUN = "--dry-run" in sys.argv


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def get_gc():
    sa_json = os.environ.get("LISZA_SHEETS_SA_JSON")
    if not sa_json:
        print("ERROR: LISZA_SHEETS_SA_JSON not set.")
        sys.exit(1)
    creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
    return gspread.authorize(creds)


def ensure_tab(sheet, name):
    try:
        return sheet.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=name, rows=500, cols=16)
        print(f"    Created new tab: '{name}'")
        return ws


def push_tab(ws, data, label):
    if DRY_RUN:
        print(f"    [DRY RUN] Would write {len(data)} rows to '{label}'")
        if data:
            print(f"      Header: {data[0]}")
        return
    ws.clear()
    if data:
        ws.update(data, "A1", value_input_option="RAW")
        ws.format("1:2", {"textFormat": {"bold": True}})
    print(f"    Wrote {len(data)} rows to '{label}'")


# ---------------------------------------------------------------------------
# Database queries

def get_accounts(con):
    return [
        dict(r) for r in con.execute(
            "SELECT code, name, type, sign_normal FROM accounts WHERE active=1 ORDER BY CAST(code AS INTEGER)"
        ).fetchall()
    ]


def beg_balances(con, accounts, before_date):
    """Sum of DR/CR per account for all posted entries before before_date."""
    result = {}
    rows = con.execute(
        """SELECT s.account, COALESCE(SUM(s.dr),0) as dr, COALESCE(SUM(s.cr),0) as cr
           FROM splits s
           JOIN entries e ON e.id = s.entry_id
           WHERE e.status='posted' AND e.entry_date < ?
           GROUP BY s.account""",
        (before_date,)
    ).fetchall()
    for r in rows:
        result[r["account"]] = {"dr": r["dr"], "cr": r["cr"]}
    return result


def period_activity(con, q_start, q_end):
    """DR/CR per account split into non-CC (period) and CC buckets."""
    # Tag entries that touch a CC account
    cc_entry_ids = {
        r[0] for r in con.execute(
            """SELECT DISTINCT e.id FROM entries e
               JOIN splits s ON s.entry_id = e.id
               WHERE e.status='posted'
                 AND e.entry_date BETWEEN ? AND ?
                 AND s.account IN ('210','211')""",
            (q_start, q_end)
        ).fetchall()
    }

    splits = con.execute(
        """SELECT s.account, s.dr, s.cr, s.entry_id
           FROM splits s
           JOIN entries e ON e.id = s.entry_id
           WHERE e.status='posted' AND e.entry_date BETWEEN ? AND ?""",
        (q_start, q_end)
    ).fetchall()

    period, cc = {}, {}
    for s in splits:
        code = s["account"]
        bucket = cc if s["entry_id"] in cc_entry_ids else period
        if code not in bucket:
            bucket[code] = {"dr": 0.0, "cr": 0.0}
        bucket[code]["dr"] += s["dr"]
        bucket[code]["cr"] += s["cr"]

    return period, cc


# ---------------------------------------------------------------------------
# Trial Balance

def build_trial_balance(con, accounts, q_label, q_start, q_end):
    beg = beg_balances(con, accounts, q_start)
    per, cc = period_activity(con, q_start, q_end)

    # Codes with any balances (beg or activity)
    active = set(beg) | set(per) | set(cc)
    acct_list = [a for a in accounts if a["code"] in active]

    def fmt(v):
        return round(v, 2) if v else ""

    header1 = [
        "#", "Account",
        "[---Beg Balances---]", "",
        "[---Period Activity---]", "",
        "[---CC Activity---]", "",
        "[---AJE---]", "", "", "",
        "[---Ending Balances---]", "",
    ]
    header2 = ["", "", "DR", "CR", "DR", "CR", "DR", "CR", "DR", "CR", "DR", "CR", "DR", "CR"]
    rows = [header1, header2]

    col_totals = [0.0] * 14

    for i, a in enumerate(acct_list, 1):
        code = a["code"]
        b = beg.get(code, {"dr": 0.0, "cr": 0.0})
        p = per.get(code, {"dr": 0.0, "cr": 0.0})
        c = cc.get(code, {"dr": 0.0, "cr": 0.0})

        end_dr = b["dr"] + p["dr"] + c["dr"]
        end_cr = b["cr"] + p["cr"] + c["cr"]

        vals = [
            b["dr"], b["cr"],
            p["dr"], p["cr"],
            c["dr"], c["cr"],
            0.0, 0.0, 0.0, 0.0,  # AJE
            end_dr, end_cr,
        ]
        for idx, v in enumerate(vals):
            col_totals[idx + 2] += v

        row = [i, f"{code} - {a['name']}"] + [fmt(v) for v in vals]
        rows.append(row)

    totals_row = ["", "TOTALS"] + [fmt(v) for v in col_totals[2:]]
    rows.append(totals_row)

    return rows


# ---------------------------------------------------------------------------
# Transaction Journal

def acct_type(con, entry_id):
    accts = {r[0] for r in con.execute("SELECT account FROM splits WHERE entry_id=?", (entry_id,)).fetchall()}
    if "210" in accts:
        return "CC1"
    if "211" in accts:
        return "CC2"
    if "102" in accts:
        return "CHK"
    if "101" in accts:
        return "CASH"
    if "106" in accts:
        return "APP"
    return "OTHER"


def build_transaction_journal(con, q_start, q_end):
    header = [["Date", "AccountType", "Check # or Trnaxn #", "Payee",
               "Amount", "Description/Notes", "Debit Account", "Credit Account", "Notes"]]

    entries = con.execute(
        """SELECT e.id, e.entry_date, e.description, e.payee, e.source_ref
           FROM entries e
           WHERE e.status='posted' AND e.entry_date BETWEEN ? AND ?
           ORDER BY e.entry_date, e.id""",
        (q_start, q_end)
    ).fetchall()

    rows = list(header)
    for e in entries:
        splits = con.execute(
            "SELECT account, dr, cr FROM splits WHERE entry_id=? ORDER BY dr DESC",
            (e["id"],)
        ).fetchall()
        dr_splits = [s for s in splits if s["dr"] > 0]
        cr_splits = [s for s in splits if s["cr"] > 0]

        at = acct_type(con, e["id"])
        ref = e["source_ref"] or str(e["id"])
        payee = e["payee"] or ""
        desc = e["description"] or ""

        # All entries in scope are 2-split; guard for future compound entries
        if len(dr_splits) == 1 and len(cr_splits) == 1:
            rows.append([
                e["entry_date"], at, ref, payee,
                round(dr_splits[0]["dr"], 2), desc,
                dr_splits[0]["account"], cr_splits[0]["account"], "",
            ])
        else:
            primary_cr = cr_splits[0]["account"] if cr_splits else ""
            for ds in dr_splits:
                rows.append([
                    e["entry_date"], at, ref, payee,
                    round(ds["dr"], 2), desc,
                    ds["account"], primary_cr, "compound",
                ])

    return rows


# ---------------------------------------------------------------------------
# Main

def main():
    print(f"\n=== quarterly_reports.py {'[DRY RUN]' if DRY_RUN else ''} ===\n")

    con = get_db()
    accounts = get_accounts(con)
    print(f"Loaded {len(accounts)} accounts from ledger.\n")

    if not DRY_RUN:
        print("Connecting to Google Sheets …")
        gc = get_gc()
        sheet = gc.open_by_key(SPREADSHEET_ID)
    else:
        sheet = None

    for q_label, q_start, q_end, tb_tab, tj_tab in QUARTERS:
        print(f"--- {q_label} ({q_start} → {q_end}) ---")

        tb_data = build_trial_balance(con, accounts, q_label, q_start, q_end)
        tj_data = build_transaction_journal(con, q_start, q_end)
        entry_count = len(tj_data) - 1  # exclude header

        print(f"  Trial Balance: {len(tb_data) - 3} accounts  |  Journal: {entry_count} transactions")

        if DRY_RUN:
            print(f"  [DRY RUN] TB tab='{tb_tab}'  TJ tab='{tj_tab}'")
            if entry_count > 0:
                print(f"    Sample TJ row: {tj_data[1]}")
        else:
            ws_tb = ensure_tab(sheet, tb_tab)
            push_tab(ws_tb, tb_data, tb_tab)

            ws_tj = ensure_tab(sheet, tj_tab)
            push_tab(ws_tj, tj_data, tj_tab)

        print()

    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
