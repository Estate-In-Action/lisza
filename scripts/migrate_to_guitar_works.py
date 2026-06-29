#!/usr/bin/env python3
"""One-shot: copy the legacy single-tenant ledger.db into clients/guitar-works,
attach an entity-aware schema + client profile, and register it. Idempotent-ish:
re-running overwrites the guitar-works book from the legacy copy.

The legacy LISZA/ledger.db is left untouched (Principle #2).
"""
from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

import book_schema
import tenancy

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "ledger.db"


def main() -> int:
    assert LEGACY.exists(), f"legacy {LEGACY} missing"
    dest = tenancy.resolve_db("guitar-works")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LEGACY, dest)

    con = sqlite3.connect(dest)
    con.execute("PRAGMA foreign_keys=ON")
    book_schema.ensure_book_schema(con)
    cid = uuid.uuid4().hex[:12]
    con.execute(
        """INSERT OR REPLACE INTO client_profile
           (client_id, slug, legal_name, display_name, ein, entity_type,
            fiscal_year_end, filing_cadence, active_window)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (cid, "guitar-works", "Guitar Works LLC", "Guitar Works",
         "47-2201234", "llc", "12-31", "quarterly", "1y"))
    con.execute("UPDATE entities SET name='Guitar Works' WHERE is_default=1")
    con.commit()
    con.close()

    tenancy.registry_db()
    reg = sqlite3.connect(tenancy.registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO clients
           (client_id, slug, db_path, status, display_name, entity_type)
           VALUES (?,?,?,?,?,?)""",
        (cid, "guitar-works", str(dest), "active", "Guitar Works", "llc"))
    reg.commit()
    reg.close()
    print(f"migrated legacy book -> {dest} (client_id={cid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
