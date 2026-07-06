#!/usr/bin/env python3
"""Purchasing pipeline for LISZA client books: purchase order -> vendor bill."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta

import tenancy

PO_STATUSES = ("draft", "sent", "approved", "billed", "cancelled")

PURCHASING_SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL,
    order_date TEXT NOT NULL,
    expected_date TEXT,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','sent','approved','billed','cancelled')),
    bill_id INTEGER REFERENCES bills(id),
    memo TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_purchasing_schema(con: sqlite3.Connection) -> None:
    con.executescript(PURCHASING_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_purchasing_schema(con)
    return con


def create_purchase_order(
    slug: str,
    *,
    vendor: str,
    amount: float,
    order_date: str | None = None,
    expected_date: str | None = None,
    memo: str | None = None,
    status: str = "draft",
) -> int:
    if status not in PO_STATUSES:
        raise ValueError(f"invalid purchase order status: {status}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO purchase_orders(vendor, order_date, expected_date, amount, status, memo)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (vendor, order_date or date.today().isoformat(), expected_date, round(float(amount), 2), status, memo),
    )
    con.commit()
    con.close()
    return int(cur.lastrowid)


def approve_purchase_order(slug: str, po_id: int) -> None:
    con = _book(slug)
    row = con.execute("SELECT status FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError(f"unknown purchase order: {po_id}")
    if row["status"] in ("billed", "cancelled"):
        con.close()
        raise ValueError(f"purchase order is {row['status']}")
    con.execute("UPDATE purchase_orders SET status='approved' WHERE id=?", (po_id,))
    con.commit()
    con.close()


def bill_purchase_order(
    slug: str,
    po_id: int,
    *,
    issue_date: str | None = None,
    due_date: str | None = None,
) -> int:
    con = _book(slug)
    po = con.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not po:
        con.close()
        raise ValueError(f"unknown purchase order: {po_id}")
    if po["status"] not in ("sent", "approved"):
        con.close()
        raise ValueError(f"purchase order is {po['status']}")
    issue = issue_date or date.today().isoformat()
    due = due_date or (date.fromisoformat(issue) + timedelta(days=30)).isoformat()
    cur = con.execute(
        """INSERT INTO bills(party, issue_date, due_date, amount, status, memo)
           VALUES (?, ?, ?, ?, 'unpaid', ?)""",
        (po["vendor"], issue, due, po["amount"], f"Purchase order #{po_id}"),
    )
    bill_id = int(cur.lastrowid)
    con.execute("UPDATE purchase_orders SET status='billed', bill_id=? WHERE id=?", (bill_id, po_id))
    con.commit()
    con.close()
    return bill_id


def list_purchases(slug: str) -> dict:
    con = _book(slug)
    try:
        orders = [dict(r) for r in con.execute("SELECT * FROM purchase_orders ORDER BY id")]
    finally:
        con.close()
    return {"purchase_orders": orders}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    po = sub.add_parser("po")
    po.add_argument("client_slug"); po.add_argument("--vendor", required=True)
    po.add_argument("--amount", type=float, required=True); po.add_argument("--memo")
    ap = sub.add_parser("approve"); ap.add_argument("client_slug"); ap.add_argument("po_id", type=int)
    bill = sub.add_parser("bill"); bill.add_argument("client_slug"); bill.add_argument("po_id", type=int)
    ls = sub.add_parser("list"); ls.add_argument("client_slug")
    args = parser.parse_args()
    if args.cmd == "po":
        print(json.dumps({"purchase_order_id": create_purchase_order(args.client_slug, vendor=args.vendor, amount=args.amount, memo=args.memo)}))
    elif args.cmd == "approve":
        approve_purchase_order(args.client_slug, args.po_id); print(json.dumps({"ok": True}))
    elif args.cmd == "bill":
        print(json.dumps({"bill_id": bill_purchase_order(args.client_slug, args.po_id)}))
    elif args.cmd == "list":
        print(json.dumps(list_purchases(args.client_slug), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
