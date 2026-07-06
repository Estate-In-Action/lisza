#!/usr/bin/env python3
"""AP/AR workflow planners for LISZA.

These jobs prepare review actions only. They do not send reminders, approve
bills, pay vendors, post ledger entries, or mutate client books.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import tenancy


def _status_for_due(due_date: str, as_of: date) -> str:
    due = date.fromisoformat(due_date)
    if due < as_of:
        return "overdue"
    if due <= as_of + timedelta(days=7):
        return "due_now"
    return "upcoming"


def _invoice_items(slug: str, display_name: str, as_of: date) -> list[dict]:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT id, party, due_date, amount
               FROM invoices
               WHERE status='open'
                 AND due_date <= ?
               ORDER BY due_date, id""",
            ((as_of + timedelta(days=7)).isoformat(),),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "client_slug": slug,
            "client_name": display_name,
            "key": f"ar_invoice_reminder:{row['id']}",
            "label": f"AR reminder: {row['party']} invoice #{row['id']}",
            "due_date": row["due_date"],
            "status": _status_for_due(row["due_date"], as_of),
            "source": "ar_open_invoice",
            "approval_required": True,
            "invoice_id": row["id"],
            "party": row["party"],
            "amount": float(row["amount"] or 0),
            "execution": "client_payment_reminder_prep",
        }
        for row in rows
    ]


def _bill_items(slug: str, display_name: str, as_of: date) -> list[dict]:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT id, party, due_date, amount
               FROM bills
               WHERE status='unpaid'
                 AND due_date <= ?
               ORDER BY due_date, id""",
            ((as_of + timedelta(days=7)).isoformat(),),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "client_slug": slug,
            "client_name": display_name,
            "key": f"ap_bill_approval:{row['id']}",
            "label": f"AP approval review: {row['party']} bill #{row['id']}",
            "due_date": row["due_date"],
            "status": _status_for_due(row["due_date"], as_of),
            "source": "ap_unpaid_bill",
            "approval_required": True,
            "bill_id": row["id"],
            "party": row["party"],
            "amount": float(row["amount"] or 0),
            "execution": "vendor_bill_approval_prep",
        }
        for row in rows
    ]


def workflow_items(*, as_of: date | None = None) -> list[dict]:
    ref = as_of or date.today()
    items: list[dict] = []
    for client in tenancy.list_clients():
        items.extend(_invoice_items(client.slug, client.display_name, ref))
        items.extend(_bill_items(client.slug, client.display_name, ref))
    return items
