#!/usr/bin/env python3
"""Client document request queue for LISZA.

Requests live in the shared registry because they are bookkeeper/client
workflow state, not posted accounting truth. They can feed workflow follow-up
jobs but never write client ledgers.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta

import tenancy

DEFAULT_REQUESTS = [
    ("w9", "W-9 or tax identity form", "Needed before 1099/vendor tax handling."),
    ("bank_statement_current", "Current bank statement", "Needed for reconciliation and opening balance review."),
    ("prior_year_tax_return", "Prior-year tax return", "Needed to understand carryovers and filing history."),
    ("entity_docs", "Entity formation documents", "Needed to confirm legal name, entity type, and ownership."),
]


def _parse_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _connect() -> sqlite3.Connection:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    return con


def _client_id(con: sqlite3.Connection, slug: str) -> str:
    return tenancy._client_id_for_slug(con, slug)


def _event(con: sqlite3.Connection, request_id: int, event_type: str,
           note: str | None = None, payload: dict | None = None) -> None:
    con.execute(
        """INSERT INTO document_request_events
           (document_request_id, event_type, note, payload_json)
           VALUES (?, ?, ?, ?)""",
        (request_id, event_type, note, json.dumps(payload or {}, sort_keys=True)),
    )


def ensure_defaults(slug: str, *, as_of: date | None = None) -> dict:
    ref = as_of or date.today()
    due = (ref + timedelta(days=14)).isoformat()
    con = _connect()
    inserted = 0
    try:
        client_id = _client_id(con, slug)
        for key, label, desc in DEFAULT_REQUESTS:
            cur = con.execute(
                """INSERT OR IGNORE INTO document_requests
                   (client_id, doc_key, label, description, due_date, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (client_id, key, label, desc, due, json.dumps({"source": "default_onboarding"})),
            )
            if cur.rowcount:
                inserted += 1
                _event(con, cur.lastrowid, "requested", payload={"doc_key": key})
        con.commit()
    finally:
        con.close()
    return {"slug": slug, "inserted": inserted, "summary": summary(slug)}


