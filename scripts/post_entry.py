#!/usr/bin/env python3
"""
post_entry.py — Manual journal entry CLI for the LISZA ledger.

Usage:
  python3 LISZA/scripts/post_entry.py new          Interactive wizard
  python3 LISZA/scripts/post_entry.py pending      List pending_inbox items
  python3 LISZA/scripts/post_entry.py show <id>    Show one pending_inbox item
  python3 LISZA/scripts/post_entry.py approve <id> Approve a pending_inbox item (post it)
  python3 LISZA/scripts/post_entry.py reject <id>  Reject a pending_inbox item
  python3 LISZA/scripts/post_entry.py accounts     List all active accounts
  python3 LISZA/scripts/post_entry.py balances     Show current account balances
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime

from tenancy import resolve_db_path

DB_PATH = str(resolve_db_path(client=os.environ.get("LISZA_CLIENT")))


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def iso_now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def prompt(msg, default=None):
    if default is not None:
        val = input(f"  {msg} [{default}]: ").strip()
        return val if val else default
    return input(f"  {msg}: ").strip()


def confirm(msg):
    return input(f"  {msg} [y/N]: ").strip().lower() == "y"


def print_table(rows, cols):
    if not rows:
        print("  (none)")
        return
    widths = {c: len(c) for c in cols}
    for r in rows:
        for c in cols:
            widths[c] = max(widths[c], len(str(r[c] or "")))
    header = "  " + "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        print("  " + "  ".join(str(r[c] or "").ljust(widths[c]) for c in cols))


def parse_pending_payload(row):
    raw = row["parsed_json"]
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def source_name(source: str) -> str:
    return {
        "bank_import": "bank import",
        "receipt_scanner": "receipt scan",
        "telegram": "telegram",
        "email": "email",
    }.get(source, source)


def _lookup_account(con, code):
    return con.execute(
        "SELECT code, name, sign_normal FROM accounts WHERE code=? AND active=1", (code,)
    ).fetchone()


def _print_split_summary(con, splits):
    for (code, dr, cr, memo) in splits:
        acct = _lookup_account(con, code)
        name = acct["name"] if acct else "(missing account)"
        side = f"DR ${dr:.2f}" if dr else f"CR ${cr:.2f}"
        print(f"    {code} {name:40s} {side}{('  — ' + memo) if memo else ''}")


def cmd_accounts():
    con = get_db()
    rows = con.execute(
        "SELECT code, name, type, sign_normal FROM accounts WHERE active=1 ORDER BY code"
    ).fetchall()
    con.close()
    print(f"\nActive accounts ({len(rows)}):\n")
    print_table(rows, ["code", "name", "type", "sign_normal"])
    print()


def cmd_balances():
    con = get_db()
    rows = con.execute(
        """SELECT code, name, type, total_dr, total_cr, balance
           FROM v_account_balances
           WHERE ABS(balance) > 0.001 OR (total_dr + total_cr) > 0
           ORDER BY type, code"""
    ).fetchall()
    con.close()
    print(f"\nAccount balances (non-zero):\n")
    print_table(rows, ["code", "name", "type", "total_dr", "total_cr", "balance"])
    print()


def cmd_pending():
    con = get_db()
    rows = con.execute(
        """SELECT id, received_at, source, suggested_account, suggested_amount, status, notes
           FROM pending_inbox
           WHERE status IN ('new','reviewed')
           ORDER BY id"""
    ).fetchall()
    con.close()
    if not rows:
        print("\nNo pending items.\n")
        return
    print(f"\nPending inbox ({len(rows)} items):\n")
    print_table(rows, ["id", "received_at", "source", "suggested_account", "suggested_amount", "status"])
    print()


def cmd_show(inbox_id):
    con = get_db()
    row = con.execute("SELECT * FROM pending_inbox WHERE id=?", (inbox_id,)).fetchone()
    if not row:
        print(f"  Pending inbox #{inbox_id} not found.")
        con.close()
        return
    payload = parse_pending_payload(row)
    print(f"\nInbox #{row['id']} — {source_name(row['source'])}")
    print(f"  received_at: {row['received_at']}")
    print(f"  status:      {row['status']}")
    print(f"  raw_path:    {row['raw_path']}")
    print(f"  suggested_account: {row['suggested_account'] or '-'}")
    print(f"  suggested_amount:  {row['suggested_amount'] or '-'}")
    if row["notes"]:
        print(f"  notes:       {row['notes']}")
    if payload:
        keys = [
            "import_kind", "txn_date", "post_date", "description", "amount",
            "direction", "raw_category", "account_label",
            "suggested_payment_account_code", "suggested_debit_account_code",
            "suggested_credit_account_code",
        ]
        for key in keys:
            if key in payload:
                print(f"  {key}: {payload[key]}")
        categorization = payload.get("categorization") or {}
        if categorization:
            print(
                "  categorization: "
                f"{categorization.get('source', '-')}"
                f" confidence={categorization.get('confidence', '-')}"
                f" tax_code={categorization.get('tax_code') or '-'}"
            )
    con.close()
    print()


def _collect_splits(con):
    splits = []
    total_dr = 0.0
    total_cr = 0.0
    print("\n  Enter splits (at least 2, blank account to finish):")
    while True:
        acct_code = input("    Account code (or ? to list): ").strip()
        if not acct_code:
            if len(splits) >= 2:
                break
            print("    Need at least 2 splits.")
            continue
        if acct_code == "?":
            cmd_accounts()
            continue
        acct = _lookup_account(con, acct_code)
        if not acct:
            print(f"    Account '{acct_code}' not found. Try '?' to list.")
            continue
        print(f"    → {acct['code']} {acct['name']} (normal: {acct['sign_normal']})")
        side = input("    Debit or Credit? [d/c]: ").strip().lower()
        if side not in ("d", "c"):
            print("    Enter 'd' or 'c'.")
            continue
        try:
            amount = float(input("    Amount: $").strip())
        except ValueError:
            print("    Invalid amount.")
            continue
        memo = input("    Memo (optional): ").strip() or None
        dr = amount if side == "d" else 0.0
        cr = amount if side == "c" else 0.0
        splits.append((acct_code, dr, cr, memo))
        total_dr += dr
        total_cr += cr
        print(f"    Running: DR ${total_dr:.2f}  CR ${total_cr:.2f}  diff ${total_dr - total_cr:.2f}")
    return splits, total_dr, total_cr


def _collect_bank_import_splits(con, row, payload):
    amount = float(payload.get("amount") or row["suggested_amount"] or 0)
    if amount <= 0:
        raise ValueError("bank import row is missing a positive amount")

    debit_acct = payload.get("suggested_debit_account_code") or row["suggested_account"]
    credit_acct = payload.get("suggested_credit_account_code") or payload.get("suggested_payment_account_code")
    if not debit_acct or not credit_acct:
        raise ValueError("bank import row is missing suggested split accounts")

    description = payload.get("description") or f"Bank import #{row['id']}"
    entry_date = payload.get("txn_date") or payload.get("post_date") or date.today().isoformat()
    payee = payload.get("cardholder") or None

    print("\n  Suggested balanced entry:")
    print(f"    Date:        {entry_date}")
    print(f"    Description: {description}")
    print(f"    Amount:      ${amount:.2f}")
    print(f"    Debit:       {debit_acct}")
    print(f"    Credit:      {credit_acct}")

    if not confirm("  Use these suggested accounts?"):
        debit_acct = prompt("Debit account", default=debit_acct)
        credit_acct = prompt("Credit account", default=credit_acct)
    entry_date = prompt("Date (YYYY-MM-DD)", default=entry_date)
    description = prompt("Description", default=description)
    payee = prompt("Payee (optional)", default=payee or "") or None

    for acct_code in (debit_acct, credit_acct):
        if not _lookup_account(con, acct_code):
            raise ValueError(f"account '{acct_code}' not found")

    splits = [
        (debit_acct, amount, 0.0, description[:40]),
        (credit_acct, 0.0, amount, description[:40]),
    ]
    return entry_date, description, payee, splits


def cmd_new():
    con = get_db()
    print("\n=== New Manual Journal Entry ===\n")
    entry_date = prompt("Date (YYYY-MM-DD)", default=date.today().isoformat())
    description = prompt("Description")
    payee = prompt("Payee (optional)") or None
    notes = prompt("Notes (optional)") or None

    splits, total_dr, total_cr = _collect_splits(con)
    diff = abs(total_dr - total_cr)
    print(f"\n  Total DR: ${total_dr:.2f}  Total CR: ${total_cr:.2f}  Diff: ${diff:.2f}")

    if diff > 0.005:
        print(f"\n  WARNING: Entry is unbalanced by ${diff:.2f}. Cannot post.")
        if not confirm("Save as PENDING anyway (for later correction)?"):
            print("  Aborted.")
            con.close()
            return
        status = "pending"
    else:
        print("\n  Entry is balanced.")
        status = "posted" if confirm("Post immediately (y) or save as pending for review (n)?") else "pending"

    print("\n  === Summary ===")
    print(f"  Date:        {entry_date}")
    print(f"  Description: {description}")
    if payee:
        print(f"  Payee:       {payee}")
    print(f"  Status:      {status}")
    print("  Splits:")
    _print_split_summary(con, splits)

    if not confirm("\nConfirm and save?"):
        print("  Aborted.")
        con.close()
        return

    now = iso_now()
    posted_at = now if status == "posted" else None
    cur = con.execute(
        """INSERT INTO entries (entry_date, description, payee, source, source_ref, status, posted_at, created_at, notes)
           VALUES (?, ?, ?, 'manual', NULL, ?, ?, ?, ?)""",
        (entry_date, description, payee, status, posted_at, now, notes),
    )
    entry_id = cur.lastrowid
    for (code, dr, cr, memo) in splits:
        con.execute(
            "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, ?, ?, ?)",
            (entry_id, code, dr, cr, memo),
        )
    con.commit()
    con.close()
    print(f"\n  Saved as entry #{entry_id} (status: {status}).\n")


def cmd_approve(inbox_id):
    con = get_db()
    row = con.execute("SELECT * FROM pending_inbox WHERE id=?", (inbox_id,)).fetchone()
    if not row:
        print(f"  Pending inbox #{inbox_id} not found.")
        con.close()
        return
    if row["status"] not in ("new", "reviewed"):
        print(f"  Item #{inbox_id} status is '{row['status']}' — cannot approve.")
        con.close()
        return

    payload = parse_pending_payload(row)
    print(f"\n  Inbox #{inbox_id}: {source_name(row['source'])}  amount=${row['suggested_amount']}  acct={row['suggested_account']}")
    if payload:
        print(f"  Parsed: {json.dumps(payload)[:240]}")

    print("\n  To post this item, we need a balanced journal entry.")
    try:
        if row["source"] == "bank_import" and payload.get("import_kind") in ("bank_csv", "statement_pdf"):
            entry_date, description, payee, splits = _collect_bank_import_splits(con, row, payload)
            total_dr = sum(s[1] for s in splits)
            total_cr = sum(s[2] for s in splits)
        else:
            print("  Suggested account:", row["suggested_account"], "| Amount:", row["suggested_amount"])
            entry_date = prompt("Date (YYYY-MM-DD)", default=date.today().isoformat())
            description = prompt("Description", default=f"Inbox #{inbox_id}")
            payee = prompt("Payee (optional)") or None
            splits, total_dr, total_cr = _collect_splits(con)
    except ValueError as exc:
        print(f"  {exc}. Aborting.")
        con.close()
        return

    diff = abs(total_dr - total_cr)
    if diff > 0.005:
        print(f"  Unbalanced by ${diff:.2f}. Aborting.")
        con.close()
        return

    print("\n  Entry summary:")
    print(f"    Date:        {entry_date}")
    print(f"    Description: {description}")
    if payee:
        print(f"    Payee:       {payee}")
    _print_split_summary(con, splits)
    if not confirm("  Confirm and post?"):
        print("  Aborted.")
        con.close()
        return

    now = iso_now()
    cur = con.execute(
        """INSERT INTO entries (entry_date, description, payee, source, source_ref, status, posted_at, created_at, notes)
           VALUES (?, ?, ?, ?, ?, 'posted', ?, ?, ?)""",
        (entry_date, description, payee, row["source"], f"inbox:{inbox_id}", now, now, row["notes"]),
    )
    entry_id = cur.lastrowid
    for (code, dr, cr, memo) in splits:
        con.execute(
            "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, ?, ?, ?)",
            (entry_id, code, dr, cr, memo),
        )
    con.execute(
        "UPDATE pending_inbox SET status='posted', entry_id=? WHERE id=?",
        (entry_id, inbox_id),
    )
    con.commit()
    con.close()
    print(f"\n  Posted as entry #{entry_id}. Inbox #{inbox_id} marked posted.\n")


def post_json(payload: dict, db_path: str) -> dict:
    """Programmatic manual journal entry — the console 'Manual Entry' backend.

    payload = {entry_date, description, payee?, notes?, post: bool, splits: [
        {account, dr, cr, memo?}, ...]}. Writes directly to the target client
    book (no inbox). A balanced entry with post=True is posted immediately;
    otherwise it is saved as 'pending'. Posting an unbalanced entry is refused.
    Raises ValueError on invalid input so callers can surface a JSON error.
    """
    raw_splits = payload.get("splits") or []
    if len(raw_splits) < 2:
        raise ValueError("a journal entry needs at least 2 splits")

    description = (payload.get("description") or "").strip()
    if not description:
        raise ValueError("description is required")

    entry_date = (payload.get("entry_date") or date.today().isoformat()).strip()
    payee = (payload.get("payee") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        splits = []
        total_dr = 0.0
        total_cr = 0.0
        for s in raw_splits:
            code = str(s.get("account") or "").strip()
            if not code:
                raise ValueError("each split needs an account code")
            dr = float(s.get("dr") or 0)
            cr = float(s.get("cr") or 0)
            if dr < 0 or cr < 0:
                raise ValueError(f"split amounts must be non-negative (account {code})")
            if (dr > 0) == (cr > 0):
                raise ValueError(
                    f"split for account {code} must have exactly one of dr/cr")
            if not _lookup_account(con, code):
                raise ValueError(f"account '{code}' not found")
            memo = (str(s.get("memo")).strip() if s.get("memo") else None)
            splits.append((code, dr, cr, memo))
            total_dr += dr
            total_cr += cr

        balanced = abs(total_dr - total_cr) <= 0.005
        want_post = bool(payload.get("post"))
        if want_post and not balanced:
            raise ValueError(
                f"cannot post an unbalanced entry "
                f"(DR {total_dr:.2f} vs CR {total_cr:.2f})")

        status = "posted" if (want_post and balanced) else "pending"
        now = iso_now()
        posted_at = now if status == "posted" else None
        cur = con.execute(
            """INSERT INTO entries
               (entry_date, description, payee, source, source_ref, status, posted_at, created_at, notes)
               VALUES (?, ?, ?, 'manual', NULL, ?, ?, ?, ?)""",
            (entry_date, description, payee, status, posted_at, now, notes),
        )
        entry_id = cur.lastrowid
        for (code, dr, cr, memo) in splits:
            con.execute(
                "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, ?, ?, ?)",
                (entry_id, code, dr, cr, memo),
            )
        con.commit()
        return {
            "entry_id": entry_id,
            "status": status,
            "balanced": balanced,
            "total_dr": round(total_dr, 2),
            "total_cr": round(total_cr, 2),
        }
    finally:
        con.close()


def cmd_reject(inbox_id):
    con = get_db()
    row = con.execute("SELECT id, status FROM pending_inbox WHERE id=?", (inbox_id,)).fetchone()
    if not row:
        print(f"  Pending inbox #{inbox_id} not found.")
    elif row["status"] not in ("new", "reviewed"):
        print(f"  Item #{inbox_id} already has status '{row['status']}'.")
    else:
        con.execute("UPDATE pending_inbox SET status='rejected' WHERE id=?", (inbox_id,))
        con.commit()
        print(f"  Inbox #{inbox_id} marked rejected.")
    con.close()


USAGE = __doc__


def _cli_post_json(argv):
    """`post-json --client <slug> [--payload <json> | stdin]` → JSON result."""
    import argparse
    from pathlib import Path

    from tenancy import resolve_db

    ap = argparse.ArgumentParser(prog="post_entry.py post-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else sys.stdin.read()
        payload = json.loads(raw)
        db_path = str(resolve_db(a.client))
        if not Path(db_path).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        result = post_json(payload, db_path)
        print(json.dumps({"ok": True, **result}))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:  # surface unexpected failures as JSON, never a traceback
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return

    cmd = args[0].lower()
    if cmd == "post-json":
        _cli_post_json(args[1:])
        return
    if cmd == "new":
        cmd_new()
    elif cmd == "pending":
        cmd_pending()
    elif cmd == "show":
        if len(args) < 2:
            print("Usage: post_entry.py show <id>")
            sys.exit(1)
        cmd_show(int(args[1]))
    elif cmd == "approve":
        if len(args) < 2:
            print("Usage: post_entry.py approve <id>")
            sys.exit(1)
        cmd_approve(int(args[1]))
    elif cmd == "reject":
        if len(args) < 2:
            print("Usage: post_entry.py reject <id>")
            sys.exit(1)
        cmd_reject(int(args[1]))
    elif cmd == "accounts":
        cmd_accounts()
    elif cmd == "balances":
        cmd_balances()
    else:
        print(f"Unknown command: {cmd}\n")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
