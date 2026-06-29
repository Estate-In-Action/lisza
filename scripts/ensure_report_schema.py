#!/usr/bin/env python3
"""Idempotently ensure a client ledger has the objects the live reports need.

The multi-tenant console queries every client's ledger.db through the same
SQL the legacy single-tenant book used. Two objects can be missing on older
client DBs (e.g. those seeded before the reports layer existed):

  - category_overrides  table  — backs the Categorize tab's manual overrides
  - v_account_balances  view   — backs the Overview balances

This makes both present without touching any data. Safe to run repeatedly.
"""
from __future__ import annotations

import sqlite3
import sys

CATEGORY_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS category_overrides (
    entry_id     INTEGER PRIMARY KEY REFERENCES entries(id),
    account_code TEXT NOT NULL REFERENCES accounts(code),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

V_ACCOUNT_BALANCES_DDL = """
CREATE VIEW v_account_balances AS
SELECT a.code, a.name, a.type, a.sign_normal,
       COALESCE(SUM(s.dr), 0) AS total_dr,
       COALESCE(SUM(s.cr), 0) AS total_cr,
       CASE
         WHEN a.sign_normal = 'debit'  THEN COALESCE(SUM(s.dr), 0) - COALESCE(SUM(s.cr), 0)
         WHEN a.sign_normal = 'credit' THEN COALESCE(SUM(s.cr), 0) - COALESCE(SUM(s.dr), 0)
       END AS balance
FROM accounts a
LEFT JOIN splits s ON s.account = a.code
LEFT JOIN entries e ON e.id = s.entry_id AND e.status = 'posted'
GROUP BY a.code
"""


def _exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone() is not None


def ensure_report_schema(con: sqlite3.Connection) -> list[str]:
    """Create any missing report objects on an open connection.

    Returns the names that were actually created (empty if already complete).
    """
    created: list[str] = []
    if not _exists(con, "category_overrides"):
        con.execute(CATEGORY_OVERRIDES_DDL)
        created.append("category_overrides")
    if not _exists(con, "v_account_balances"):
        con.execute(V_ACCOUNT_BALANCES_DDL)
        created.append("v_account_balances")
    return created


def apply(db_path: str) -> list[str]:
    """Apply the migration to a ledger file. Returns names that were created."""
    con = sqlite3.connect(db_path)
    try:
        created = ensure_report_schema(con)
        con.commit()
        return created
    finally:
        con.close()


if __name__ == "__main__":
    import os
    from pathlib import Path

    home = Path(os.environ.get("LISZA_HOME", Path(__file__).resolve().parent.parent))
    clients_dir = home / "clients"
    total = 0
    for sub in sorted(clients_dir.iterdir()):
        ledger = sub / "ledger.db"
        if not ledger.exists():
            continue
        created = apply(str(ledger))
        total += len(created)
        tag = ", ".join(created) if created else "already complete"
        print(f"{sub.name:<22} {tag}")
    print(f"\nDone — {total} object(s) created.")
