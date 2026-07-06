#!/usr/bin/env python3
"""Financial statement suite for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3

import tenancy


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    return con


def trial_balance(slug: str, *, as_of: str | None = None) -> list[dict]:
    con = _book(slug)
    date_clause = "AND e.entry_date <= ?" if as_of else ""
    rows = con.execute(
        f"""SELECT a.code, a.name, a.type,
                   ROUND(COALESCE(SUM(CASE WHEN e.id IS NULL THEN 0 ELSE s.dr END),0),2) AS debit,
                   ROUND(COALESCE(SUM(CASE WHEN e.id IS NULL THEN 0 ELSE s.cr END),0),2) AS credit
            FROM accounts a
            LEFT JOIN splits s ON s.account=a.code
            LEFT JOIN entries e ON e.id=s.entry_id AND e.status='posted' {date_clause}
            GROUP BY a.code, a.name, a.type
            ORDER BY a.code""",
        (as_of,) if as_of else (),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def pnl(slug: str, *, start: str, end: str) -> dict:
    con = _book(slug)
    rows = con.execute(
        """SELECT a.code, a.name, a.type,
                  ROUND(COALESCE(SUM(
                    CASE
                      WHEN e.id IS NULL THEN 0
                      WHEN a.type='income' THEN s.cr - s.dr
                      WHEN a.type='expense' THEN s.dr - s.cr
                      ELSE 0
                    END),0),2) AS amount
           FROM accounts a
           LEFT JOIN splits s ON s.account=a.code
           LEFT JOIN entries e ON e.id=s.entry_id AND e.status='posted'
                AND e.entry_date >= ? AND e.entry_date <= ?
           WHERE a.type IN ('income','expense')
           GROUP BY a.code, a.name, a.type
           ORDER BY a.type, a.code""",
        (start, end),
    ).fetchall()
    con.close()
    lines = [dict(r) for r in rows]
    income = round(sum(r["amount"] for r in lines if r["type"] == "income"), 2)
    expense = round(sum(r["amount"] for r in lines if r["type"] == "expense"), 2)
    return {"start": start, "end": end, "lines": lines, "income": income, "expense": expense, "net_income": round(income - expense, 2)}


def balance_sheet(slug: str, *, as_of: str | None = None) -> dict:
    tb = trial_balance(slug, as_of=as_of)
    totals = {"asset": 0.0, "liability": 0.0, "equity": 0.0, "income": 0.0, "expense": 0.0}
    for row in tb:
        amount = round(float(row["debit"] or 0) - float(row["credit"] or 0), 2)
        if row["type"] in ("liability", "equity", "income"):
            amount = -amount
        totals[row["type"]] = round(totals.get(row["type"], 0.0) + amount, 2)
    retained_income = round(totals["income"] - totals["expense"], 2)
    equity_total = round(totals["equity"] + retained_income, 2)
    return {
        "as_of": as_of,
        "assets": totals["asset"],
        "liabilities": totals["liability"],
        "equity": totals["equity"],
        "retained_income": retained_income,
        "equity_total": equity_total,
        "balanced": round(totals["asset"] - totals["liability"] - equity_total, 2),
    }


def statement_suite(slug: str, *, start: str, end: str, as_of: str | None = None) -> dict:
    return {
        "client_slug": slug,
        "pnl": pnl(slug, start=start, end=end),
        "balance_sheet": balance_sheet(slug, as_of=as_of or end),
        "trial_balance": trial_balance(slug, as_of=as_of or end),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_slug"); parser.add_argument("--start", required=True); parser.add_argument("--end", required=True)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    print(json.dumps(statement_suite(args.client_slug, start=args.start, end=args.end, as_of=args.as_of), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
