#!/usr/bin/env python3
"""Number-series helpers for LISZA document identifiers.

These helpers are additive and local to one synthetic client book. Previewing a
number is read-only; reserving a number is an explicit write and is intended to
be called only after approval.
"""
from __future__ import annotations

import argparse
import json
import sqlite3

import tenancy

SERIES_DEFAULTS = {
    "invoice": {"prefix": "INV", "next_value": 1, "padding": 5},
    "bill": {"prefix": "BILL", "next_value": 1, "padding": 5},
    "journal": {"prefix": "JRN", "next_value": 1, "padding": 5},
    "payment": {"prefix": "PAY", "next_value": 1, "padding": 5},
}


def ensure_number_series_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS number_series (
            series_key TEXT PRIMARY KEY,
            prefix TEXT NOT NULL,
            next_value INTEGER NOT NULL,
            padding INTEGER NOT NULL DEFAULT 5,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS number_series_reservations (
            reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_key TEXT NOT NULL,
            document_number TEXT NOT NULL UNIQUE,
            document_type TEXT NOT NULL,
            document_id INTEGER,
            status TEXT NOT NULL DEFAULT 'reserved'
                CHECK(status IN ('reserved','assigned','void')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    for key, cfg in SERIES_DEFAULTS.items():
        con.execute(
            """INSERT OR IGNORE INTO number_series(series_key, prefix, next_value, padding)
               VALUES (?, ?, ?, ?)""",
            (key, cfg["prefix"], cfg["next_value"], cfg["padding"]),
        )
    con.commit()


def _connect(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_number_series_schema(con)
    return con


def _format(row: sqlite3.Row, value: int | None = None) -> str:
    number = int(row["next_value"] if value is None else value)
    return f"{row['prefix']}-{number:0{int(row['padding'])}d}"


def series_state(slug: str) -> dict:
    con = _connect(slug)
    try:
        rows = con.execute(
            "SELECT series_key, prefix, next_value, padding FROM number_series ORDER BY series_key"
        ).fetchall()
    finally:
        con.close()
    return {
        row["series_key"]: {
            "prefix": row["prefix"],
            "next_value": row["next_value"],
            "padding": row["padding"],
            "next_number": _format(row),
        }
        for row in rows
    }


def preview_next(slug: str, series_key: str) -> str:
    state = series_state(slug)
    if series_key not in state:
        raise ValueError(f"unknown number series: {series_key}")
    return state[series_key]["next_number"]


def reserve_next(
    slug: str,
    series_key: str,
    *,
    document_type: str | None = None,
    document_id: int | None = None,
    payload: dict | None = None,
) -> dict:
    con = _connect(slug)
    try:
        row = con.execute(
            "SELECT series_key, prefix, next_value, padding FROM number_series WHERE series_key=?",
            (series_key,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown number series: {series_key}")
        document_number = _format(row)
        con.execute(
            """INSERT INTO number_series_reservations
               (series_key, document_number, document_type, document_id, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                series_key,
                document_number,
                document_type or series_key,
                document_id,
                json.dumps(payload or {}, sort_keys=True),
            ),
        )
        con.execute(
            "UPDATE number_series SET next_value=next_value+1, updated_at=datetime('now') WHERE series_key=?",
            (series_key,),
        )
        con.commit()
    finally:
        con.close()
    return {
        "series_key": series_key,
        "document_number": document_number,
        "document_type": document_type or series_key,
        "document_id": document_id,
        "status": "reserved",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_slug")
    parser.add_argument("--series", choices=sorted(SERIES_DEFAULTS), default=None)
    parser.add_argument("--reserve", action="store_true")
    parser.add_argument("--document-id", type=int)
    args = parser.parse_args()
    if args.reserve:
        if not args.series:
            parser.error("--reserve requires --series")
        data = reserve_next(
            args.client_slug,
            args.series,
            document_type=args.series,
            document_id=args.document_id,
        )
    elif args.series:
        data = {"series_key": args.series, "next_number": preview_next(args.client_slug, args.series)}
    else:
        data = series_state(args.client_slug)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
