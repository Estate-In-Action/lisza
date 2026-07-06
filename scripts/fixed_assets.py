#!/usr/bin/env python3
"""Fixed asset register and depreciation schedules for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import tenancy

ASSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS fixed_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    cost REAL NOT NULL,
    salvage_value REAL NOT NULL DEFAULT 0,
    useful_life_months INTEGER NOT NULL,
    asset_account TEXT,
    accumulated_depreciation_account TEXT,
    depreciation_expense_account TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','disposed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_asset_schema(con: sqlite3.Connection) -> None:
    con.execute(ASSET_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_asset_schema(con)
    return con


def add_asset(
    slug: str,
    *,
    name: str,
    purchase_date: str,
    cost: float,
    useful_life_months: int,
    salvage_value: float = 0.0,
    asset_account: str | None = None,
    accumulated_depreciation_account: str | None = None,
    depreciation_expense_account: str | None = None,
) -> int:
    if cost <= 0 or useful_life_months <= 0:
        raise ValueError("cost and useful life must be positive")
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO fixed_assets
           (name, purchase_date, cost, salvage_value, useful_life_months,
            asset_account, accumulated_depreciation_account, depreciation_expense_account)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            purchase_date,
            round(float(cost), 2),
            round(float(salvage_value), 2),
            int(useful_life_months),
            asset_account,
            accumulated_depreciation_account,
            depreciation_expense_account,
        ),
    )
    con.commit()
    con.close()
    return int(cur.lastrowid)


def depreciation_schedule(slug: str, asset_id: int, *, as_of: str | None = None) -> dict:
    con = _book(slug)
    asset = con.execute("SELECT * FROM fixed_assets WHERE id=?", (asset_id,)).fetchone()
    con.close()
    if not asset:
        raise ValueError(f"unknown asset: {asset_id}")
    ref = date.fromisoformat(as_of) if as_of else date.today()
    start = date.fromisoformat(asset["purchase_date"])
    months_elapsed = max(0, (ref.year - start.year) * 12 + (ref.month - start.month) + 1)
    months_used = min(months_elapsed, int(asset["useful_life_months"]))
    depreciable = max(0.0, float(asset["cost"]) - float(asset["salvage_value"] or 0))
    monthly = round(depreciable / int(asset["useful_life_months"]), 2)
    accumulated = round(min(depreciable, monthly * months_used), 2)
    net_book_value = round(float(asset["cost"]) - accumulated, 2)
    return {
        "asset_id": asset_id,
        "name": asset["name"],
        "as_of": ref.isoformat(),
        "method": "straight_line_monthly",
        "monthly_depreciation": monthly,
        "months_elapsed": months_used,
        "accumulated_depreciation": accumulated,
        "net_book_value": net_book_value,
        "status": asset["status"],
    }


def list_assets(slug: str) -> list[dict]:
    con = _book(slug)
    rows = [dict(r) for r in con.execute("SELECT * FROM fixed_assets ORDER BY purchase_date, id")]
    con.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("client_slug"); add.add_argument("--name", required=True)
    add.add_argument("--purchase-date", required=True); add.add_argument("--cost", type=float, required=True)
    add.add_argument("--life-months", type=int, required=True); add.add_argument("--salvage", type=float, default=0)
    dep = sub.add_parser("schedule"); dep.add_argument("client_slug"); dep.add_argument("asset_id", type=int); dep.add_argument("--as-of")
    ls = sub.add_parser("list"); ls.add_argument("client_slug")
    args = parser.parse_args()
    if args.cmd == "add":
        print(json.dumps({"asset_id": add_asset(
            args.client_slug, name=args.name, purchase_date=args.purchase_date,
            cost=args.cost, useful_life_months=args.life_months, salvage_value=args.salvage,
        )}))
    elif args.cmd == "schedule":
        print(json.dumps(depreciation_schedule(args.client_slug, args.asset_id, as_of=args.as_of), indent=2, sort_keys=True))
    elif args.cmd == "list":
        print(json.dumps({"assets": list_assets(args.client_slug)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
