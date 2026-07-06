#!/usr/bin/env python3
"""Cash management summaries for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

import tenancy

CASH_ACCOUNTS = ("101", "102", "103")


def cash_position(slug: str, *, as_of: str | None = None) -> dict:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    date_clause = "AND e.entry_date <= ?" if as_of else ""
    params = ([as_of] if as_of else []) + list(CASH_ACCOUNTS)
    qmarks = ",".join("?" * len(CASH_ACCOUNTS))
    rows = con.execute(
        f"""SELECT a.code, a.name,
                   ROUND(COALESCE(SUM(s.dr - s.cr), 0), 2) AS balance
            FROM accounts a
            LEFT JOIN splits s ON s.account=a.code
            LEFT JOIN entries e ON e.id=s.entry_id AND e.status='posted' {date_clause}
            WHERE a.code IN ({qmarks})
            GROUP BY a.code, a.name
            ORDER BY a.code""",
        tuple(params),
    ).fetchall()
    ar = con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM invoices WHERE status='open'").fetchone()[0]
    ap = con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM bills WHERE status='unpaid'").fetchone()[0]
    if _table_exists(con, "statement_lines"):
        unreconciled = con.execute(
            """SELECT COUNT(*), ROUND(COALESCE(SUM(amount),0),2)
               FROM statement_lines WHERE status='unmatched'"""
        ).fetchone()
    else:
        unreconciled = (0, 0.0)
    con.close()
    accounts = [dict(r) for r in rows]
    cash_total = round(sum(float(r["balance"] or 0) for r in accounts), 2)
    return {
        "client_slug": slug,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of,
        "cash_accounts": accounts,
        "cash_total": cash_total,
        "open_ar": float(ar or 0),
        "open_ap": float(ap or 0),
        "net_near_term_cash": round(cash_total + float(ar or 0) - float(ap or 0), 2),
        "unreconciled_statement_count": int(unreconciled[0] or 0),
        "unreconciled_statement_amount": float(unreconciled[1] or 0),
    }


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_slug")
    parser.add_argument("--as-of")
    args = parser.parse_args()
    print(json.dumps(cash_position(args.client_slug, as_of=args.as_of), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
