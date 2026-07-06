#!/usr/bin/env python3
"""Multi-currency metadata for LISZA client books.

The ledger remains functional-currency accounting. This module records source
currency, FX rate, and translated amount for entries so future realized FX
gain/loss workflows have evidence.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import tenancy

FX_SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rate_date TEXT NOT NULL,
    from_currency TEXT NOT NULL,
    to_currency TEXT NOT NULL DEFAULT 'USD',
    rate REAL NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(rate_date, from_currency, to_currency)
);

CREATE TABLE IF NOT EXISTS entry_currency_meta (
    entry_id INTEGER PRIMARY KEY REFERENCES entries(id),
    source_currency TEXT NOT NULL,
    functional_currency TEXT NOT NULL DEFAULT 'USD',
    source_amount REAL NOT NULL,
    fx_rate REAL NOT NULL,
    functional_amount REAL NOT NULL,
    rate_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_fx_schema(con: sqlite3.Connection) -> None:
    con.executescript(FX_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_fx_schema(con)
    return con


def set_fx_rate(
    slug: str,
    *,
    rate_date: str,
    from_currency: str,
    to_currency: str = "USD",
    rate: float,
    source: str | None = None,
) -> None:
    if rate <= 0:
        raise ValueError("fx rate must be positive")
    con = _book(slug)
    con.execute(
        """INSERT INTO fx_rates(rate_date, from_currency, to_currency, rate, source)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(rate_date, from_currency, to_currency)
           DO UPDATE SET rate=excluded.rate, source=excluded.source""",
        (rate_date, from_currency.upper(), to_currency.upper(), float(rate), source),
    )
    con.commit()
    con.close()


def get_fx_rate(slug: str, *, rate_date: str, from_currency: str, to_currency: str = "USD") -> float:
    con = _book(slug)
    row = con.execute(
        """SELECT rate FROM fx_rates
           WHERE from_currency=? AND to_currency=? AND rate_date <= ?
           ORDER BY rate_date DESC LIMIT 1""",
        (from_currency.upper(), to_currency.upper(), rate_date),
    ).fetchone()
    con.close()
    if not row:
        raise ValueError(f"missing FX rate for {from_currency}->{to_currency} on or before {rate_date}")
    return float(row["rate"])


def annotate_entry_currency(
    slug: str,
    *,
    entry_id: int,
    source_currency: str,
    source_amount: float,
    rate_date: str | None = None,
    functional_currency: str = "USD",
) -> dict:
    con = _book(slug)
    entry = con.execute("SELECT entry_date FROM entries WHERE id=?", (entry_id,)).fetchone()
    con.close()
    if not entry:
        raise ValueError(f"unknown entry: {entry_id}")
    used_rate_date = rate_date or entry["entry_date"] or date.today().isoformat()
    rate = get_fx_rate(
        slug,
        rate_date=used_rate_date,
        from_currency=source_currency,
        to_currency=functional_currency,
    )
    functional_amount = round(float(source_amount) * rate, 2)
    con = _book(slug)
    con.execute(
        """INSERT INTO entry_currency_meta
           (entry_id, source_currency, functional_currency, source_amount,
            fx_rate, functional_amount, rate_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(entry_id) DO UPDATE SET
             source_currency=excluded.source_currency,
             functional_currency=excluded.functional_currency,
             source_amount=excluded.source_amount,
             fx_rate=excluded.fx_rate,
             functional_amount=excluded.functional_amount,
             rate_date=excluded.rate_date""",
        (entry_id, source_currency.upper(), functional_currency.upper(), float(source_amount), rate, functional_amount, used_rate_date),
    )
    con.commit()
    con.close()
    return {
        "entry_id": entry_id,
        "source_currency": source_currency.upper(),
        "functional_currency": functional_currency.upper(),
        "source_amount": float(source_amount),
        "fx_rate": rate,
        "functional_amount": functional_amount,
        "rate_date": used_rate_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    rate = sub.add_parser("rate")
    rate.add_argument("client_slug"); rate.add_argument("--date", required=True)
    rate.add_argument("--from-currency", required=True); rate.add_argument("--to-currency", default="USD")
    rate.add_argument("--rate", type=float, required=True); rate.add_argument("--source")
    ann = sub.add_parser("annotate")
    ann.add_argument("client_slug"); ann.add_argument("entry_id", type=int)
    ann.add_argument("--source-currency", required=True); ann.add_argument("--source-amount", type=float, required=True)
    ann.add_argument("--rate-date")
    args = parser.parse_args()
    if args.cmd == "rate":
        set_fx_rate(args.client_slug, rate_date=args.date, from_currency=args.from_currency,
                    to_currency=args.to_currency, rate=args.rate, source=args.source)
        print(json.dumps({"ok": True}))
    elif args.cmd == "annotate":
        print(json.dumps(annotate_entry_currency(
            args.client_slug,
            entry_id=args.entry_id,
            source_currency=args.source_currency,
            source_amount=args.source_amount,
            rate_date=args.rate_date,
        ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
