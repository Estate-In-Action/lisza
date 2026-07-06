#!/usr/bin/env python3
"""Approve, edit, or reject LISZA pending_inbox rows without prompts."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tenancy import resolve_db


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAYMENT_ACCOUNT = "102"


def iso_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    ensure_pending_inbox(con)
    return con


def ensure_pending_inbox(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_inbox (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            source      TEXT NOT NULL,
            raw_path    TEXT,
            parsed_json TEXT,
            suggested_account TEXT,
            suggested_amount  REAL,
            status      TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','reviewed','posted','rejected')),
            entry_id    INTEGER REFERENCES entries(id),
            notes       TEXT
        )
        """
    )


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["parsed_json"]
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _coded(payload: dict[str, Any]) -> dict[str, Any]:
    coded = payload.get("coded")
    return coded if isinstance(coded, dict) else payload


def _account_exists(con: sqlite3.Connection, code: str) -> bool:
    return con.execute(
        "SELECT 1 FROM accounts WHERE code=? AND active=1", (code,)
    ).fetchone() is not None


def _append_note(existing: str | None, note: str) -> str:
    current = (existing or "").strip()
    return f"{current}\n{note}".strip() if current else note


def _entry_fields(row: sqlite3.Row, payload: dict[str, Any]) -> tuple[str, str, str | None]:
    coded = _coded(payload)
    entry_date = (
        coded.get("date")
        or payload.get("txn_date")
        or payload.get("transaction_date")
        or payload.get("post_date")
        or date.today().isoformat()
    )
    payee = (
        coded.get("vendor")
        or payload.get("payee")
        or payload.get("merchant")
        or payload.get("description")
        or None
    )
    description = (
        payload.get("description")
        or coded.get("category_hint")
        or f"Pending inbox #{row['id']}"
    )
    return str(entry_date), str(description), str(payee) if payee else None


