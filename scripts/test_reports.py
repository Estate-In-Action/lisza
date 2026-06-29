#!/usr/bin/env python3
"""Accounting-invariant tests for the LISZA synthetic ledger.

The report routes (/api/lisza/report) compute Balance Sheet / P&L / AR-AP aging /
General Ledger with the same SQL these tests use. If the ledger satisfies the
double-entry invariants below, the reports built on it tie out. Run after seeding:

    python3 scripts/init_ledger.py && python3 scripts/seed_synthetic.py
    python3 -m pytest scripts/test_reports.py -q
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

DB = Path(os.environ.get("LISZA_DB",
          str(Path(__file__).resolve().parent.parent / "clients" / "guitar-works" / "ledger.db")))
AS_OF = "2026-12-31"   # past the data end → captures the whole book


@pytest.fixture(scope="module")
def con():
    assert DB.exists(), "ledger.db missing — run init_ledger.py + seed_synthetic.py first"
    c = sqlite3.connect(DB)
    yield c
    c.close()


def _bal(con, typ, as_of=AS_OF):
    return con.execute("""
        SELECT ROUND(COALESCE(SUM(CASE WHEN a.sign_normal='debit' THEN s.dr-s.cr ELSE s.cr-s.dr END),0),2)
        FROM accounts a
        LEFT JOIN splits s ON s.account=a.code
        LEFT JOIN entries e ON e.id=s.entry_id AND e.status='posted' AND e.entry_date<=?
        WHERE a.type=?""", (as_of, typ)).fetchone()[0]


def test_every_posted_entry_balances(con):
    bad = con.execute("""
        SELECT e.id, ROUND(SUM(s.dr)-SUM(s.cr),2) AS diff
        FROM entries e JOIN splits s ON s.entry_id=e.id
        WHERE e.status='posted' GROUP BY e.id HAVING ABS(diff) > 0.005""").fetchall()
    assert bad == [], f"unbalanced entries: {bad[:5]}"


def test_balance_sheet_balances(con):
    assets = _bal(con, "asset")
    liab = _bal(con, "liability")
    equity_accts = _bal(con, "equity")
    net_income = _bal(con, "income") - _bal(con, "expense")  # income(cr-dr) − expense(dr-cr)
    equity = round(equity_accts + net_income, 2)
    assert abs(assets - (liab + equity)) < 0.01, f"A={assets} L={liab} E={equity}"


def test_ar_aging_ties_to_ar_account(con):
    ar_acct = con.execute("""
        SELECT ROUND(COALESCE(SUM(s.dr-s.cr),0),2) FROM splits s
        JOIN entries e ON e.id=s.entry_id AND e.status='posted' WHERE s.account='110'""").fetchone()[0]
    open_inv = con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM invoices WHERE status='open'").fetchone()[0]
    assert abs(ar_acct - open_inv) < 0.01, f"AR acct {ar_acct} != open invoices {open_inv}"


def test_ap_aging_ties_to_ap_account(con):
    ap_acct = con.execute("""
        SELECT ROUND(COALESCE(SUM(s.cr-s.dr),0),2) FROM splits s
        JOIN entries e ON e.id=s.entry_id AND e.status='posted' WHERE s.account='200'""").fetchone()[0]
    unpaid = con.execute("SELECT ROUND(COALESCE(SUM(amount),0),2) FROM bills WHERE status='unpaid'").fetchone()[0]
    assert abs(ap_acct - unpaid) < 0.01, f"AP acct {ap_acct} != unpaid bills {unpaid}"


def test_general_ledger_total_debits_equal_credits(con):
    dr, cr = con.execute("""
        SELECT ROUND(SUM(s.dr),2), ROUND(SUM(s.cr),2) FROM splits s
        JOIN entries e ON e.id=s.entry_id AND e.status='posted'""").fetchone()
    assert abs(dr - cr) < 0.01, f"GL debits {dr} != credits {cr}"


def test_multi_year_span(con):
    lo, hi = con.execute("SELECT MIN(entry_date), MAX(entry_date) FROM entries WHERE status='posted'").fetchone()
    assert lo < "2024-01-01" and hi >= "2026-01-01", f"span {lo}..{hi} not multi-year"
