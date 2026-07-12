#!/usr/bin/env python3
"""Dunning / late-fee escalation for LISZA client books.

Extends the read-only AR reminder planner (ar_ap_workflows.py) into a tiered
escalation ladder plus an explicit, ledger-affecting late-fee charge.

Two layers, deliberately separated:

  * dunning_ladder(...)   — READ ONLY. For each overdue open invoice it reports
      days overdue, the escalation *stage* it has reached, the fee that stage
      would charge, and whether that stage's fee was already assessed. Nothing
      posts. This mirrors the "prepare review actions only" contract of the
      existing AR planner, so a nightly job can surface escalations without
      ever mutating a book.

  * assess_late_fee(...)  — LEDGER AFFECTING. Charges one stage's late fee to
      one invoice. A late fee is a *new charge*, never an edit to the original
      invoice (Principle #2 — we add, we do not subtract), so it posts

          Dr 110 Accounts Receivable   (the customer now owes more)
          Cr <late-fee income>         (finance-charge income booked)

      and records the charge in `dunning_fees`. UNIQUE(invoice_id, stage) makes
      the charge idempotent: a ladder that re-runs daily can never double-bill
      the same stage.

Fee income defaults to 445 "Late Fee & Finance Charge Income" when the book has
it, else 490 "Other Income". Settling an assessed fee (applying a payment to it)
is intentionally out of scope here — the GL is already correct because both the
receivable and the income are booked; fee collection is a later follow-up.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import payments
import tenancy
from post_entry import post_json

AR_CONTROL = "110"                 # Accounts Receivable
LATE_FEE_INCOME = ("445", "490")   # preferred, fallback


def default_policy() -> list[dict]:
    """Built-in escalation ladder used when a book has configured none."""
    return [
        {"stage": 1, "label": "reminder", "min_days_overdue": 1,
         "late_fee_pct": 0.0, "late_fee_flat": 0.0},
        {"stage": 2, "label": "first_notice", "min_days_overdue": 15,
         "late_fee_pct": 1.5, "late_fee_flat": 0.0},
        {"stage": 3, "label": "second_notice", "min_days_overdue": 30,
         "late_fee_pct": 1.5, "late_fee_flat": 0.0},
        {"stage": 4, "label": "final_demand", "min_days_overdue": 60,
         "late_fee_pct": 0.0, "late_fee_flat": 25.0},
    ]


DUNNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS dunning_policy (
    stage INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    min_days_overdue INTEGER NOT NULL,
    late_fee_pct REAL NOT NULL DEFAULT 0,
    late_fee_flat REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dunning_fees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    stage INTEGER NOT NULL,
    label TEXT,
    fee_date TEXT NOT NULL,
    amount REAL NOT NULL,
    income_code TEXT NOT NULL,
    entry_id INTEGER REFERENCES entries(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(invoice_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_dunning_fees_invoice
    ON dunning_fees(invoice_id);
"""


