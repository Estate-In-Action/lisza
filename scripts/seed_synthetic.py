#!/usr/bin/env python3
"""Generate a fully-synthetic demo book for LISZA.

Fake payees, fake amounts, fake dates — ZERO real financial data. Deterministic
(seeded) so the demo ledger is reproducible. Posts balanced double-entry
transactions so balances and reports have something to show.

Run AFTER init_ledger.py:  python3 scripts/seed_synthetic.py
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "ledger.db"
SEED = 42
N_TXNS = 140
START = date(2025, 1, 6)
SPAN_DAYS = 250

# Wholly invented names — any resemblance to real businesses is coincidental.
CLIENTS = ["Maple & Co Consulting", "Riverside Studios LLC", "Northgate Retail",
           "Bluepeak Ventures", "Harborline Design", "Summit Yoga Collective",
           "Greenfield Landscaping", "Tidewater Marketing"]
VENDORS = ["Corner Cafe", "Acme Software Co", "Metro Print Shop", "Cloudhost Inc",
           "Bluebird Office Supplies", "Citywide Internet", "Fleet Fuel Stop",
           "Evergreen Cleaning", "Pinnacle Accounting Tools", "Daily Grind Roasters",
           "Skyline Coworking", "Rapid Courier", "Lumen Utilities", "Apex Insurance"]


def _codes(con, typ):
    return [r[0] for r in con.execute(
        "SELECT code FROM accounts WHERE type=? AND active=1 ORDER BY code", (typ,))]


def _prefer(codes, wanted):
    """Pick `wanted` codes if present, else fall back to the first available."""
    hit = [c for c in wanted if c in codes]
    return hit or codes[:2]


def _entry(con, d, desc, payee):
    cur = con.execute(
        "INSERT INTO entries(entry_date, description, payee, source, status) "
        "VALUES(?,?,?,?, 'pending')", (d.isoformat(), desc, payee, "synthetic"))
    return cur.lastrowid


def _split(con, eid, account, *, dr=0.0, cr=0.0):
    con.execute("INSERT INTO splits(entry_id, account, dr, cr) VALUES(?,?,?,?)",
                (eid, account, dr, cr))


def _post(con, eid):
    # Flips status to posted; the schema trigger rejects it unless dr == cr.
    con.execute("UPDATE entries SET status='posted', posted_at=datetime('now') WHERE id=?", (eid,))


def main() -> int:
    if not DB.exists():
        print(f"ERROR: {DB} missing — run init_ledger.py first")
        return 1
    rng = random.Random(SEED)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")

    cash = _prefer(_codes(con, "asset"), ["102", "101", "103"])      # checking / cash / savings
    cards = _prefer(_codes(con, "liability"), ["210", "211"])         # credit cards
    income = _prefer(_codes(con, "income"), ["430", "431", "400"])    # contractor / employment
    expense = _codes(con, "expense")
    if not (cash and income and expense):
        print("ERROR: chart of accounts missing asset/income/expense codes")
        return 1

    posted = 0
    for _ in range(N_TXNS):
        d = START + timedelta(days=rng.randint(0, SPAN_DAYS))
        if rng.random() < 0.28:
            # Client payment: debit cash, credit income.
            amt = round(rng.uniform(450, 6200), 2)
            payee = rng.choice(CLIENTS)
            eid = _entry(con, d, f"Invoice payment — {payee}", payee)
            _split(con, eid, rng.choice(cash), dr=amt)
            _split(con, eid, rng.choice(income), cr=amt)
        else:
            # Business expense: debit expense, credit cash or a card.
            amt = round(rng.uniform(6, 850), 2)
            payee = rng.choice(VENDORS)
            eid = _entry(con, d, payee, payee)
            _split(con, eid, rng.choice(expense), dr=amt)
            _split(con, eid, rng.choice(cash + cards), cr=amt)
        _post(con, eid)
        posted += 1

    # A few auto-categorization rules over the synthetic vendors.
    rules = [("Corner Cafe", "582"), ("Acme Software", "550"),
             ("Metro Print", "580"), ("Cloudhost", "550"), ("Lumen Utilities", "550")]
    for pat, acct in rules:
        if acct in expense:
            con.execute("INSERT INTO payee_rules(pattern, account_code) VALUES(?,?)", (pat, acct))

    con.commit()
    n_entries = con.execute("SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
    bal = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    con.close()
    print(f"seeded {posted} synthetic transactions; {n_entries} posted entries; "
          f"splits dr={bal[0]} cr={bal[1]} (must be equal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
