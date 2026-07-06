#!/usr/bin/env python3
"""Billable time tracking for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta

import tenancy

TIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    party TEXT NOT NULL,
    work_date TEXT NOT NULL,
    hours REAL NOT NULL,
    rate REAL NOT NULL,
    description TEXT,
    billable INTEGER NOT NULL DEFAULT 1,
    invoice_id INTEGER REFERENCES invoices(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_time_schema(con: sqlite3.Connection) -> None:
    con.execute(TIME_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_time_schema(con)
    return con


def add_time_entry(
    slug: str,
    *,
    party: str,
    work_date: str,
    hours: float,
    rate: float,
    description: str | None = None,
    billable: bool = True,
) -> int:
    if hours <= 0 or rate < 0:
        raise ValueError("hours must be positive and rate must be non-negative")
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO time_entries(party, work_date, hours, rate, description, billable)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (party, work_date, float(hours), float(rate), description, 1 if billable else 0),
    )
    con.commit()
    con.close()
    return int(cur.lastrowid)


def unbilled_time(slug: str, *, party: str | None = None) -> list[dict]:
    con = _book(slug)
    params: list = []
    where = "WHERE billable=1 AND invoice_id IS NULL"
    if party:
        where += " AND party=?"
        params.append(party)
    rows = con.execute(
        f"""SELECT *, ROUND(hours * rate, 2) AS amount
            FROM time_entries {where}
            ORDER BY work_date, id""",
        params,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def invoice_time(
    slug: str,
    *,
    party: str,
    issue_date: str,
    due_date: str | None = None,
) -> int:
    rows = unbilled_time(slug, party=party)
    if not rows:
        raise ValueError(f"no unbilled billable time for {party}")
    total = round(sum(float(r["amount"] or 0) for r in rows), 2)
    due = due_date or (date.fromisoformat(issue_date) + timedelta(days=30)).isoformat()
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO invoices(party, issue_date, due_date, amount, status, memo)
           VALUES (?, ?, ?, ?, 'open', ?)""",
        (party, issue_date, due, total, f"Billable time: {len(rows)} entries"),
    )
    invoice_id = int(cur.lastrowid)
    ids = [r["id"] for r in rows]
    con.executemany("UPDATE time_entries SET invoice_id=? WHERE id=?", [(invoice_id, row_id) for row_id in ids])
    con.commit()
    con.close()
    return invoice_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("client_slug"); add.add_argument("--party", required=True)
    add.add_argument("--date", required=True); add.add_argument("--hours", type=float, required=True)
    add.add_argument("--rate", type=float, required=True); add.add_argument("--description")
    inv = sub.add_parser("invoice")
    inv.add_argument("client_slug"); inv.add_argument("--party", required=True)
    inv.add_argument("--issue-date", required=True); inv.add_argument("--due-date")
    ls = sub.add_parser("unbilled"); ls.add_argument("client_slug"); ls.add_argument("--party")
    args = parser.parse_args()
    if args.cmd == "add":
        print(json.dumps({"time_entry_id": add_time_entry(
            args.client_slug, party=args.party, work_date=args.date,
            hours=args.hours, rate=args.rate, description=args.description,
        )}))
    elif args.cmd == "invoice":
        print(json.dumps({"invoice_id": invoice_time(
            args.client_slug, party=args.party, issue_date=args.issue_date, due_date=args.due_date,
        )}))
    elif args.cmd == "unbilled":
        print(json.dumps({"time_entries": unbilled_time(args.client_slug, party=args.party)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