def ensure_dunning_schema(con: sqlite3.Connection) -> None:
    con.executescript(DUNNING_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_dunning_schema(con)
    return con


def get_policy(slug: str) -> list[dict]:
    con = _book(slug)
    try:
        rows = con.execute(
            "SELECT stage, label, min_days_overdue, late_fee_pct, late_fee_flat "
            "FROM dunning_policy ORDER BY min_days_overdue, stage"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return default_policy()
    return [dict(r) for r in rows]


def set_policy(slug: str, stages: list[dict]) -> list[dict]:
    """Replace the book's escalation ladder (config, not a posted ledger)."""
    con = _book(slug)
    try:
        con.execute("DELETE FROM dunning_policy")
        for s in stages:
            con.execute(
                "INSERT INTO dunning_policy"
                "(stage, label, min_days_overdue, late_fee_pct, late_fee_flat)"
                " VALUES (?,?,?,?,?)",
                (int(s["stage"]), str(s["label"]), int(s["min_days_overdue"]),
                 float(s.get("late_fee_pct") or 0), float(s.get("late_fee_flat") or 0)),
            )
        con.commit()
    finally:
        con.close()
    return get_policy(slug)


def _stage_for(days_overdue: int, policy: list[dict]) -> dict | None:
    """Highest-min stage whose threshold the overdue count has crossed."""
    reached = [s for s in policy if days_overdue >= s["min_days_overdue"]]
    if not reached:
        return None
    return max(reached, key=lambda s: s["min_days_overdue"])


def _fee_for(stage: dict, balance: float) -> float:
    return round(balance * float(stage["late_fee_pct"]) / 100.0
                 + float(stage["late_fee_flat"]), 2)


def _as_of_date(as_of: str | None) -> date:
    return date.fromisoformat(as_of) if as_of else date.today()


def _assessed_stages(con: sqlite3.Connection, invoice_id: int) -> set[int]:
    return {
        r["stage"] for r in con.execute(
            "SELECT stage FROM dunning_fees WHERE invoice_id=?", (invoice_id,)
        )
    }


def dunning_ladder(slug: str, *, as_of: str | None = None) -> list[dict]:
    """Overdue open invoices with their escalation stage and suggested fee."""
    ref = _as_of_date(as_of)
    policy = get_policy(slug)
    con = _book(slug)
    try:
        rows = con.execute(
            "SELECT id, party, due_date, amount FROM invoices "
            "WHERE status='open' ORDER BY due_date, id"
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            due = date.fromisoformat(r["due_date"])
            days_overdue = (ref - due).days
            if days_overdue < 1:
                continue
            balance = round(
                float(r["amount"])
                - payments._allocated_so_far(con, "invoice", r["id"]), 2)
            if balance <= 0:
                continue
            stage = _stage_for(days_overdue, policy)
            if stage is None:
                continue
            assessed = _assessed_stages(con, r["id"])
            out.append({
                "invoice_id": r["id"],
                "party": r["party"],
                "due_date": r["due_date"],
                "days_overdue": days_overdue,
                "stage": stage["stage"],
                "stage_label": stage["label"],
                "balance": balance,
                "suggested_fee": _fee_for(stage, balance),
                "fee_assessed": stage["stage"] in assessed,
            })
        return out
    finally:
        con.close()


def invoice_dunning_state(slug: str, invoice_id: int,
                          *, as_of: str | None = None) -> dict:
    """Original balance + assessed late fees = total currently owed."""
    ref = _as_of_date(as_of)
    policy = get_policy(slug)
    con = _book(slug)
    try:
        row = con.execute(
            "SELECT party, due_date, amount, status FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown invoice: {invoice_id}")
        base_balance = round(
            float(row["amount"])
            - payments._allocated_so_far(con, "invoice", invoice_id), 2)
        fees = round(float(con.execute(
            "SELECT COALESCE(SUM(amount),0) FROM dunning_fees WHERE invoice_id=?",
            (invoice_id,)).fetchone()[0] or 0), 2)
        due = date.fromisoformat(row["due_date"])
        days_overdue = (ref - due).days
        stage = _stage_for(days_overdue, policy) if days_overdue >= 1 else None
        return {
            "invoice_id": invoice_id,
            "party": row["party"],
            "due_date": row["due_date"],
            "status": row["status"],
            "days_overdue": days_overdue,
            "stage": stage["stage"] if stage else None,
            "stage_label": stage["label"] if stage else None,
            "original": round(float(row["amount"]), 2),
            "base_balance": base_balance,
            "fees": fees,
            "total_due": round(base_balance + fees, 2),
        }
    finally:
        con.close()


def _resolve_income(con: sqlite3.Connection, income_code: str | None) -> str:
    if income_code:
        acct = con.execute(
            "SELECT type FROM accounts WHERE code=? AND active=1", (income_code,)
        ).fetchone()
        if not acct:
            raise ValueError(f"income account '{income_code}' not found")
        if acct["type"] != "income":
            raise ValueError(
                f"account '{income_code}' is {acct['type']}, not income")
        return income_code
    for code in LATE_FEE_INCOME:
        if con.execute(
            "SELECT 1 FROM accounts WHERE code=? AND active=1 AND type='income'",
            (code,),
        ).fetchone():
            return code
    raise ValueError("no income account available for late fees")


def assess_late_fee(
    slug: str,
    invoice_id: int,
    *,
    stage: int | None = None,
    amount: float | None = None,
    income_code: str | None = None,
    fee_date: str | None = None,
    as_of: str | None = None,
) -> dict:
    """Charge one escalation stage's late fee to an overdue invoice.

    Posts Dr 110 A/R / Cr <income> and records the charge. Idempotent per
    (invoice, stage). `stage`/`amount` override the policy when supplied.
    """
    ref = _as_of_date(as_of)
    policy = get_policy(slug)
    con = _book(slug)
    try:
        inv = con.execute(
            "SELECT party, due_date, amount, status FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone()
        if not inv:
            raise ValueError(f"unknown invoice: {invoice_id}")
        if inv["status"] != "open":
            raise ValueError(
                f"invoice {invoice_id} is {inv['status']}, not open")

        due = date.fromisoformat(inv["due_date"])
        days_overdue = (ref - due).days
        if days_overdue < 1:
            raise ValueError(
                f"invoice {invoice_id} is not overdue (due {inv['due_date']})")

        if stage is not None:
            stage_row = next((s for s in policy if s["stage"] == int(stage)), None)
            if stage_row is None:
                raise ValueError(f"unknown dunning stage: {stage}")
        else:
            stage_row = _stage_for(days_overdue, policy)
            if stage_row is None:
                raise ValueError(
                    f"invoice {invoice_id} has not reached any dunning stage")

        if stage_row["stage"] in _assessed_stages(con, invoice_id):
            raise ValueError(
                f"stage {stage_row['stage']} fee already assessed for "
                f"invoice {invoice_id}")

        balance = round(
            float(inv["amount"])
            - payments._allocated_so_far(con, "invoice", invoice_id), 2)
        fee = (round(float(amount), 2) if amount is not None
               else _fee_for(stage_row, balance))
        if fee <= 0:
            raise ValueError(
                f"late fee for stage {stage_row['stage']} computes to $0 — "
                "nothing to charge")

        income = _resolve_income(con, income_code)
        charge_date = fee_date or ref.isoformat()

        # Post the balanced GL entry first (separate connection — WAL-safe).
        posted = post_json(
            {
                "entry_date": charge_date,
                "description":
                    f"Late fee ({stage_row['label']}) — "
                    f"{inv['party']} invoice #{invoice_id}",
                "payee": inv["party"],
                "post": True,
                "splits": [
                    {"account": AR_CONTROL, "dr": fee, "cr": 0, "memo": "late fee"},
                    {"account": income, "dr": 0, "cr": fee, "memo": "late fee income"},
                ],
            },
            str(tenancy.resolve_db(slug)),
        )
        entry_id = posted["entry_id"]

        con.execute(
            "INSERT INTO dunning_fees"
            "(invoice_id, stage, label, fee_date, amount, income_code, entry_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (invoice_id, stage_row["stage"], stage_row["label"], charge_date,
             fee, income, entry_id),
        )
        con.commit()
        return {
            "invoice_id": invoice_id,
            "stage": stage_row["stage"],
            "stage_label": stage_row["label"],
            "amount": fee,
            "income_code": income,
            "entry_id": entry_id,
            "status": posted["status"],
        }
    finally:
        con.close()


def list_fees(slug: str) -> list[dict]:
    con = _book(slug)
    try:
        rows = con.execute(
            "SELECT id, invoice_id, stage, label, fee_date, amount, income_code, "
            "entry_id FROM dunning_fees ORDER BY fee_date, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---- CLI ----------------------------------------------------------------

def _cli_dun_json(argv) -> None:
    """`dun-json --client <slug> --payload <json>` → JSON result.

    payload = {"action": "ladder"|"assess"|"state"|"fees"|"policy", ...}
    Spawned by the /api/lisza route, mirroring payments.py pay-json.
    """
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="dunning.py dun-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else __import__("sys").stdin.read()
        p = json.loads(raw)
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        action = p.get("action") or "ladder"
        if action == "ladder":
            print(json.dumps({"ok": True,
                              "ladder": dunning_ladder(a.client, as_of=p.get("as_of"))}))
        elif action == "assess":
            res = assess_late_fee(
                a.client, int(p["invoice_id"]),
                stage=p.get("stage"), amount=p.get("amount"),
                income_code=p.get("income_code"), fee_date=p.get("fee_date"),
                as_of=p.get("as_of"))
            print(json.dumps({"ok": True, **res}))
        elif action == "state":
            print(json.dumps({"ok": True, **invoice_dunning_state(
                a.client, int(p["invoice_id"]), as_of=p.get("as_of"))}))
        elif action == "fees":
            print(json.dumps({"ok": True, "fees": list_fees(a.client)}))
        elif action == "policy":
            if p.get("stages"):
                pol = set_policy(a.client, p["stages"])
            else:
                pol = get_policy(a.client)
            print(json.dumps({"ok": True, "policy": pol}))
        else:
            print(json.dumps({"error": f"unknown action: {action}"}))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:  # surface unexpected failures as JSON, never a traceback
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main() -> int:
    argv = __import__("sys").argv[1:]
    if argv and argv[0] == "dun-json":
        _cli_dun_json(argv[1:])
        return 0

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("ladder", help="overdue invoices with escalation stage")
    l.add_argument("client_slug")
    l.add_argument("--as-of")

    a = sub.add_parser("assess", help="charge a late fee to an overdue invoice")
    a.add_argument("client_slug")
    a.add_argument("--invoice", type=int, required=True)
    a.add_argument("--stage", type=int)
    a.add_argument("--amount", type=float)
    a.add_argument("--income-code")
    a.add_argument("--fee-date")
    a.add_argument("--as-of")

    f = sub.add_parser("fees", help="list assessed late fees")
    f.add_argument("client_slug")

    args = ap.parse_args()
    if args.cmd == "ladder":
        print(json.dumps(dunning_ladder(args.client_slug, as_of=args.as_of), indent=2))
    elif args.cmd == "assess":
        print(json.dumps(assess_late_fee(
            args.client_slug, args.invoice, stage=args.stage, amount=args.amount,
            income_code=args.income_code, fee_date=args.fee_date,
            as_of=args.as_of), indent=2))
    elif args.cmd == "fees":
        print(json.dumps(list_fees(args.client_slug), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