def upsert_request(slug: str, doc_key: str, label: str, *,
                   description: str = "", due_date: str | None = None,
                   payload: dict | None = None) -> dict:
    con = _connect()
    try:
        client_id = _client_id(con, slug)
        row = con.execute(
            """SELECT document_request_id FROM document_requests
               WHERE client_id=? AND doc_key=?""",
            (client_id, doc_key),
        ).fetchone()
        data = json.dumps(payload or {}, sort_keys=True)
        if row:
            con.execute(
                """UPDATE document_requests
                   SET label=?, description=?, due_date=?, payload_json=?,
                       updated_at=datetime('now')
                   WHERE document_request_id=?""",
                (label, description, due_date, data, row["document_request_id"]),
            )
            request_id = row["document_request_id"]
            _event(con, request_id, "updated", payload=payload)
        else:
            cur = con.execute(
                """INSERT INTO document_requests
                   (client_id, doc_key, label, description, due_date, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (client_id, doc_key, label, description, due_date, data),
            )
            request_id = cur.lastrowid
            _event(con, request_id, "requested", payload=payload)
        con.commit()
    finally:
        con.close()
    return get_request(request_id)


def transition(request_id: int, status: str, *, note: str | None = None,
               payload: dict | None = None) -> dict:
    if status not in {"received", "waived", "requested", "overdue"}:
        raise ValueError("status must be received, waived, requested, or overdue")
    con = _connect()
    try:
        row = con.execute(
            "SELECT status FROM document_requests WHERE document_request_id=?",
            (request_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown document request: {request_id}")
        received_at = "datetime('now')" if status == "received" else "NULL"
        con.execute(
            f"""UPDATE document_requests
                SET status=?, received_at={received_at}, updated_at=datetime('now')
                WHERE document_request_id=?""",
            (status, request_id),
        )
        _event(con, request_id, status, note=note, payload=payload)
        con.commit()
    finally:
        con.close()
    return get_request(request_id)


def record_followup(request_id: int, *, note: str | None = None) -> dict:
    con = _connect()
    try:
        row = con.execute(
            "SELECT document_request_id FROM document_requests WHERE document_request_id=?",
            (request_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown document request: {request_id}")
        con.execute(
            """UPDATE document_requests
               SET followup_count=followup_count+1,
                   last_followup_at=datetime('now'),
                   updated_at=datetime('now')
               WHERE document_request_id=?""",
            (request_id,),
        )
        _event(con, request_id, "followup_prepared", note=note)
        con.commit()
    finally:
        con.close()
    return get_request(request_id)


def list_requests(slug: str | None = None, *, status: str | None = None) -> dict:
    con = _connect()
    try:
        filters = []
        params: list = []
        if slug:
            filters.append("c.slug=?")
            params.append(slug)
        if status:
            filters.append("d.status=?")
            params.append(status)
        where = "WHERE " + " AND ".join(filters) if filters else ""
        rows = con.execute(
            f"""SELECT d.document_request_id, c.slug AS client_slug,
                       c.display_name AS client_name, d.doc_key, d.label,
                       d.description, d.due_date, d.status, d.requested_at,
                       d.received_at, d.last_followup_at, d.followup_count,
                       d.updated_at
                FROM document_requests d
                JOIN clients c ON c.client_id=d.client_id
                {where}
                ORDER BY
                  CASE d.status
                    WHEN 'overdue' THEN 0
                    WHEN 'requested' THEN 1
                    WHEN 'received' THEN 2
                    ELSE 3
                  END,
                  d.due_date IS NULL, d.due_date, c.display_name, d.label""",
            params,
        ).fetchall()
    finally:
        con.close()
    return {"summary": _summary_from_rows(rows), "requests": [dict(r) for r in rows]}


def _summary_from_rows(rows: list[sqlite3.Row]) -> dict:
    counts = {"requested": 0, "received": 0, "waived": 0, "overdue": 0, "open": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] in ("requested", "overdue"):
            counts["open"] += 1
    return counts


def summary(slug: str | None = None) -> dict:
    return list_requests(slug)["summary"]


def mark_overdue(*, as_of: date | None = None) -> dict:
    ref = (as_of or date.today()).isoformat()
    con = _connect()
    changed = 0
    try:
        rows = con.execute(
            """SELECT document_request_id FROM document_requests
               WHERE status='requested' AND due_date IS NOT NULL AND due_date < ?""",
            (ref,),
        ).fetchall()
        for row in rows:
            con.execute(
                """UPDATE document_requests
                   SET status='overdue', updated_at=datetime('now')
                   WHERE document_request_id=?""",
                (row["document_request_id"],),
            )
            _event(con, row["document_request_id"], "overdue", payload={"as_of": ref})
            changed += 1
        con.commit()
    finally:
        con.close()
    return {"as_of": ref, "overdue_marked": changed, "summary": summary()}


def workflow_items(*, as_of: date | None = None) -> list[dict]:
    mark_overdue(as_of=as_of)
    rows = list_requests()["requests"]
    items = []
    ref = as_of or date.today()
    for row in rows:
        if row["status"] not in ("requested", "overdue"):
            continue
        due = date.fromisoformat(row["due_date"]) if row.get("due_date") else ref
        if row["status"] == "overdue" or due <= ref:
            planner_status = "due_now"
        elif due <= ref + timedelta(days=14):
            planner_status = "upcoming"
        else:
            planner_status = "scheduled"
        items.append({
            "key": f"document_request:{row['document_request_id']}",
            "label": f"Document request: {row['label']}",
            "due_date": due.isoformat(),
            "status": planner_status,
            "source": "client_portal.document_requests",
            "approval_required": True,
            "client_slug": row["client_slug"],
            "client_name": row["client_name"],
            "document_request_id": row["document_request_id"],
            "doc_key": row["doc_key"],
        })
    return items


def get_request(request_id: int) -> dict:
    rows = list_requests()["requests"]
    for row in rows:
        if row["document_request_id"] == request_id:
            return row
    raise ValueError(f"unknown document request: {request_id}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    defaults = sub.add_parser("ensure-defaults")
    defaults.add_argument("slug")
    defaults.add_argument("--as-of")

    add = sub.add_parser("request")
    add.add_argument("slug")
    add.add_argument("doc_key")
    add.add_argument("label")
    add.add_argument("--description", default="")
    add.add_argument("--due-date")

    listp = sub.add_parser("list")
    listp.add_argument("--client")
    listp.add_argument("--status")

    trans = sub.add_parser("status")
    trans.add_argument("request_id", type=int)
    trans.add_argument("status", choices=["requested", "received", "waived", "overdue"])
    trans.add_argument("--note")

    follow = sub.add_parser("followup")
    follow.add_argument("request_id", type=int)
    follow.add_argument("--note")

    overdue = sub.add_parser("mark-overdue")
    overdue.add_argument("--as-of")

    flow = sub.add_parser("workflow-items")
    flow.add_argument("--as-of")

    args = ap.parse_args()
    if args.cmd == "ensure-defaults":
        data = ensure_defaults(args.slug, as_of=_parse_date(args.as_of))
    elif args.cmd == "request":
        data = upsert_request(
            args.slug, args.doc_key, args.label,
            description=args.description, due_date=args.due_date)
    elif args.cmd == "list":
        data = list_requests(args.client, status=args.status)
    elif args.cmd == "status":
        data = transition(args.request_id, args.status, note=args.note)
    elif args.cmd == "followup":
        data = record_followup(args.request_id, note=args.note)
    elif args.cmd == "mark-overdue":
        data = mark_overdue(as_of=_parse_date(args.as_of))
    elif args.cmd == "workflow-items":
        data = {"items": workflow_items(as_of=_parse_date(args.as_of))}
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
