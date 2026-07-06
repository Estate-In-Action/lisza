#!/usr/bin/env python3
"""Inventory and light manufacturing primitives for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3

import tenancy

INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'each',
    standard_cost REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES stock_items(id),
    movement_date TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_cost REAL NOT NULL DEFAULT 0,
    movement_type TEXT NOT NULL
        CHECK(movement_type IN ('receive','consume','adjust','build')),
    reference TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_inventory_schema(con: sqlite3.Connection) -> None:
    con.executescript(INVENTORY_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_inventory_schema(con)
    return con


def upsert_item(slug: str, *, sku: str, name: str, unit: str = "each", standard_cost: float = 0) -> int:
    con = _book(slug)
    con.execute(
        """INSERT INTO stock_items(sku, name, unit, standard_cost)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(sku) DO UPDATE SET
             name=excluded.name, unit=excluded.unit,
             standard_cost=excluded.standard_cost, active=1""",
        (sku, name, unit, float(standard_cost)),
    )
    row = con.execute("SELECT id FROM stock_items WHERE sku=?", (sku,)).fetchone()
    con.commit()
    con.close()
    return int(row["id"])


def record_movement(
    slug: str,
    *,
    sku: str,
    movement_date: str,
    quantity: float,
    movement_type: str,
    unit_cost: float | None = None,
    reference: str | None = None,
) -> int:
    if movement_type not in {"receive", "consume", "adjust", "build"}:
        raise ValueError(f"invalid movement type: {movement_type}")
    con = _book(slug)
    item = con.execute("SELECT id, standard_cost FROM stock_items WHERE sku=? AND active=1", (sku,)).fetchone()
    if not item:
        con.close()
        raise ValueError(f"unknown active sku: {sku}")
    signed_qty = abs(float(quantity)) if movement_type in {"receive", "build"} else -abs(float(quantity))
    cost = float(unit_cost) if unit_cost is not None else float(item["standard_cost"] or 0)
    cur = con.execute(
        """INSERT INTO inventory_movements(item_id, movement_date, quantity, unit_cost, movement_type, reference)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (item["id"], movement_date, signed_qty, cost, movement_type, reference),
    )
    con.commit()
    con.close()
    return int(cur.lastrowid)


def stock_status(slug: str) -> list[dict]:
    con = _book(slug)
    rows = con.execute(
        """SELECT i.sku, i.name, i.unit,
                  ROUND(COALESCE(SUM(m.quantity),0), 4) AS quantity_on_hand,
                  ROUND(COALESCE(SUM(m.quantity * m.unit_cost),0), 2) AS inventory_value
           FROM stock_items i
           LEFT JOIN inventory_movements m ON m.item_id=i.id
           WHERE i.active=1
           GROUP BY i.id
           ORDER BY i.sku"""
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    item = sub.add_parser("item")
    item.add_argument("client_slug"); item.add_argument("--sku", required=True)
    item.add_argument("--name", required=True); item.add_argument("--unit", default="each")
    item.add_argument("--standard-cost", type=float, default=0)
    move = sub.add_parser("move")
    move.add_argument("client_slug"); move.add_argument("--sku", required=True)
    move.add_argument("--date", required=True); move.add_argument("--qty", type=float, required=True)
    move.add_argument("--type", required=True); move.add_argument("--unit-cost", type=float); move.add_argument("--ref")
    status = sub.add_parser("status"); status.add_argument("client_slug")
    args = parser.parse_args()
    if args.cmd == "item":
        print(json.dumps({"item_id": upsert_item(
            args.client_slug, sku=args.sku, name=args.name,
            unit=args.unit, standard_cost=args.standard_cost,
        )}))
    elif args.cmd == "move":
        print(json.dumps({"movement_id": record_movement(
            args.client_slug, sku=args.sku, movement_date=args.date,
            quantity=args.qty, movement_type=args.type, unit_cost=args.unit_cost,
            reference=args.ref,
        )}))
    elif args.cmd == "status":
        print(json.dumps({"stock": stock_status(args.client_slug)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
