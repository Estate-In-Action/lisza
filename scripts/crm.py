#!/usr/bin/env python3
"""LISZA CRM: lead funnel + lead→client conversion.

Leads live in the shared registry (lisza.db), not in a per-client book — a lead
has no ledger yet. Conversion is the bridge: it calls tenancy.register_client,
which mints clients/<slug>/ledger.db + the registry row, then back-links the
lead to that client_id. This is the unified Party substrate: a lead is a
pre-client Party that becomes a client row on conversion.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone

import tenancy

STAGES = ("intake", "in_process", "won", "lost")
OPEN_STAGES = ("intake", "in_process")
EDITABLE = ("display_name", "contact_name", "email", "phone", "entity_type",
            "source", "est_monthly_fee", "notes")

CRM_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id         TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    contact_name    TEXT,
    email           TEXT,
    phone           TEXT,
    entity_type     TEXT,
    stage           TEXT NOT NULL DEFAULT 'intake'
                    CHECK(stage IN ('intake','in_process','won','lost')),
    source          TEXT,
    est_monthly_fee REAL,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    converted_client_id TEXT,
    converted_at    TEXT
)
"""


def ensure_crm_schema(con: sqlite3.Connection) -> None:
    con.execute(CRM_SCHEMA)
    con.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    ensure_crm_schema(con)
    return con


def add_lead(display_name: str, *, contact_name: str | None = None,
             email: str | None = None, phone: str | None = None,
             entity_type: str | None = None, source: str | None = None,
             est_monthly_fee: float | None = None,
             notes: str | None = None) -> str:
    if not display_name or not display_name.strip():
        raise ValueError("display_name is required")
    lead_id = uuid.uuid4().hex[:12]
    con = _connect()
    con.execute(
        """INSERT INTO leads(lead_id, display_name, contact_name, email, phone,
                             entity_type, source, est_monthly_fee, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (lead_id, display_name.strip(), contact_name, email, phone,
         entity_type, source,
         float(est_monthly_fee) if est_monthly_fee is not None else None, notes))
    con.commit()
    con.close()
    return lead_id


def list_leads(stage: str | None = None) -> list[dict]:
    con = _connect()
    if stage:
        rows = con.execute(
            "SELECT * FROM leads WHERE stage=? ORDER BY created_at DESC, lead_id",
            (stage,)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM leads ORDER BY created_at DESC, lead_id").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_lead(lead_id: str) -> dict | None:
    con = _connect()
    row = con.execute("SELECT * FROM leads WHERE lead_id=?", (lead_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def _require(con: sqlite3.Connection, lead_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM leads WHERE lead_id=?", (lead_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown lead: {lead_id}")
    return row


def update_lead(lead_id: str, **fields) -> None:
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
    con = _connect()
    row = _require(con, lead_id)
    if row["converted_client_id"]:
        con.close()
        raise ValueError("cannot edit a converted lead")
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE lead_id=?",
                    (*fields.values(), _now(), lead_id))
        con.commit()
    con.close()


def set_stage(lead_id: str, stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"invalid stage: {stage} (allowed: {', '.join(STAGES)})")
    con = _connect()
    row = _require(con, lead_id)
    if row["converted_client_id"]:
        con.close()
        raise ValueError("cannot change stage of a converted lead")
    con.execute("UPDATE leads SET stage=?, updated_at=? WHERE lead_id=?",
                (stage, _now(), lead_id))
    con.commit()
    con.close()


def convert_lead(lead_id: str, slug: str, *, display_name: str | None = None,
                 **register_kwargs) -> str:
    con = _connect()
    row = _require(con, lead_id)
    if row["converted_client_id"]:
        con.close()
        raise ValueError("lead already converted")
    taken = con.execute(
        "SELECT 1 FROM clients WHERE slug=?", (slug,)).fetchone()
    if taken:
        con.close()
        raise ValueError(f"slug already in use: {slug}")
    con.close()

    client_id = tenancy.register_client(
        slug=slug,
        display_name=display_name or row["display_name"],
        entity_type=row["entity_type"],
        **register_kwargs)

    con = _connect()
    con.execute(
        """UPDATE leads SET stage='won', converted_client_id=?, converted_at=?,
                            updated_at=? WHERE lead_id=?""",
        (client_id, _now(), _now(), lead_id))
    con.commit()
    con.close()
    return client_id


def funnel_summary() -> dict:
    con = _connect()
    rows = con.execute(
        "SELECT stage, COUNT(*) n, COALESCE(SUM(est_monthly_fee),0) fee "
        "FROM leads GROUP BY stage").fetchall()
    con.close()
    counts = {s: 0 for s in STAGES}
    est_pipeline = 0.0
    total = 0
    for r in rows:
        counts[r["stage"]] = r["n"]
        total += r["n"]
        if r["stage"] in OPEN_STAGES:
            est_pipeline += r["fee"] or 0.0
    return {"counts": counts, "est_pipeline": round(est_pipeline, 2),
            "total": total}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("display_name")
    a.add_argument("--contact"); a.add_argument("--email"); a.add_argument("--phone")
    a.add_argument("--entity-type"); a.add_argument("--source")
    a.add_argument("--fee", type=float); a.add_argument("--notes")

    ls = sub.add_parser("list"); ls.add_argument("--stage")
    g = sub.add_parser("get"); g.add_argument("lead_id")
    st = sub.add_parser("stage"); st.add_argument("lead_id"); st.add_argument("stage")
    cv = sub.add_parser("convert")
    cv.add_argument("lead_id"); cv.add_argument("slug")
    cv.add_argument("--display-name")
    sub.add_parser("funnel")
    sub.add_parser("board")

    args = p.parse_args()
    try:
        return _dispatch(args)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 0


def _dispatch(args) -> int:
    if args.cmd == "add":
        lead_id = add_lead(args.display_name, contact_name=args.contact,
                           email=args.email, phone=args.phone,
                           entity_type=args.entity_type, source=args.source,
                           est_monthly_fee=args.fee, notes=args.notes)
        print(json.dumps({"lead_id": lead_id}))
    elif args.cmd == "list":
        print(json.dumps({"leads": list_leads(args.stage)}))
    elif args.cmd == "get":
        print(json.dumps({"lead": get_lead(args.lead_id)}))
    elif args.cmd == "stage":
        set_stage(args.lead_id, args.stage)
        print(json.dumps({"ok": True, "lead": get_lead(args.lead_id)}))
    elif args.cmd == "convert":
        client_id = convert_lead(args.lead_id, args.slug,
                                 display_name=args.display_name)
        print(json.dumps({"ok": True, "client_id": client_id,
                          "slug": args.slug}))
    elif args.cmd == "funnel":
        print(json.dumps(funnel_summary()))
    elif args.cmd == "board":
        print(json.dumps({"leads": list_leads(), "funnel": funnel_summary()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
