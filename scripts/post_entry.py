#!/usr/bin/env python3
"""
post_entry.py — Manual journal entry CLI for the LISZA ledger.

Usage:
  python3 LISZA/scripts/post_entry.py new          Interactive wizard
  python3 LISZA/scripts/post_entry.py pending      List pending_inbox items
  python3 LISZA/scripts/post_entry.py approve <id> Approve a pending_inbox item (post it)
  python3 LISZA/scripts/post_entry.py reject  <id> Reject a pending_inbox item
  python3 LISZA/scripts/post_entry.py accounts     List all active accounts
  python3 LISZA/scripts/post_entry.py balances     Show current account balances

Environment:
  LISZA_DB  Path to ledger.db (default: LISZA/ledger.db)
"""

import os
import sys
import sqlite3
import json
from datetime import date, datetime

DB_PATH = os.environ.get("LISZA_DB", "LISZA/ledger.db")


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


# ---------------------------------------------------------------------------
# Helpers

def iso_now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def prompt(msg, default=None):
    if default:
        val = input(f"  {msg} [{default}]: ").strip()
        return val if val else default
    val = input(f"  {msg}: ").strip()
    return val


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


# ---------------------------------------------------------------------------
# Commands

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


def _lookup_account(con, code):
    row = con.execute(
        "SELECT code, name, sign_normal FROM accounts WHERE code=? AND active=1", (code,)
    ).fetchone()
    return row


def _collect_splits(con):
    """Interactive split entry. Returns list of (account_code, dr, cr, memo) tuples."""
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
        post_now = confirm("Post immediately (y) or save as pending for review (n)?")
        status = "posted" if post_now else "pending"

    print("\n  === Summary ===")
    print(f"  Date:        {entry_date}")
    print(f"  Description: {description}")
    if payee:
        print(f"  Payee:       {payee}")
    print(f"  Status:      {status}")
    print(f"  Splits:")
    for (code, dr, cr, memo) in splits:
        acct = _lookup_account(con, code)
        side = f"DR ${dr:.2f}" if dr else f"CR ${cr:.2f}"
        print(f"    {code} {acct['name']:40s} {side}{('  — ' + memo) if memo else ''}")

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

    print(f"\n  Inbox #{inbox_id}: {row['source']}  amount=${row['suggested_amount']}  acct={row['suggested_account']}")
    parsed = row["parsed_json"]
    if parsed:
        print(f"  Parsed: {parsed[:200]}")

    print("\n  To post this item, we need a balanced journal entry.")
    print("  Suggested account:", row["suggested_account"], "| Amount:", row["suggested_amount"])
    entry_date = prompt("Date (YYYY-MM-DD)", default=date.today().isoformat())
    description = prompt("Description", default=f"Inbox #{inbox_id}")
    payee = prompt("Payee (optional)") or None

    splits, total_dr, total_cr = _collect_splits(con)
    diff = abs(total_dr - total_cr)
    if diff > 0.005:
        print(f"  Unbalanced by ${diff:.2f}. Aborting.")
        con.close()
        return

    now = iso_now()
    cur = con.execute(
        """INSERT INTO entries (entry_date, description, payee, source, source_ref, status, posted_at, created_at)
           VALUES (?, ?, ?, 'telegram', ?, 'posted', ?, ?)""",
        (entry_date, description, payee, f"inbox:{inbox_id}", now, now),
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


# ---------------------------------------------------------------------------
# Entry point

USAGE = __doc__


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return

    cmd = args[0].lower()

    if cmd == "new":
        cmd_new()
    elif cmd == "pending":
        cmd_pending()
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
