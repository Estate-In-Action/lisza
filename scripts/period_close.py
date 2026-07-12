#!/usr/bin/env python3
"""Period / fiscal-year closing for LISZA client books.

Income and expense accounts are *temporary* — they accumulate a period's
activity and must reset to zero so the next period starts clean. Closing a
period:

  1. sums each posted income/expense account over [start, end]
  2. posts one balanced closing entry that zeroes every P&L account
       Dr <each income account>   (their credit balance)
       Cr <each expense account>  (their debit balance)
       and plugs the difference (net income) to retained earnings —
       Cr equity on a profit, Dr equity on a loss
  3. records the period in `accounting_periods` and *locks* it: once a period
     is closed, post_entry.post_json refuses to post any entry dated on or
     before that period's end (corrections go in the next open period, or the
     period is reopened first).

Retained earnings defaults to COA 310 (new books) and falls back to 300
Owner's Equity for books seeded before 310 existed. Either way the offset
account is type-checked to be equity.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import tenancy
from post_entry import post_json

RETAINED_EARNINGS = ("310", "300")  # preferred, fallback
_CENT = 0.005

PERIOD_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounting_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'closed' CHECK(status IN ('open','closed')),
    net_income REAL,
    retained_account TEXT,
    entry_id INTEGER REFERENCES entries(id),
    closed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_period_schema(con: sqlite3.Connection) -> None:
    con.executescript(PERIOD_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_period_schema(con)
    return con


def _pnl_balances(con: sqlite3.Connection, start: str, end: str) -> tuple[list, list]:
    """Per-account posted P&L balances over [start, end].

    Returns (income, expense) lists of {code, name, balance}. `balance` is the
    account's natural balance: credit-positive for income, debit-positive for
    expense (so a normal profit yields positive numbers on both sides).
    """
    rows = con.execute(
        """SELECT a.code, a.name, a.type,
                  COALESCE(SUM(s.dr), 0) AS dr, COALESCE(SUM(s.cr), 0) AS cr
             FROM splits s
             JOIN entries e ON e.id = s.entry_id
             JOIN accounts a ON a.code = s.account
            WHERE e.status = 'posted'
              AND e.entry_date BETWEEN ? AND ?
              AND a.type IN ('income', 'expense')
            GROUP BY a.code, a.name, a.type
            ORDER BY a.code""",
        (start, end),
    ).fetchall()
    income, expense = [], []
    for r in rows:
        if r["type"] == "income":
            bal = round(float(r["cr"]) - float(r["dr"]), 2)   # credit-positive
            if abs(bal) >= _CENT:
                income.append({"code": r["code"], "name": r["name"], "balance": bal})
        else:
            bal = round(float(r["dr"]) - float(r["cr"]), 2)   # debit-positive
            if abs(bal) >= _CENT:
                expense.append({"code": r["code"], "name": r["name"], "balance": bal})
    return income, expense


def income_summary(slug: str, *, start: str, end: str) -> dict:
    """Read-only P&L summary for the period (no posting)."""
    con = _book(slug)
    try:
        income, expense = _pnl_balances(con, start, end)
        total_income = round(sum(a["balance"] for a in income), 2)
        total_expense = round(sum(a["balance"] for a in expense), 2)
        return {
            "start": start, "end": end,
            "income": income, "expense": expense,
            "total_income": total_income,
            "total_expense": total_expense,
            "net_income": round(total_income - total_expense, 2),
        }
    finally:
        con.close()


def _resolve_retained(con: sqlite3.Connection, retained_account: str | None) -> str:
    if retained_account:
        row = con.execute(
            "SELECT type FROM accounts WHERE code=? AND active=1", (retained_account,)
        ).fetchone()
        if not row:
            raise ValueError(f"retained-earnings account '{retained_account}' not found")
        if row["type"] != "equity":
            raise ValueError(
                f"account '{retained_account}' is {row['type']}, not equity")
        return retained_account
    for code in RETAINED_EARNINGS:
        row = con.execute(
            "SELECT type FROM accounts WHERE code=? AND active=1", (code,)
        ).fetchone()
        if row and row["type"] == "equity":
            return code
    raise ValueError("no equity account found for retained earnings")


def is_period_locked(slug: str, entry_date: str) -> str | None:
    """Return the period_end that locks `entry_date`, or None if it's open."""
    con = _book(slug)
    try:
        row = con.execute(
            "SELECT MAX(period_end) AS pe FROM accounting_periods "
            "WHERE status='closed' AND period_end >= ?", (entry_date,)
        ).fetchone()
        return row["pe"] if row and row["pe"] else None
    finally:
        con.close()