def _payment_account(payload: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    coded = _coded(payload)
    return str(
        coded.get("suggested_payment_account_code")
        or payload.get("suggested_payment_account_code")
        or payload.get("payment_account")
        or DEFAULT_PAYMENT_ACCOUNT
    )


def approve_row(
    row_id: int,
    *,
    db_path: str | Path,
    account_override: str | None = None,
    amount_override: float | None = None,
    payment_account: str | None = None,
    actor: str = "post_pending",
) -> int | None:
    """Post a reviewed pending row as a balanced two-line transaction.

    Returns the new entries.id. Returns None when the row is absent or no longer
    in reviewed state, which makes repeated or racing approval attempts safe.
    """
    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM pending_inbox WHERE id=?", (row_id,)).fetchone()
        if not row or row["status"] != "reviewed":
            con.execute("ROLLBACK")
            return None

        payload = _payload(row)
        expense_account = str(account_override or row["suggested_account"] or "").strip()
        if not expense_account:
            raise ValueError("suggested account is required")
        if not _account_exists(con, expense_account):
            raise ValueError(f"account '{expense_account}' not found")

        pay_account = _payment_account(payload, payment_account)
        if not _account_exists(con, pay_account):
            raise ValueError(f"payment account '{pay_account}' not found")

        amount = float(amount_override if amount_override is not None else row["suggested_amount"] or 0)
        if amount <= 0:
            raise ValueError("amount must be positive")

        entry_date, description, payee = _entry_fields(row, payload)
        now = iso_now()
        note = _append_note(row["notes"], f"approved by {actor} at {now}")
        cur = con.execute(
            """INSERT INTO entries
               (entry_date, description, payee, source, source_ref, status, posted_at, created_at, notes)
               VALUES (?, ?, ?, ?, ?, 'posted', ?, ?, ?)""",
            (entry_date, description, payee, row["source"], f"inbox:{row_id}", now, now, note),
        )
        entry_id = int(cur.lastrowid)
        con.execute(
            "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, ?, 0, ?)",
            (entry_id, expense_account, amount, f"inbox:{row_id}"),
        )
        con.execute(
            "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, 0, ?, ?)",
            (entry_id, pay_account, amount, f"inbox:{row_id}"),
        )
        updated = con.execute(
            """UPDATE pending_inbox
                  SET status='posted', entry_id=?, notes=?
                WHERE id=? AND status='reviewed'""",
            (entry_id, note, row_id),
        ).rowcount
        if updated != 1:
            con.execute("ROLLBACK")
            return None
        con.execute("COMMIT")
        return entry_id
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def reject_row(
    row_id: int,
    *,
    db_path: str | Path,
    reason: str = "",
    actor: str = "post_pending",
) -> bool:
    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id, status, notes FROM pending_inbox WHERE id=?", (row_id,)).fetchone()
        if not row or row["status"] != "reviewed":
            con.execute("ROLLBACK")
            return False
        now = iso_now()
        detail = f"rejected by {actor} at {now}"
        if reason:
            detail += f": {reason}"
        note = _append_note(row["notes"], detail)
        changed = con.execute(
            "UPDATE pending_inbox SET status='rejected', notes=? WHERE id=? AND status='reviewed'",
            (note, row_id),
        ).rowcount
        con.execute("COMMIT")
        return changed == 1
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def edit_row(
    row_id: int,
    *,
    db_path: str | Path,
    field: str,
    value: Any,
    actor: str = "post_pending",
) -> bool:
    allowed = {"account", "amount", "date", "payee", "description", "memo"}
    if field not in allowed:
        raise ValueError(f"unsupported edit field '{field}'")

    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM pending_inbox WHERE id=?", (row_id,)).fetchone()
        if not row or row["status"] != "reviewed":
            con.execute("ROLLBACK")
            return False

        payload = _payload(row)
        coded = dict(_coded(payload))
        note_value = value
        updates: dict[str, Any] = {}
        if field == "account":
            code = str(value).strip()
            if not _account_exists(con, code):
                raise ValueError(f"account '{code}' not found")
            updates["suggested_account"] = code
        elif field == "amount":
            amount = float(value)
            if amount <= 0:
                raise ValueError("amount must be positive")
            updates["suggested_amount"] = amount
            note_value = f"{amount:.2f}"
        else:
            coded[field] = value
            if "coded" in payload and isinstance(payload.get("coded"), dict):
                payload["coded"] = coded
            else:
                payload = coded
            updates["parsed_json"] = json.dumps(payload)

        now = iso_now()
        note = _append_note(row["notes"], f"edited {field}={note_value} by {actor} at {now}")
        if "suggested_account" in updates:
            con.execute(
                """UPDATE pending_inbox
                      SET suggested_account=?, notes=?
                    WHERE id=? AND status='reviewed'""",
                (updates["suggested_account"], note, row_id),
            )
        elif "suggested_amount" in updates:
            con.execute(
                """UPDATE pending_inbox
                      SET suggested_amount=?, notes=?
                    WHERE id=? AND status='reviewed'""",
                (updates["suggested_amount"], note, row_id),
            )
        else:
            con.execute(
                """UPDATE pending_inbox
                      SET parsed_json=?, notes=?
                    WHERE id=? AND status='reviewed'""",
                (updates["parsed_json"], note, row_id),
            )
        con.execute("COMMIT")
        return True
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def _db_from_args(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db)
    return resolve_db(args.client) if args.client else ROOT / "ledger.db"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="ledger database path")
    ap.add_argument("--client", help="client slug; ignored when --db is set")
    ap.add_argument("--row-id", type=int, required=True)
    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--approve", action="store_true")
    action.add_argument("--reject", action="store_true")
    action.add_argument("--edit")
    ap.add_argument("--account")
    ap.add_argument("--amount", type=float)
    ap.add_argument("--payment-account")
    ap.add_argument("--reason", default="")
    args = ap.parse_args()

    db_path = _db_from_args(args)
    if args.approve:
        entry_id = approve_row(
            args.row_id,
            db_path=db_path,
            account_override=args.account,
            amount_override=args.amount,
            payment_account=args.payment_account,
            actor="cli",
        )
        print(json.dumps({"status": "posted" if entry_id else "not_actioned", "entry_id": entry_id}))
    elif args.reject:
        rejected = reject_row(args.row_id, db_path=db_path, reason=args.reason, actor="cli")
        print(json.dumps({"status": "rejected" if rejected else "not_actioned"}))
    else:
        if "=" not in args.edit:
            raise SystemExit("--edit expects field=value")
        field, value = args.edit.split("=", 1)
        edited = edit_row(args.row_id, db_path=db_path, field=field.strip(), value=value.strip(), actor="cli")
        print(json.dumps({"status": "edited" if edited else "not_actioned"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
