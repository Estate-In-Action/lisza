#!/usr/bin/env python3
"""Approval-gated online-payment request preparation for LISZA invoices."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import payments
import tenancy

SCHEMA = """
CREATE TABLE IF NOT EXISTS online_payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK(status IN ('pending_approval','approved','cancelled','dispatched','settled')),
    approved_by TEXT,
    approved_at TEXT,
    provider_reference TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(invoice_id)
);
"""


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def prepare_request(slug: str, invoice_id: int, *, commit: bool = False) -> dict:
    con = _book(slug)
    try:
        invoice = con.execute(
            "SELECT id, party, amount, status FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        if not invoice:
            raise ValueError(f"unknown invoice: {invoice_id}")
        if invoice["status"] != "open":
            raise ValueError(f"invoice {invoice_id} is {invoice['status']}")
        amount = payments.target_balance(slug, "invoice", invoice_id)
        if amount <= 0:
            raise ValueError(f"invoice {invoice_id} has no collectible balance")
        result = {
            "invoice_id": invoice_id,
            "party": invoice["party"],
            "amount": amount,
            "currency": "USD",
            "status": "pending_approval",
            "approval_required": True,
            "provider_call": False,
            "ledger_posted": False,
        }
        if not commit:
            return {**result, "dry_run": True}
        con.execute(
            """INSERT INTO online_payment_requests(invoice_id, amount)
               VALUES (?, ?) ON CONFLICT(invoice_id) DO NOTHING""",
            (invoice_id, amount),
        )
        con.commit()
        row = con.execute(
            "SELECT id, status FROM online_payment_requests WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()
        return {**result, "request_id": row["id"], "status": row["status"], "dry_run": False}
    finally:
        con.close()


def approve_request(slug: str, request_id: int, *, approved_by: str) -> dict:
    if not approved_by.strip():
        raise ValueError("approved_by is required")
    con = _book(slug)
    try:
        row = con.execute(
            "SELECT status FROM online_payment_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"unknown payment request: {request_id}")
        if row["status"] != "pending_approval":
            raise ValueError(f"payment request {request_id} is {row['status']}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            """UPDATE online_payment_requests
               SET status='approved', approved_by=?, approved_at=? WHERE id=?""",
            (approved_by.strip(), now, request_id),
        )
        con.commit()
        return {"request_id": request_id, "status": "approved", "provider_call": False}
    finally:
        con.close()


def checkout_payload(slug: str, request_id: int, *, success_url: str, cancel_url: str) -> dict:
    if os.environ.get("LISZA_ONLINE_PAYMENTS_ENABLED") != "1":
        raise PermissionError("online payment dispatch is disabled")
    con = _book(slug)
    try:
        row = con.execute(
            """SELECT r.*, i.party FROM online_payment_requests r
               JOIN invoices i ON i.id=r.invoice_id WHERE r.id=?""",
            (request_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown payment request: {request_id}")
        if row["status"] != "approved":
            raise PermissionError("payment request requires explicit approval")
        return {
            "request_id": request_id,
            "invoice_id": row["invoice_id"],
            "party": row["party"],
            "amount_minor": round(float(row["amount"]) * 100),
            "currency": row["currency"].lower(),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "provider_call": False,
            "settlement_authority": False,
        }
    finally:
        con.close()