def close_period(slug: str, *, start: str, end: str,
                 retained_account: str | None = None,
                 close_date: str | None = None, memo: str | None = None) -> dict:
    """Post the closing entry for [start, end] and lock the period."""
    close_on = close_date or end
    con = _book(slug)
    try:
        if con.execute(
            "SELECT 1 FROM accounting_periods WHERE period_end=? AND status='closed'",
            (end,),
        ).fetchone():
            raise ValueError(f"period ending {end} is already closed")

        retained = _resolve_retained(con, retained_account)
        income, expense = _pnl_balances(con, start, end)
        if not income and not expense:
            raise ValueError(f"no income or expense activity in {start}..{end} to close")

        splits = []
        total_income = 0.0
        total_expense = 0.0
        for a in income:                       # credit-positive → debit to zero
            if a["balance"] >= 0:
                splits.append({"account": a["code"], "dr": a["balance"], "cr": 0,
                               "memo": "close income"})
            else:
                splits.append({"account": a["code"], "dr": 0, "cr": -a["balance"],
                               "memo": "close income"})
            total_income += a["balance"]
        for a in expense:                      # debit-positive → credit to zero
            if a["balance"] >= 0:
                splits.append({"account": a["code"], "dr": 0, "cr": a["balance"],
                               "memo": "close expense"})
            else:
                splits.append({"account": a["code"], "dr": -a["balance"], "cr": 0,
                               "memo": "close expense"})
            total_expense += a["balance"]

        net_income = round(total_income - total_expense, 2)
        if net_income > 0:                     # profit credits equity
            splits.append({"account": retained, "dr": 0, "cr": net_income,
                           "memo": "net income to retained earnings"})
        elif net_income < 0:                   # loss debits equity
            splits.append({"account": retained, "dr": -net_income, "cr": 0,
                           "memo": "net loss to retained earnings"})

        # Post the closing entry on a separate connection BEFORE opening this
        # book's write txn (WAL: avoids self-deadlock on the same file). The
        # period is not locked yet, so the closing entry — dated at period end —
        # is allowed to post.
        posted = post_json(
            {
                "entry_date": close_on,
                "description": memo or f"Closing entry — period ending {end}",
                "post": True,
                "splits": splits,
            },
            str(tenancy.resolve_db(slug)),
        )
        entry_id = posted["entry_id"]

        con.execute(
            "INSERT INTO accounting_periods"
            "(period_start, period_end, status, net_income, retained_account, entry_id)"
            " VALUES (?,?, 'closed', ?, ?, ?)",
            (start, end, net_income, retained, entry_id),
        )
        con.commit()
        return {
            "period_start": start, "period_end": end, "status": "closed",
            "entry_id": entry_id, "net_income": net_income,
            "retained_account": retained,
            "closed_income": round(total_income, 2),
            "closed_expense": round(total_expense, 2),
        }
    finally:
        con.close()


def list_periods(slug: str) -> list[dict]:
    con = _book(slug)
    try:
        return [dict(r) for r in con.execute(
            "SELECT period_start, period_end, status, net_income, retained_account,"
            " entry_id, closed_at FROM accounting_periods ORDER BY period_end DESC")]
    finally:
        con.close()


# ---- CLI ----------------------------------------------------------------

def _cli_close_json(argv) -> None:
    """`close-json --client <slug> --payload <json>` → JSON result.

    payload = {"action": "close"|"summary"|"periods", ...}
    """
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="period_close.py close-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else __import__("sys").stdin.read()
        p = json.loads(raw)
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        action = p.get("action") or "summary"
        if action == "summary":
            print(json.dumps({"ok": True, **income_summary(
                a.client, start=p["start"], end=p["end"])}))
        elif action == "close":
            print(json.dumps({"ok": True, **close_period(
                a.client, start=p["start"], end=p["end"],
                retained_account=p.get("retained_account"),
                close_date=p.get("close_date"), memo=p.get("memo"))}))
        elif action == "periods":
            print(json.dumps({"ok": True, "periods": list_periods(a.client)}))
        else:
            print(json.dumps({"error": f"unknown action: {action}"}))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main(argv=None) -> None:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "close-json":
        _cli_close_json(argv[1:])
        return
    print("usage: period_close.py close-json --client <slug> --payload <json>")


if __name__ == "__main__":
    main()
