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


# Cash movements are classified by the type of the non-cash counterpart account.
_CF_CLASS = {
    "income": "operating",
    "expense": "operating",
    "asset": "investing",
    "liability": "financing",
    "equity": "financing",
}


def cash_flow(slug: str, *, start: str, end: str) -> dict:
    """Direct-method cash flow statement, reconstructed from the cash accounts.

    Each posted entry that moves a cash account is bucketed by the type of its
    non-cash counterpart(s); when an entry has mixed counterparts the cash delta
    is split proportionally by counterpart magnitude. Cash-to-cash transfers net
    to zero across the cash accounts and drop out.
    """
    con = _book(slug)
    cash = tuple(tenancy.CASH_ACCOUNTS)
    placeholders = ",".join("?" * len(cash))
    types = {r["code"]: r["type"] for r in con.execute("SELECT code, type FROM accounts")}

    def cash_balance(date_clause: str, params: tuple) -> float:
        row = con.execute(
            f"""SELECT ROUND(COALESCE(SUM(s.dr - s.cr), 0), 2) AS bal
                FROM splits s JOIN entries e ON e.id = s.entry_id
                WHERE e.status = 'posted' AND s.account IN ({placeholders}) {date_clause}""",
            (*cash, *params),
        ).fetchone()
        return round(float(row["bal"] or 0), 2)

    opening = cash_balance("AND e.entry_date < ?", (start,))
    closing = cash_balance("AND e.entry_date <= ?", (end,))

    entry_ids = [
        r["id"]
        for r in con.execute(
            f"""SELECT DISTINCT e.id
                FROM entries e JOIN splits s ON s.entry_id = e.id
                WHERE e.status = 'posted' AND e.entry_date >= ? AND e.entry_date <= ?
                  AND s.account IN ({placeholders})""",
            (start, end, *cash),
        )
    ]

    buckets = {"operating": 0.0, "investing": 0.0, "financing": 0.0}
    for eid in entry_ids:
        splits = con.execute(
            "SELECT account, dr, cr FROM splits WHERE entry_id=?", (eid,)
        ).fetchall()
        cash_delta = round(
            sum(float(s["dr"]) - float(s["cr"]) for s in splits if s["account"] in cash), 2
        )
        if cash_delta == 0:
            continue
        counter = [s for s in splits if s["account"] not in cash]
        weight = sum(abs(float(s["dr"]) - float(s["cr"])) for s in counter)
        if weight == 0:
            continue
        for s in counter:
            mag = abs(float(s["dr"]) - float(s["cr"]))
            if mag == 0:
                continue
            bucket = _CF_CLASS.get(types.get(s["account"], ""), "operating")
            buckets[bucket] = round(buckets[bucket] + cash_delta * (mag / weight), 2)
    con.close()

    net_change = round(sum(buckets.values()), 2)
    return {
        "start": start,
        "end": end,
        "opening_cash": opening,
        "operating": round(buckets["operating"], 2),
        "investing": round(buckets["investing"], 2),
        "financing": round(buckets["financing"], 2),
        "net_change": net_change,
        "closing_cash": closing,
        "reconciled": round(opening + net_change - closing, 2),
    }


def statement_suite(slug: str, *, start: str, end: str, as_of: str | None = None) -> dict:
    return {
        "client_slug": slug,
        "pnl": pnl(slug, start=start, end=end),
        "balance_sheet": balance_sheet(slug, as_of=as_of or end),
        "cash_flow": cash_flow(slug, start=start, end=end),
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
