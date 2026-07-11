#!/usr/bin/env python3
"""Payment application / reconciliation for LISZA client books.

Closes the money loop: an invoice/bill lives in its sub-ledger as 'open'/'unpaid'
until a payment is *recorded and applied* here. Applying a payment:

  1. posts the balanced cash journal to the GL via post_entry.post_json
       receipt      → Dr <cash account>   / Cr 110 Accounts Receivable
       disbursement → Dr 200 Accounts Payable / Cr <cash account>
  2. records the payment + its allocations across one or more invoices/bills
  3. flips a target to 'paid' (with paid_date) once its running allocation
     covers the amount; partial allocations leave it open with a lower balance.

The GL entry always posts the *full* cash amount to the control account, so an
over-payment (unapplied cash) sits as a customer/vendor credit in AR/AP — the
standard on-account treatment.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import tenancy
from post_entry import post_json

AR_CONTROL = "110"   # Accounts Receivable
AP_CONTROL = "200"   # Accounts Payable
DEFAULT_CASH = "102"  # Business Checking

_TARGET = {
    "receipt": {
        "table": "invoices",
        "target_type": "invoice",
        "open_status": "open",
        "control": AR_CONTROL,
    },
    "disbursement": {
        "table": "bills",
        "target_type": "bill",
        "open_status": "unpaid",
        "control": AP_CONTROL,
    },
}

PAYMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK(direction IN ('receipt','disbursement')),
    party TEXT,
    payment_date TEXT NOT NULL,
    amount REAL NOT NULL,
    method TEXT,
    cash_account TEXT NOT NULL REFERENCES accounts(code),
    reference TEXT,
    memo TEXT,
    entry_id INTEGER REFERENCES entries(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK(target_type IN ('invoice','bill')),
    target_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_payalloc_payment ON payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_payalloc_target
    ON payment_allocations(target_type, target_id);
"""


