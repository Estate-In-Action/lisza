#!/usr/bin/env python3
"""Sales pipeline for LISZA client books: quote -> order -> invoice."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta

import tenancy

QUOTE_STATUSES = ("draft", "sent", "accepted", "declined")
ORDER_STATUSES = ("open", "invoiced", "cancelled")

SALES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    party TEXT NOT NULL,
    quote_date TEXT NOT NULL,
    valid_until TEXT,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','sent','accepted','declined')),
    memo TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sales_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER REFERENCES sales_quotes(id),
    party TEXT NOT NULL,
    order_date TEXT NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open','invoiced','cancelled')),
    invoice_id INTEGER REFERENCES invoices(id),
    memo TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_sales_schema(con: sqlite3.Connection) -> None:
    con.executescript(SALES_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_sales_schema(con)
    return con


def create_quote(
    slug: str,
    *,
    party: str,
    quote_date: str | None = None,
    valid_until: str | None = None,
    amount: float,
    memo: str | None = None,
    status: str = "draft",
) -> int:
    if status not in QUOTE_STATUSES:
        raise ValueError(f"invalid quote status: {status}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO sales_quotes(party, quote_date, valid_until, amount, status, memo)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (party, quote_date or date.today().isoformat(), valid_until, round(float(amount), 2), status, memo),
    )
    con.commit()
    con.close()
    return int(cur.lastrowid)


def accept_quote(slug: str, quote_id: int, *, order_date: str | None = None) -> int:
    con = _book(slug)
    quote = con.execute("SELECT * FROM sales_quotes WHERE id=?", (quote_id,)).fetchone()
    if not quote:
        con.close()
        raise ValueError(f"unknown quote: {quote_id}")
    if quote["status"] in ("declined",):
        con.close()
        raise ValueError("declined quotes cannot become orders")
    cur = con.execute(
        """INSERT INTO sales_orders(quote_id, party, order_date, amount, memo)
           VALUES (?, ?, ?, ?, ?)""",
        (quote_id, quote["party"], order_date or date.today().isoformat(), quote["amount"], quote["memo"]),
    )
    order_id = int(cur.lastrowid)
    con.execute("UPDATE sales_quotes SET status='accepted' WHERE id=?", (quote_id,))
    con.commit()
    con.close()
    return order_id


def invoice_order(
    slug: str,
    order_id: int,
    *,
    issue_date: str | None = None,
    due_date: str | None = None,
) -> int:
    con = _book(slug)
    order = con.execute("SELECT * FROM sales_orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        con.close()
        raise ValueError(f"unknown order: {order_id}")
    if order["status"] != "open":
        con.close()
        raise ValueError(f"order is {order['status']}")
    issue = issue_date or date.today().isoformat()
    due = due_date or (date.fromisoformat(issue) + timedelta(days=30)).isoformat()
    cur = con.execute(
        """INSERT INTO invoices(party, issue_date, due_date, amount, status, memo)
           VALUES (?, ?, ?, ?, 'open', ?)""",
        (order["party"], issue, due, order["amount"], f"Sales order #{order_id}"),
    )
    invoice_id = int(cur.lastrowid)
    con.execute(
        "UPDATE sales_orders SET status='invoiced', invoice_id=? WHERE id=?",
        (invoice_id, order_id),
    )
    con.commit()
    con.close()
    return invoice_id


def list_sales(slug: str) -> dict:
    con = _book(slug)
    try:
        quotes = [dict(r) for r in con.execute("SELECT * FROM sales_quotes ORDER BY id")]
        orders = [dict(r) for r in con.execute("SELECT * FROM sales_orders ORDER BY id")]
    finally:
        con.close()
    return {"quotes": quotes, "orders": orders}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quote")
    q.add_argument("client_slug"); q.add_argument("--party", required=True)
    q.add_argument("--amount", type=float, required=True); q.add_argument("--memo")
    a = sub.add_parser("accept"); a.add_argument("client_slug"); a.add_argument("quote_id", type=int)
    i = sub.add_parser("invoice"); i.add_argument("client_slug"); i.add_argument("order_id", type=int)
    ls = sub.add_parser("list"); ls.add_argument("client_slug")
    args = parser.parse_args()
    if args.cmd == "quote":
        print(json.dumps({"quote_id": create_quote(args.client_slug, party=args.party, amount=args.amount, memo=args.memo)}))
    elif args.cmd == "accept":
        print(json.dumps({"order_id": accept_quote(args.client_slug, args.quote_id)}))
    elif args.cmd == "invoice":
        print(json.dumps({"invoice_id": invoice_order(args.client_slug, args.order_id)}))
    elif args.cmd == "list":
        print(json.dumps(list_sales(args.client_slug), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
