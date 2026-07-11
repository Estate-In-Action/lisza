#!/usr/bin/env python3
"""Registry-backed LISZA workflow control plane.

This module turns advisory automation-profile jobs into durable workflow rows.
It never posts ledger entries, sends reports, files taxes, moves money, or
changes client books. Running an approved job creates an audit/report-prep
receipt only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path

import automation_profile
import ar_ap_workflows
import document_requests
import tenancy

TERMINAL = {"skipped", "completed"}
RUNNABLE = {
    "weekly_digest",
    "monthly_close",
    "quarterly_packet",
    "tax_packet",
    "payroll_review",
    "sales_tax_review",
    "document_followup",
    "send_invoice",
}


def _parse_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _connect() -> sqlite3.Connection:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    return con


def _event(con: sqlite3.Connection, job_id: int, event_type: str,
           note: str | None = None, payload: dict | None = None) -> None:
    con.execute(
        """INSERT INTO workflow_events
           (workflow_job_id, event_type, note, payload_json)
           VALUES (?, ?, ?, ?)""",
        (job_id, event_type, note, json.dumps(payload or {}, sort_keys=True)),
    )


def sync_jobs(*, as_of: date | None = None,
              include_scheduled: bool = False) -> dict:
    """Upsert current advisory jobs into durable workflow state."""
    ref = as_of or date.today()
    queue = automation_profile.workflow_queue(
        as_of=ref, include_scheduled=include_scheduled)
    planned_items = (
        list(queue["queue"])
        + document_requests.workflow_items(as_of=ref)
        + ar_ap_workflows.workflow_items(as_of=ref)
    )
    con = _connect()
    inserted = 0
    updated = 0
    try:
        for item in planned_items:
            client_id = tenancy._client_id_for_slug(con, item["client_slug"])
            workflow_status = "pending_approval" if item.get("approval_required") else "blocked"
            payload = json.dumps(item, sort_keys=True)
            row = con.execute(
                """SELECT workflow_job_id, workflow_status
                   FROM workflow_jobs
                   WHERE client_id=? AND job_key=? AND due_date=?""",
                (client_id, item["key"], item["due_date"]),
            ).fetchone()
            if row:
                if row["workflow_status"] not in TERMINAL:
                    con.execute(
                        """UPDATE workflow_jobs
                           SET label=?, planner_status=?, source=?,
                               approval_required=?, payload_json=?,
                               updated_at=datetime('now')
                           WHERE workflow_job_id=?""",
                        (item["label"], item["status"], item.get("source"),
                         1 if item.get("approval_required") else 0,
                         payload, row["workflow_job_id"]),
                    )
                    _event(con, row["workflow_job_id"], "refreshed", payload=item)
                    updated += 1
                continue
            cur = con.execute(
                """INSERT INTO workflow_jobs
                   (client_id, job_key, label, due_date, planner_status,
                    workflow_status, source, approval_required, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (client_id, item["key"], item["label"], item["due_date"],
                 item["status"], workflow_status, item.get("source"),
                 1 if item.get("approval_required") else 0, payload),
            )
            _event(con, cur.lastrowid, "planned", payload=item)
            inserted += 1
        con.commit()
    finally:
        con.close()
    return {
        "as_of": queue["as_of"],
        "mode": "approval_control_plane",
        "execution": "approval_required",
        "inserted": inserted,
        "updated": updated,
        "summary": workflow_summary(),
    }


def workflow_summary() -> dict:
    con = _connect()
    try:
        rows = con.execute(
            """SELECT workflow_status, COUNT(*)
               FROM workflow_jobs GROUP BY workflow_status"""
        ).fetchall()
    finally:
        con.close()
    counts = {r[0]: r[1] for r in rows}
    for key in ("pending_approval", "approved", "skipped", "completed", "failed", "blocked"):
        counts.setdefault(key, 0)
    return counts


def list_jobs(status: str | None = None, *, limit: int = 100) -> dict:
    con = _connect()
    try:
        where = "WHERE w.workflow_status=?" if status else ""
        params: tuple = (status, limit) if status else (limit,)
        rows = con.execute(
            f"""SELECT w.workflow_job_id, c.slug AS client_slug,
                       c.display_name AS client_name, w.job_key, w.label,
                       w.due_date, w.planner_status, w.workflow_status,
                       w.source, w.approval_required, w.planned_at,
                       w.decided_at, w.executed_at, w.updated_at
                FROM workflow_jobs w
                JOIN clients c ON c.client_id=w.client_id
                {where}
                ORDER BY
                  CASE w.workflow_status
                    WHEN 'pending_approval' THEN 0
                    WHEN 'approved' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'blocked' THEN 3
                    ELSE 4
                  END,
                  w.due_date, c.display_name, w.job_key
                LIMIT ?""",
            params,
        ).fetchall()
    finally:
        con.close()
    return {"summary": workflow_summary(), "jobs": [dict(r) for r in rows]}


