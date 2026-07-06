#!/usr/bin/env python3
"""Budget lines and variance reports for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3

import tenancy

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    account TEXT NOT NULL REFERENCES accounts(code),
    amount REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(period_start, period_end, account)
);
"""


def ensure_budget_schema(con: sqlite3.Connection) -> None:
    con.execute(BUDGET_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_budget_schema(con)
    return con


def set_budget_line(
    slug: str,
    *,
    period_start: str,
    period_end: str,
    account: str,
    amount: float,
    notes: str | None = None,
) -> None:
    con = _book(slug)
    exists = con.execute("SELECT 1 FROM accounts WHERE code=?", (account,)).fetchone()
    if not exists:
        con.close()
        raise ValueError(f"unknown account: {account}")
    con.execute(
        """INSERT INTO budget_lines(period_start, period_end, account, amount, notes)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(period_start, period_end, account)
           DO UPDATE SET amount=excluded.amount, notes=excluded.notes""",
        (period_start, period_end, account, round(float(amount), 2), notes),
    )
    con.commit()
    con.close()


def budget_variance(slug: str, *, period_start: str, period_end: str) -> dict:
    con = _book(slug)
    rows = con.execute(
        """SELECT b.account, a.name, a.type, b.amount AS budget,
                  ROUND(COALESCE(SUM(
                    CASE
                      WHEN e.id IS NULL THEN 0
                      WHEN a.type='income' THEN s.cr - s.dr
                      WHEN a.type='expense' THEN s.dr - s.cr
                      ELSE s.dr - s.cr
                    END), 0), 2) AS actual
           FROM budget_lines b
           JOIN accounts a ON a.code=b.account
           LEFT JOIN splits s ON s.account=b.account
           LEFT JOIN entries e ON e.id=s.entry_id AND e.status='posted'
                AND e.entry_date >= b.period_start AND e.entry_date <= b.period_end
           WHERE b.period_start=? AND b.period_end=?
           GROUP BY b.id, b.account, a.name, a.type, b.amount
           ORDER BY b.account""",
        (period_start, period_end),
    ).fetchall()
    con.close()
    lines = []
    for row in rows:
        budget = float(row["budget"] or 0)
        actual = float(row["actual"] or 0)
        delta = round(actual - budget, 2)
        delta_pct = None if abs(budget) < 0.005 else round(delta / abs(budget) * 100, 2)
        lines.append({
            "account": row["account"],
            "name": row["name"],
            "type": row["type"],
            "budget": budget,
            "actual": actual,
            "delta": delta,
            "delta_pct": delta_pct,
        })
    return {
        "client_slug": slug,
        "period_start": period_start,
        "period_end": period_end,
        "lines": lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    setp = sub.add_parser("set")
    setp.add_argument("client_slug"); setp.add_argument("--start", required=True); setp.add_argument("--end", required=True)
    setp.add_argument("--account", required=True); setp.add_argument("--amount", type=float, required=True); setp.add_argument("--notes")
    var = sub.add_parser("variance")
    var.add_argument("client_slug"); var.add_argument("--start", required=True); var.add_argument("--end", required=True)
    args = parser.parse_args()
    if args.cmd == "set":
        set_budget_line(args.client_slug, period_start=args.start, period_end=args.end,
                        account=args.account, amount=args.amount, notes=args.notes)
        print(json.dumps({"ok": True}))
    elif args.cmd == "variance":
        print(json.dumps(budget_variance(args.client_slug, period_start=args.start, period_end=args.end), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