def ensure_payments_schema(con: sqlite3.Connection) -> None:
    con.executescript(PAYMENTS_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_payments_schema(con)
    return con


def _allocated_so_far(con: sqlite3.Connection, target_type: str, target_id: int) -> float:
    row = con.execute(
        """SELECT COALESCE(SUM(amount), 0) FROM payment_allocations
           WHERE target_type=? AND target_id=?""",
        (target_type, target_id),
    ).fetchone()
    return round(float(row[0] or 0), 2)


def target_balance(slug: str, target_type: str, target_id: int) -> float:
    """Remaining unpaid balance on an invoice/bill (amount minus allocations)."""
    table = "invoices" if target_type == "invoice" else "bills"
    con = _book(slug)
    try:
        row = con.execute(f"SELECT amount FROM {table} WHERE id=?", (target_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown {target_type}: {target_id}")
        allocated = _allocated_so_far(con, target_type, target_id)
        return round(float(row["amount"]) - allocated, 2)
    finally:
        con.close()


def open_items(slug: str, direction: str) -> list[dict]:
    """Open receivables (receipt) or payables (disbursement), with balances."""
    spec = _TARGET.get(direction)
    if not spec:
        raise ValueError(f"invalid direction: {direction}")
    con = _book(slug)
    try:
        rows = con.execute(
            f"""SELECT id, party, due_date, amount FROM {spec['table']}
                WHERE status=? ORDER BY due_date, id""",
            (spec["open_status"],),
        ).fetchall()
        items = []
        for r in rows:
            allocated = _allocated_so_far(con, spec["target_type"], r["id"])
            items.append({
                "target_type": spec["target_type"],
                "target_id": r["id"],
                "party": r["party"],
                "due_date": r["due_date"],
                "amount": round(float(r["amount"]), 2),
                "allocated": allocated,
                "balance": round(float(r["amount"]) - allocated, 2),
            })
        return items
    finally:
        con.close()


def record_payment(
    slug: str,
    *,
    direction: str,
    amount: float,
    payment_date: str | None = None,
    party: str | None = None,
    method: str | None = None,
    cash_account: str | None = None,
    reference: str | None = None,
    memo: str | None = None,
    allocations: list[dict] | None = None,
) -> dict:
    """Record a payment, apply it to invoices/bills, and post the cash journal.

    allocations: [{"target_id": int, "amount": float}, ...] against open
    invoices (receipt) or unpaid bills (disbursement). Sum of allocations may be
    less than `amount` (rest is unapplied on-account credit) but never more.
    """
    spec = _TARGET.get(direction)
    if not spec:
        raise ValueError(f"invalid direction: {direction!r} (receipt|disbursement)")

    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("payment amount must be positive")

    cash_account = cash_account or DEFAULT_CASH
    pay_date = payment_date or date.today().isoformat()
    allocations = allocations or []

    con = _book(slug)
    try:
        if not con.execute(
            "SELECT 1 FROM accounts WHERE code=? AND active=1", (cash_account,)
        ).fetchone():
            raise ValueError(f"cash account '{cash_account}' not found")

        # Validate each allocation against its target's remaining balance.
        norm_allocs: list[tuple[int, float]] = []
        alloc_total = 0.0
        for a in allocations:
            tid = int(a["target_id"])
            amt = round(float(a["amount"]), 2)
            if amt <= 0:
                raise ValueError("allocation amount must be positive")
            row = con.execute(
                f"SELECT amount, status FROM {spec['table']} WHERE id=?", (tid,)
            ).fetchone()
            if not row:
                raise ValueError(
                    f"{spec['target_type']} {tid} not found for a {direction}")
            if row["status"] != spec["open_status"]:
                raise ValueError(
                    f"{spec['target_type']} {tid} is {row['status']}, not "
                    f"{spec['open_status']}")
            bal = round(float(row["amount"]) - _allocated_so_far(
                con, spec["target_type"], tid), 2)
            if amt > bal + 0.005:
                raise ValueError(
                    f"allocation ${amt:.2f} exceeds {spec['target_type']} {tid} "
                    f"balance ${bal:.2f}")
            norm_allocs.append((tid, amt))
            alloc_total += amt

        alloc_total = round(alloc_total, 2)
        if alloc_total > amount + 0.005:
            raise ValueError(
                f"allocations ${alloc_total:.2f} exceeds payment amount "
                f"${amount:.2f}")

        # Post the balanced cash journal to the GL (full amount to control acct).
        if direction == "receipt":
            splits = [
                {"account": cash_account, "dr": amount, "cr": 0, "memo": "receipt"},
                {"account": spec["control"], "dr": 0, "cr": amount, "memo": "A/R"},
            ]
        else:
            splits = [
                {"account": spec["control"], "dr": amount, "cr": 0, "memo": "A/P"},
                {"account": cash_account, "dr": 0, "cr": amount, "memo": "disbursement"},
            ]
        desc = memo or (
            f"{'Receipt from' if direction == 'receipt' else 'Payment to'} "
            f"{party or 'party'}")
        posted = post_json(
            {
                "entry_date": pay_date,
                "description": desc,
                "payee": party,
                "post": True,
                "splits": splits,
            },
            str(tenancy.resolve_db(slug)),
        )
        entry_id = posted["entry_id"]

        cur = con.execute(
            """INSERT INTO payments
               (direction, party, payment_date, amount, method, cash_account,
                reference, memo, entry_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (direction, party, pay_date, amount, method, cash_account,
             reference, memo, entry_id),
        )
        payment_id = cur.lastrowid

        targets_paid = []
        for tid, amt in norm_allocs:
            con.execute(
                """INSERT INTO payment_allocations
                   (payment_id, target_type, target_id, amount)
                   VALUES (?,?,?,?)""",
                (payment_id, spec["target_type"], tid, amt),
            )
            total = _allocated_so_far(con, spec["target_type"], tid)
            orig = con.execute(
                f"SELECT amount FROM {spec['table']} WHERE id=?", (tid,)
            ).fetchone()["amount"]
            if total >= round(float(orig), 2) - 0.005:
                con.execute(
                    f"UPDATE {spec['table']} SET status='paid', paid_date=? WHERE id=?",
                    (pay_date, tid),
                )
                targets_paid.append(tid)

        con.commit()
        return {
            "payment_id": payment_id,
            "entry_id": entry_id,
            "status": posted["status"],
            "amount": amount,
            "allocated": alloc_total,
            "unapplied": round(amount - alloc_total, 2),
            "targets_paid": targets_paid,
        }
    finally:
        con.close()


def list_payments(slug: str) -> list[dict]:
    con = _book(slug)
    try:
        rows = con.execute(
            """SELECT p.*, (
                   SELECT COALESCE(SUM(amount),0) FROM payment_allocations
                   WHERE payment_id=p.id) AS allocated
               FROM payments p ORDER BY p.payment_date, p.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open", help="list open receivables/payables")
    o.add_argument("client_slug")
    o.add_argument("--direction", choices=["receipt", "disbursement"], default="receipt")

    r = sub.add_parser("pay", help="record and apply a payment")
    r.add_argument("client_slug")
    r.add_argument("--direction", choices=["receipt", "disbursement"], required=True)
    r.add_argument("--amount", type=float, required=True)
    r.add_argument("--date")
    r.add_argument("--party")
    r.add_argument("--method")
    r.add_argument("--cash-account")
    r.add_argument("--reference")
    r.add_argument("--memo")
    r.add_argument("--allocations", help='JSON: [{"target_id":1,"amount":100.0}]')

    ls = sub.add_parser("list", help="list recorded payments")
    ls.add_argument("client_slug")

    args = ap.parse_args()
    if args.cmd == "open":
        print(json.dumps(open_items(args.client_slug, args.direction), indent=2))
    elif args.cmd == "pay":
        allocs = json.loads(args.allocations) if args.allocations else []
        res = record_payment(
            args.client_slug, direction=args.direction, amount=args.amount,
            payment_date=args.date, party=args.party, method=args.method,
            cash_account=args.cash_account, reference=args.reference,
            memo=args.memo, allocations=allocs)
        print(json.dumps(res, indent=2))
    elif args.cmd == "list":
        print(json.dumps(list_payments(args.client_slug), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