def decide(job_id: int, action: str, *, note: str | None = None) -> dict:
    if action not in {"approve", "skip"}:
        raise ValueError("action must be approve or skip")
    new_status = "approved" if action == "approve" else "skipped"
    con = _connect()
    try:
        row = con.execute(
            "SELECT workflow_status FROM workflow_jobs WHERE workflow_job_id=?",
            (job_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown workflow job: {job_id}")
        if row["workflow_status"] in TERMINAL:
            raise ValueError(f"workflow job {job_id} is already {row['workflow_status']}")
        con.execute(
            """UPDATE workflow_jobs
               SET workflow_status=?, decided_at=datetime('now'),
                   updated_at=datetime('now')
               WHERE workflow_job_id=?""",
            (new_status, job_id),
        )
        _event(con, job_id, action, note=note)
        con.commit()
    finally:
        con.close()
    return get_job(job_id)


def get_job(job_id: int) -> dict:
    data = list_jobs(limit=10000)
    for row in data["jobs"]:
        if row["workflow_job_id"] == job_id:
            return row
    raise ValueError(f"unknown workflow job: {job_id}")


def _write_receipt(job: sqlite3.Row) -> dict:
    payload = json.loads(job["payload_json"])
    job_key = str(job["job_key"])
    if job_key.startswith("document_request:"):
        execution = "document_followup"
    elif job_key.startswith("send_invoice:"):
        execution = "invoice_send_prep"
    elif job_key.startswith("ar_invoice_reminder:"):
        execution = "client_payment_reminder_prep"
    elif job_key.startswith("ap_bill_approval:"):
        execution = "vendor_bill_approval_prep"
    else:
        execution = "report_prep_only"
    receipt = {
        "workflow_job_id": job["workflow_job_id"],
        "client_slug": job["client_slug"],
        "client_name": job["client_name"],
        "job_key": job["job_key"],
        "label": job["label"],
        "due_date": job["due_date"],
        "execution": execution,
        "external_delivery": "disabled",
        "ledger_write": "disabled",
        "tax_or_payment_action": "disabled",
        "payload": payload,
    }
    public_dir = tenancy.lisza_home() / "public"
    workflow_dir = public_dir / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    safe_key = str(job["job_key"]).replace(":", "-").replace("/", "-")
    out = workflow_dir / f"{job['client_slug']}-{safe_key}-{job['due_date']}.json"
    out.write_text(json.dumps(receipt, indent=2))
    receipt["artifact"] = str(out.relative_to(tenancy.lisza_home()))
    if execution == "document_followup" and payload.get("document_request_id"):
        document_requests.record_followup(
            int(payload["document_request_id"]),
            note=f"Prepared follow-up via workflow job {job['workflow_job_id']}",
        )
    return receipt


def run_approved(*, limit: int = 25) -> dict:
    con = _connect()
    receipts = []
    failed = []
    try:
        rows = con.execute(
            """SELECT w.*, c.slug AS client_slug, c.display_name AS client_name
               FROM workflow_jobs w
               JOIN clients c ON c.client_id=w.client_id
               WHERE w.workflow_status='approved'
               ORDER BY w.due_date, c.display_name, w.job_key
               LIMIT ?""",
            (limit,),
        ).fetchall()
        for row in rows:
            job_key = str(row["job_key"])
            runnable = (
                row["job_key"] in RUNNABLE
                or job_key.startswith("document_request:")
                or job_key.startswith("send_invoice:")
                or job_key.startswith("ar_invoice_reminder:")
                or job_key.startswith("ap_bill_approval:")
            )
            if not runnable:
                con.execute(
                    """UPDATE workflow_jobs
                       SET workflow_status='failed', executed_at=datetime('now'),
                           updated_at=datetime('now')
                       WHERE workflow_job_id=?""",
                    (row["workflow_job_id"],),
                )
                _event(con, row["workflow_job_id"], "failed",
                       note="job key is not runnable by the safe report-prep runner")
                failed.append(row["workflow_job_id"])
                continue
            receipt = _write_receipt(row)
            con.execute(
                """UPDATE workflow_jobs
                   SET workflow_status='completed', executed_at=datetime('now'),
                       updated_at=datetime('now')
                   WHERE workflow_job_id=?""",
                (row["workflow_job_id"],),
            )
            _event(con, row["workflow_job_id"], "completed", payload=receipt)
            receipts.append(receipt)
        con.commit()
    finally:
        con.close()
    return {
        "execution": "report_prep_only",
        "completed": len(receipts),
        "failed": len(failed),
        "receipts": receipts,
        "summary": workflow_summary(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    syncp = sub.add_parser("sync", help="sync advisory jobs into workflow state")
    syncp.add_argument("--as-of")
    syncp.add_argument("--include-scheduled", action="store_true")

    listp = sub.add_parser("list", help="list workflow jobs")
    listp.add_argument("--status")
    listp.add_argument("--limit", type=int, default=100)

    approvep = sub.add_parser("approve", help="approve one workflow job")
    approvep.add_argument("job_id", type=int)
    approvep.add_argument("--note")

    skipp = sub.add_parser("skip", help="skip one workflow job")
    skipp.add_argument("job_id", type=int)
    skipp.add_argument("--note")

    runp = sub.add_parser("run-approved", help="run approved safe report-prep jobs")
    runp.add_argument("--limit", type=int, default=25)

    args = ap.parse_args()
    if args.cmd == "sync":
        data = sync_jobs(as_of=_parse_date(args.as_of),
                         include_scheduled=args.include_scheduled)
    elif args.cmd == "list":
        data = list_jobs(status=args.status, limit=args.limit)
    elif args.cmd == "approve":
        data = decide(args.job_id, "approve", note=args.note)
    elif args.cmd == "skip":
        data = decide(args.job_id, "skip", note=args.note)
    elif args.cmd == "run-approved":
        data = run_approved(limit=args.limit)
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
