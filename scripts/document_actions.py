#!/usr/bin/env python3
"""Approval-gated document write-action helpers for LISZA.

The first action is invoice sending. It creates or exposes a pending approval
record in the shared workflow control plane; it does not send email, post ledger
entries, or mark invoices as delivered.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import number_series
import tenancy


def _registry() -> sqlite3.Connection:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    return con


def invoice_send_plan(slug: str, invoice_id: int) -> dict:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    try:
        invoice = con.execute(
            "SELECT id, party, issue_date, due_date, amount, status, memo FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone()
    finally:
        con.close()
    if not invoice:
        raise ValueError(f"unknown invoice: {invoice_id}")
    if invoice["status"] != "open":
        raise ValueError(f"invoice {invoice_id} is {invoice['status']}")
    return {
        "client_slug": slug,
        "key": f"send_invoice:{invoice_id}",
        "label": f"Send invoice #{invoice_id} to {invoice['party']}",
        "due_date": invoice["issue_date"] or date.today().isoformat(),
        "status": "due_now",
        "source": "invoice_send_button",
        "approval_required": True,
        "execution": "invoice_send_prep",
        "invoice_id": invoice["id"],
        "party": invoice["party"],
        "amount": float(invoice["amount"] or 0),
        "issue_date": invoice["issue_date"],
        "due_date_invoice": invoice["due_date"],
        "preview_number": number_series.preview_next(slug, "invoice"),
    }


def queue_invoice_send(slug: str, invoice_id: int) -> dict:
    plan = invoice_send_plan(slug, invoice_id)
    con = _registry()
    try:
        client_id = tenancy._client_id_for_slug(con, slug)
        payload = json.dumps(plan, sort_keys=True)
        row = con.execute(
            """SELECT workflow_job_id, workflow_status
               FROM workflow_jobs
               WHERE client_id=? AND job_key=? AND due_date=?""",
            (client_id, plan["key"], plan["due_date"]),
        ).fetchone()
        if row:
            if row["workflow_status"] not in {"skipped", "completed"}:
                con.execute(
                    """UPDATE workflow_jobs
                       SET label=?, planner_status=?, source=?, approval_required=1,
                           payload_json=?, updated_at=datetime('now')
                       WHERE workflow_job_id=?""",
                    (plan["label"], plan["status"], plan["source"], payload, row["workflow_job_id"]),
                )
            workflow_job_id = int(row["workflow_job_id"])
            workflow_status = row["workflow_status"]
        else:
            cur = con.execute(
                """INSERT INTO workflow_jobs
                   (client_id, job_key, label, due_date, planner_status, workflow_status,
                    source, approval_required, payload_json)
                   VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, 1, ?)""",
                (client_id, plan["key"], plan["label"], plan["due_date"], plan["status"], plan["source"], payload),
            )
            workflow_job_id = int(cur.lastrowid)
            workflow_status = "pending_approval"
            con.execute(
                """INSERT INTO workflow_events(workflow_job_id, event_type, note, payload_json)
                   VALUES (?, 'planned', 'invoice send approval queued', ?)""",
                (workflow_job_id, payload),
            )
        con.commit()
    finally:
        con.close()
    return {
        **plan,
        "workflow_job_id": workflow_job_id,
        "workflow_status": workflow_status,
        "external_delivery": "disabled_until_approved",
        "ledger_write": "disabled",
    }


def pending_invoice_actions(slug: str) -> list[dict]:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT id, party, issue_date, due_date, amount, status
               FROM invoices WHERE status='open' ORDER BY due_date, id LIMIT 25"""
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "document_type": "invoice",
            "document_id": row["id"],
            "action": "send_invoice",
            "label": f"Send invoice #{row['id']}",
            "party": row["party"],
            "amount": float(row["amount"] or 0),
            "issue_date": row["issue_date"],
            "due_date": row["due_date"],
            "status": "approval_required",
            "preview_number": number_series.preview_next(slug, "invoice"),
        }
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan-send-invoice")
    plan.add_argument("client_slug")
    plan.add_argument("invoice_id", type=int)
    queue = sub.add_parser("queue-send-invoice")
    queue.add_argument("client_slug")
    queue.add_argument("invoice_id", type=int)
    args = parser.parse_args()
    if args.cmd == "plan-send-invoice":
        data = invoice_send_plan(args.client_slug, args.invoice_id)
    else:
        data = queue_invoice_send(args.client_slug, args.invoice_id)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
