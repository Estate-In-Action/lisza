#!/usr/bin/env python3
"""Export LISZA client books into integration-friendly files.

This is the first ecosystem compatibility layer: deterministic, read-only
exports for QBO/Xero/e-commerce adapter work. It does not call external APIs or
mutate a client book.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tenancy

DATASETS = ("accounts", "journal_lines", "invoices", "bills")

FIELD_MAP = {
    "accounts": {
        "qbo": {"code": "AcctNum", "name": "Name", "type": "AccountType"},
        "xero": {"code": "Code", "name": "Name", "type": "Type"},
    },
    "journal_lines": {
        "qbo": {"entry_id": "JournalEntry.Id", "account": "AccountRef", "dr": "Debit", "cr": "Credit"},
        "xero": {"entry_id": "ManualJournal.Narration", "account": "AccountCode", "dr": "Debit", "cr": "Credit"},
    },
    "invoices": {
        "qbo": {"party": "CustomerRef", "due_date": "DueDate", "amount": "TotalAmt"},
        "xero": {"party": "Contact.Name", "due_date": "DueDate", "amount": "Total"},
    },
    "bills": {
        "qbo": {"party": "VendorRef", "due_date": "DueDate", "amount": "TotalAmt"},
        "xero": {"party": "Contact.Name", "due_date": "DueDate", "amount": "Total"},
    },
}


def _rows(con: sqlite3.Connection, dataset: str) -> list[dict]:
    if dataset == "accounts":
        return [dict(r) for r in con.execute(
            """SELECT code, name, type, sign_normal, active
               FROM accounts ORDER BY code"""
        )]
    if dataset == "journal_lines":
        return [dict(r) for r in con.execute(
            """SELECT e.id AS entry_id, e.entry_date, e.description, e.payee,
                      e.source, e.status, s.account, ROUND(s.dr, 2) AS dr,
                      ROUND(s.cr, 2) AS cr, s.memo
               FROM entries e
               JOIN splits s ON s.entry_id=e.id
               ORDER BY e.entry_date, e.id, s.id"""
        )]
    if dataset == "invoices":
        return [dict(r) for r in con.execute(
            """SELECT id, party, issue_date, due_date, ROUND(amount, 2) AS amount,
                      status, paid_date, entry_id, memo
               FROM invoices ORDER BY due_date, id"""
        )]
    if dataset == "bills":
        return [dict(r) for r in con.execute(
            """SELECT id, party, issue_date, due_date, ROUND(amount, 2) AS amount,
                      status, paid_date, entry_id, memo
               FROM bills ORDER BY due_date, id"""
        )]
    raise ValueError(f"unknown dataset: {dataset}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_client(slug: str, out_dir: str | Path | None = None) -> dict:
    base = Path(out_dir) if out_dir else tenancy.lisza_home() / "exports" / "ecosystem" / slug
    base.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    exported = {}
    try:
        for dataset in DATASETS:
            rows = _rows(con, dataset)
            json_path = base / f"{dataset}.json"
            csv_path = base / f"{dataset}.csv"
            json_path.write_text(json.dumps(rows, indent=2, sort_keys=True))
            _write_csv(csv_path, rows)
            exported[dataset] = {
                "rows": len(rows),
                "json": str(json_path),
                "csv": str(csv_path),
            }
    finally:
        con.close()

    manifest = {
        "client_slug": slug,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "read_only_export",
        "external_api_calls": "disabled",
        "writeback": "disabled",
        "datasets": exported,
        "field_map": FIELD_MAP,
    }
    manifest_path = base / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    manifest["manifest"] = str(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_slug")
    parser.add_argument("--out-dir")
    args = parser.parse_args()
    print(json.dumps(export_client(args.client_slug, args.out_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
