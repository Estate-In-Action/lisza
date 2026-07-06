#!/usr/bin/env python3
"""Custom report artifacts for LISZA client books."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tenancy


def _pnl_by_account(con: sqlite3.Connection, start: str, end: str) -> dict[str, dict]:
    rows = con.execute(
        """SELECT a.code, a.name, a.type,
                  ROUND(COALESCE(SUM(
                    CASE
                      WHEN e.id IS NULL THEN 0
                      WHEN a.type='income' THEN s.cr - s.dr
                      WHEN a.type='expense' THEN s.dr - s.cr
                      ELSE 0
                    END), 0), 2) AS amount
           FROM accounts a
           LEFT JOIN splits s ON s.account=a.code
           LEFT JOIN entries e ON e.id=s.entry_id
                AND e.status='posted'
                AND e.entry_date >= ?
                AND e.entry_date <= ?
           WHERE a.type IN ('income', 'expense')
           GROUP BY a.code, a.name, a.type
           ORDER BY a.type, a.code""",
        (start, end),
    ).fetchall()
    return {
        row["code"]: {
            "account": row["code"],
            "name": row["name"],
            "type": row["type"],
            "amount": float(row["amount"] or 0),
        }
        for row in rows
    }


def variance_report(
    slug: str,
    *,
    current_start: str,
    current_end: str,
    prior_start: str,
    prior_end: str,
) -> dict:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    try:
        current = _pnl_by_account(con, current_start, current_end)
        prior = _pnl_by_account(con, prior_start, prior_end)
    finally:
        con.close()

    rows = []
    for account in sorted(set(current) | set(prior)):
        c = current.get(account) or prior[account]
        current_amount = current.get(account, {}).get("amount", 0.0)
        prior_amount = prior.get(account, {}).get("amount", 0.0)
        delta = round(current_amount - prior_amount, 2)
        delta_pct = None if abs(prior_amount) < 0.005 else round(delta / abs(prior_amount) * 100, 2)
        rows.append({
            "account": account,
            "name": c["name"],
            "type": c["type"],
            "current": round(current_amount, 2),
            "prior": round(prior_amount, 2),
            "delta": delta,
            "delta_pct": delta_pct,
        })
    return {
        "client_slug": slug,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": "pnl_variance",
        "current_period": {"start": current_start, "end": current_end},
        "prior_period": {"start": prior_start, "end": prior_end},
        "rows": rows,
        "totals": {
            "current_income": round(sum(r["current"] for r in rows if r["type"] == "income"), 2),
            "current_expense": round(sum(r["current"] for r in rows if r["type"] == "expense"), 2),
            "prior_income": round(sum(r["prior"] for r in rows if r["type"] == "income"), 2),
            "prior_expense": round(sum(r["prior"] for r in rows if r["type"] == "expense"), 2),
        },
    }


def write_variance_report(slug: str, out_dir: str | Path | None = None, **periods) -> dict:
    report = variance_report(slug, **periods)
    base = Path(out_dir) if out_dir else tenancy.lisza_home() / "exports" / "reports" / slug
    base.mkdir(parents=True, exist_ok=True)
    stem = f"pnl-variance-{periods['current_start']}-{periods['current_end']}"
    json_path = base / f"{stem}.json"
    csv_path = base / f"{stem}.csv"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["account", "name", "type", "current", "prior", "delta", "delta_pct"])
        writer.writeheader()
        writer.writerows(report["rows"])
    report["artifacts"] = {"json": str(json_path), "csv": str(csv_path)}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_slug")
    parser.add_argument("--current-start", required=True)
    parser.add_argument("--current-end", required=True)
    parser.add_argument("--prior-start", required=True)
    parser.add_argument("--prior-end", required=True)
    parser.add_argument("--out-dir")
    args = parser.parse_args()
    report = write_variance_report(
        args.client_slug,
        out_dir=args.out_dir,
        current_start=args.current_start,
        current_end=args.current_end,
        prior_start=args.prior_start,
        prior_end=args.prior_end,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
