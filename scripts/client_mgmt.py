#!/usr/bin/env python3
"""LISZA Client Management: roster, engagement terms, contracts.

The client-side half of the Party substrate (CRM is the lead side). Adding a
client reuses tenancy.register_client (mints clients/<slug>/ledger.db + the
registry row). Archive/restore flips only the registry status — the book is
never deleted (Rule #5: archive != delete). Engagement terms live in the
client's own book (client_profile); the registry caches only display_name and
entity_type. Contracts are per-client documents stored in the client book.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3

import book_schema
import tenancy

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
SYSTEM_SLUGS = {tenancy.HOUSE_SLUG}
CONTRACT_STATUSES = ("draft", "active", "expired", "terminated")
EDITABLE_TERMS = ("display_name", "legal_name", "ein", "entity_type",
                  "fiscal_year_end", "filing_cadence", "monthly_fee")
REGISTRY_MIRRORED = ("display_name", "entity_type")

CONTRACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    effective_date TEXT,
    end_date       TEXT,
    monthly_fee    REAL,
    status         TEXT NOT NULL DEFAULT 'draft'
                   CHECK(status IN ('draft','active','expired','terminated')),
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def ensure_client_mgmt_schema(con: sqlite3.Connection) -> None:
    if not book_schema._has_column(con, "client_profile", "monthly_fee"):
        con.execute("ALTER TABLE client_profile ADD COLUMN monthly_fee REAL")
    con.execute(CONTRACTS_SCHEMA)
    con.commit()


def _registry() -> sqlite3.Connection:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    return con


def _book(slug: str) -> sqlite3.Connection:
    db = tenancy.resolve_db(slug)
    if not db.exists():
        raise ValueError(f"unknown client: {slug}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    ensure_client_mgmt_schema(con)
    return con


def _registry_row(slug: str) -> sqlite3.Row:
    con = _registry()
    row = con.execute("SELECT * FROM clients WHERE slug=?", (slug,)).fetchone()
    con.close()
    if not row:
        raise ValueError(f"unknown client: {slug}")
    return row


def _guard_manageable(slug: str) -> sqlite3.Row:
    row = _registry_row(slug)
    if row["kind"] == "house" or slug in SYSTEM_SLUGS:
        raise ValueError(f"cannot manage system client: {slug}")
    return row


def _terms(slug: str) -> dict:
    con = _book(slug)
    row = con.execute(
        """SELECT legal_name, display_name, ein, entity_type, fiscal_year_end,
                  filing_cadence, monthly_fee FROM client_profile LIMIT 1"""
    ).fetchone()
    con.close()
    return dict(row) if row else {}


def get_client(slug: str) -> dict | None:
    con = _registry()
    reg = con.execute("SELECT * FROM clients WHERE slug=?", (slug,)).fetchone()
    summ = con.execute(
        "SELECT * FROM client_summary WHERE client_id=?",
        (reg["client_id"],)).fetchone() if reg else None
    con.close()
    if not reg:
        return None
    out = dict(reg)
    out.update(_terms(slug))  # book terms win for shared keys
    out["display_name"] = out.get("display_name") or reg["display_name"]
    if summ:
        out["summary"] = dict(summ)
    return out


def list_all(status: str | None = None) -> list[dict]:
    con = _registry()
    if status:
        rows = con.execute(
            "SELECT * FROM clients WHERE kind != 'house' AND status=? "
            "ORDER BY slug", (status,)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM clients WHERE kind != 'house' ORDER BY slug").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d.update(_terms(r["slug"]))
        d["display_name"] = d.get("display_name") or r["display_name"]
        out.append(d)
    return out


def _validate_slug(slug: str) -> None:
    if slug in SYSTEM_SLUGS or not SLUG_RE.match(slug or ""):
        raise ValueError(f"invalid slug: {slug}")


def add_client(slug: str, display_name: str, *, legal_name: str | None = None,
               entity_type: str | None = None, ein: str | None = None,
               fiscal_year_end: str = "12-31", filing_cadence: str = "quarterly",
               monthly_fee: float | None = None) -> str:
    _validate_slug(slug)
    if not display_name or not display_name.strip():
        raise ValueError("display_name is required")
    con = _registry()
    taken = con.execute("SELECT 1 FROM clients WHERE slug=?", (slug,)).fetchone()
    con.close()
    if taken:
        raise ValueError(f"slug already in use: {slug}")

    client_id = tenancy.register_client(
        slug=slug, display_name=display_name.strip(), legal_name=legal_name,
        entity_type=entity_type, ein=ein, fiscal_year_end=fiscal_year_end,
        filing_cadence=filing_cadence)
    if monthly_fee is not None:
        con = _book(slug)
        con.execute("UPDATE client_profile SET monthly_fee=?",
                    (float(monthly_fee),))
        con.commit()
        con.close()
    return client_id


def _set_status(slug: str, status: str) -> None:
    _guard_manageable(slug)
    con = _registry()
    con.execute("UPDATE clients SET status=? WHERE slug=?", (status, slug))
    con.commit()
    con.close()


def archive_client(slug: str) -> None:
    _set_status(slug, "archived")


def restore_client(slug: str) -> None:
    _set_status(slug, "active")


def update_terms(slug: str, **fields) -> None:
    unknown = set(fields) - set(EDITABLE_TERMS)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
    _guard_manageable(slug)
    if not fields:
        return
    if "monthly_fee" in fields and fields["monthly_fee"] is not None:
        fields["monthly_fee"] = float(fields["monthly_fee"])
    con = _book(slug)
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE client_profile SET {sets}", tuple(fields.values()))
    con.commit()
    con.close()

    mirror = {k: fields[k] for k in REGISTRY_MIRRORED if k in fields}
    if mirror:
        reg = _registry()
        sets = ", ".join(f"{k}=?" for k in mirror)
        reg.execute(f"UPDATE clients SET {sets} WHERE slug=?",
                    (*mirror.values(), slug))
        reg.commit()
        reg.close()


def list_contracts(slug: str) -> list[dict]:
    con = _book(slug)
    rows = con.execute(
        "SELECT * FROM contracts ORDER BY created_at DESC, id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_contract(slug: str, title: str, *, effective_date: str | None = None,
                 end_date: str | None = None, monthly_fee: float | None = None,
                 notes: str | None = None, status: str = "draft") -> int:
    if not title or not title.strip():
        raise ValueError("title is required")
    if status not in CONTRACT_STATUSES:
        raise ValueError(f"invalid status: {status}")
    con = _book(slug)
    cur = con.execute(
        """INSERT INTO contracts(title, effective_date, end_date, monthly_fee,
                                 status, notes) VALUES (?,?,?,?,?,?)""",
        (title.strip(), effective_date, end_date,
         float(monthly_fee) if monthly_fee is not None else None, status, notes))
    con.commit()
    contract_id = cur.lastrowid
    con.close()
    return contract_id


def set_contract_status(slug: str, contract_id: int, status: str) -> None:
    if status not in CONTRACT_STATUSES:
        raise ValueError(
            f"invalid status: {status} (allowed: {', '.join(CONTRACT_STATUSES)})")
    con = _book(slug)
    row = con.execute(
        "SELECT 1 FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError(f"unknown contract: {contract_id}")
    con.execute("UPDATE contracts SET status=? WHERE id=?", (status, contract_id))
    con.commit()
    con.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list"); ls.add_argument("--status")
    g = sub.add_parser("get"); g.add_argument("slug")

    a = sub.add_parser("add")
    a.add_argument("slug"); a.add_argument("display_name")
    a.add_argument("--legal-name"); a.add_argument("--entity-type")
    a.add_argument("--ein"); a.add_argument("--fye", default="12-31")
    a.add_argument("--cadence", default="quarterly")
    a.add_argument("--fee", type=float)

    ar = sub.add_parser("archive"); ar.add_argument("slug")
    rs = sub.add_parser("restore"); rs.add_argument("slug")

    tm = sub.add_parser("terms")
    tm.add_argument("slug")
    tm.add_argument("--display-name"); tm.add_argument("--legal-name")
    tm.add_argument("--ein"); tm.add_argument("--entity-type")
    tm.add_argument("--fye"); tm.add_argument("--cadence")
    tm.add_argument("--fee", type=float)

    cl = sub.add_parser("contracts"); cl.add_argument("slug")
    ca = sub.add_parser("contract-add")
    ca.add_argument("slug"); ca.add_argument("title")
    ca.add_argument("--effective"); ca.add_argument("--end")
    ca.add_argument("--fee", type=float); ca.add_argument("--notes")
    ca.add_argument("--status", default="draft")
    cs = sub.add_parser("contract-status")
    cs.add_argument("slug"); cs.add_argument("contract_id", type=int)
    cs.add_argument("status")

    args = p.parse_args()
    try:
        return _dispatch(args)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 0


def _dispatch(args) -> int:
    if args.cmd == "list":
        print(json.dumps({"clients": list_all(args.status)}))
    elif args.cmd == "get":
        print(json.dumps({"client": get_client(args.slug)}))
    elif args.cmd == "add":
        client_id = add_client(
            args.slug, args.display_name, legal_name=args.legal_name,
            entity_type=args.entity_type, ein=args.ein,
            fiscal_year_end=args.fye, filing_cadence=args.cadence,
            monthly_fee=args.fee)
        print(json.dumps({"ok": True, "client_id": client_id, "slug": args.slug}))
    elif args.cmd == "archive":
        archive_client(args.slug)
        print(json.dumps({"ok": True, "client": get_client(args.slug)}))
    elif args.cmd == "restore":
        restore_client(args.slug)
        print(json.dumps({"ok": True, "client": get_client(args.slug)}))
    elif args.cmd == "terms":
        fields = {}
        for cli_key, term in (("display_name", "display_name"),
                              ("legal_name", "legal_name"), ("ein", "ein"),
                              ("entity_type", "entity_type"), ("fye", "fiscal_year_end"),
                              ("cadence", "filing_cadence"), ("fee", "monthly_fee")):
            v = getattr(args, cli_key, None)
            if v is not None:
                fields[term] = v
        update_terms(args.slug, **fields)
        print(json.dumps({"ok": True, "client": get_client(args.slug)}))
    elif args.cmd == "contracts":
        print(json.dumps({"contracts": list_contracts(args.slug)}))
    elif args.cmd == "contract-add":
        cid = add_contract(args.slug, args.title, effective_date=args.effective,
                           end_date=args.end, monthly_fee=args.fee,
                           notes=args.notes, status=args.status)
        print(json.dumps({"ok": True, "contract_id": cid}))
    elif args.cmd == "contract-status":
        set_contract_status(args.slug, args.contract_id, args.status)
        print(json.dumps({"ok": True, "contracts": list_contracts(args.slug)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
