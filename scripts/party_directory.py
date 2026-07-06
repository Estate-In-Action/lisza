#!/usr/bin/env python3
"""Unified customer/vendor party directory for LISZA client books."""
from __future__ import annotations

import argparse
import json
import sqlite3

import tenancy

ROLES = ("customer", "vendor", "both", "lead")

PARTY_SCHEMA = """
CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer'
        CHECK(role IN ('customer','vendor','both','lead')),
    email TEXT,
    phone TEXT,
    tax_id TEXT,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, role)
);
"""


def ensure_party_schema(con: sqlite3.Connection) -> None:
    con.execute(PARTY_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    ensure_party_schema(con)
    return con


def upsert_party(
    slug: str,
    *,
    name: str,
    role: str = "customer",
    email: str | None = None,
    phone: str | None = None,
    tax_id: str | None = None,
    notes: str | None = None,
) -> int:
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    if not name.strip():
        raise ValueError("name is required")
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO parties(name, role, email, phone, tax_id, notes)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(name, role) DO UPDATE SET
             email=excluded.email,
             phone=excluded.phone,
             tax_id=excluded.tax_id,
             notes=excluded.notes,
             active=1,
             updated_at=datetime('now')""",
        (name.strip(), role, email, phone, tax_id, notes),
    )
    row = con.execute("SELECT id FROM parties WHERE name=? AND role=?", (name.strip(), role)).fetchone()
    con.commit()
    con.close()
    return int(row["id"] if row else cur.lastrowid)


def list_parties(slug: str, *, role: str | None = None, active_only: bool = True) -> list[dict]:
    con = _book(slug)
    clauses = []
    params: list = []
    if role:
        clauses.append("role=?")
        params.append(role)
    if active_only:
        clauses.append("active=1")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = con.execute(
        f"SELECT * FROM parties {where} ORDER BY role, name",
        params,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def archive_party(slug: str, party_id: int) -> None:
    con = _book(slug)
    con.execute("UPDATE parties SET active=0, updated_at=datetime('now') WHERE id=?", (party_id,))
    con.commit()
    con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upsert")
    up.add_argument("client_slug"); up.add_argument("--name", required=True)
    up.add_argument("--role", default="customer"); up.add_argument("--email")
    up.add_argument("--phone"); up.add_argument("--tax-id"); up.add_argument("--notes")
    ls = sub.add_parser("list"); ls.add_argument("client_slug"); ls.add_argument("--role")
    ar = sub.add_parser("archive"); ar.add_argument("client_slug"); ar.add_argument("party_id", type=int)
    args = parser.parse_args()
    if args.cmd == "upsert":
        print(json.dumps({"party_id": upsert_party(
            args.client_slug, name=args.name, role=args.role, email=args.email,
            phone=args.phone, tax_id=args.tax_id, notes=args.notes,
        )}))
    elif args.cmd == "list":
        print(json.dumps({"parties": list_parties(args.client_slug, role=args.role)}, indent=2, sort_keys=True))
    elif args.cmd == "archive":
        archive_party(args.client_slug, args.party_id); print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
