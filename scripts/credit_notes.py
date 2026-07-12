#!/usr/bin/env python3
"""Credit notes / debit notes / vendor credits for LISZA client books.

The credit-document side of the money loop. Where payments.py relieves a
receivable/payable with *cash*, a credit note relieves it by *reversing the
original sale or purchase* — no cash moves. Two kinds:

  customer credit (sales credit memo)  Dr <revenue>  / Cr 110 Accounts Receivable
  vendor credit  (debit note)          Dr 200 A/P    / Cr <expense>

Posting always credits/debits the full document amount to the control account,
so an unapplied credit sits as a customer/vendor credit balance in AR/AP — the
standard on-account treatment, exactly as an over-payment does in payments.py.

This honours Principle #2 (we add, we don't subtract): the original invoice/bill
and its GL entry are never mutated; the credit is a *new* offsetting document.
An invoice/bill is relieved to 'paid' once payments **and** credits together
cover it, so the two modules compose.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import tenancy
from post_entry import post_json

AR_CONTROL = "110"   # Accounts Receivable
AP_CONTROL = "200"   # Accounts Payable

_KIND = {
    "customer": {
        "table": "invoices",
        "target_type": "invoice",
        "open_status": "open",
        "control": AR_CONTROL,
        "offset_type": "income",
    },
    "vendor": {
        "table": "bills",
        "target_type": "bill",
        "open_status": "unpaid",
        "control": AP_CONTROL,
        "offset_type": "expense",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS credit_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('customer','vendor')),
    party TEXT,
    credit_date TEXT NOT NULL,
    amount REAL NOT NULL,
    offset_code TEXT NOT NULL REFERENCES accounts(code),
    reference TEXT,
    memo TEXT,
    entry_id INTEGER REFERENCES entries(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credit_note_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    credit_note_id INTEGER NOT NULL REFERENCES credit_notes(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK(target_type IN ('invoice','bill')),
    target_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cnalloc_note ON credit_note_allocations(credit_note_id);
CREATE INDEX IF NOT EXISTS idx_cnalloc_target
    ON credit_note_allocations(target_type, target_id);
"""


def ensure_credit_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_credit_schema(con)
    return con


def _relieved_so_far(con: sqlite3.Connection, target_type: str, target_id: int) -> float:
    """Total relief on a target from cash payments AND credit notes combined.

    Both sub-ledgers count against the same invoice/bill, so the true remaining
    balance is amount minus the sum of both. Each table is queried defensively
    so a book that has only ever used one module still resolves.
    """
    total = 0.0
    for table, col in (("payment_allocations", "payment_id"),
                       ("credit_note_allocations", "credit_note_id")):
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        row = con.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM {table} "
            "WHERE target_type=? AND target_id=?",
            (target_type, target_id),
        ).fetchone()
        total += float(row[0] or 0)
    return round(total, 2)


def target_balance(slug: str, target_type: str, target_id: int) -> float:
    """Remaining balance on an invoice/bill (amount minus payments and credits)."""
    table = "invoices" if target_type == "invoice" else "bills"
    con = _book(slug)
    try:
        row = con.execute(f"SELECT amount FROM {table} WHERE id=?", (target_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown {target_type}: {target_id}")
        return round(float(row["amount"]) - _relieved_so_far(con, target_type, target_id), 2)
    finally:
        con.close()


def record_credit_note(
    slug: str,
    *,
    kind: str,
    amount: float,
    offset_code: str,
    credit_date: str | None = None,
    party: str | None = None,
    reference: str | None = None,
    memo: str | None = None,
    allocations: list[dict] | None = None,
) -> dict:
    """Issue a credit note, post its reversing GL entry, and apply it.

    kind='customer' reverses revenue against AR; kind='vendor' reverses expense
    against AP. allocations: [{"target_id": int, "amount": float}, ...] against
    open invoices (customer) or unpaid bills (vendor). Sum of allocations may be
    less than `amount` (rest is an unapplied on-account credit) but never more.
    """
    spec = _KIND.get(kind)
    if not spec:
        raise ValueError(f"invalid kind: {kind!r} (customer|vendor)")

    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("credit amount must be positive")

    credit_date = credit_date or date.today().isoformat()
    allocations = allocations or []

    con = _book(slug)
    try:
        offset = con.execute(
            "SELECT type FROM accounts WHERE code=? AND active=1", (offset_code,)
        ).fetchone()
        if not offset:
            raise ValueError(f"offset account '{offset_code}' not found")
        if offset["type"] != spec["offset_type"]:
            raise ValueError(
                f"{kind} credit needs an {spec['offset_type']} offset account, "
                f"got {offset_code} ({offset['type']})")

        # Validate each allocation against its target's remaining balance.
        norm_allocs: list[tuple[int, float]] = []
        alloc_total = 0.0
        for a in allocations:
            tid = int(a["target_id"])
            amt = round(float(a["amount"]), 2)
            if amt <= 0:
                raise ValueError("allocation amount must be positive")
            row = con.execute(
                f"SELECT amount, status FROM {spec['table']} WHERE id=?", (tid,)
            ).fetchone()
            if not row:
                raise ValueError(
                    f"{spec['target_type']} {tid} not found for a {kind} credit")
            if row["status"] != spec["open_status"]:
                raise ValueError(
                    f"{spec['target_type']} {tid} is {row['status']}, not "
                    f"{spec['open_status']}")
            bal = round(float(row["amount"]) - _relieved_so_far(
                con, spec["target_type"], tid), 2)
            if amt > bal + 0.005:
                raise ValueError(
                    f"allocation ${amt:.2f} exceeds {spec['target_type']} {tid} "
                    f"balance ${bal:.2f}")
            norm_allocs.append((tid, amt))
            alloc_total += amt

        alloc_total = round(alloc_total, 2)
        if alloc_total > amount + 0.005:
            raise ValueError(
                f"allocations ${alloc_total:.2f} exceeds credit amount "
                f"${amount:.2f}")

        # Post the balanced reversing entry (full amount to the control acct).
        if kind == "customer":
            splits = [
                {"account": offset_code, "dr": amount, "cr": 0, "memo": "credit memo"},
                {"account": spec["control"], "dr": 0, "cr": amount, "memo": "A/R"},
            ]
        else:
            splits = [
                {"account": spec["control"], "dr": amount, "cr": 0, "memo": "A/P"},
                {"account": offset_code, "dr": 0, "cr": amount, "memo": "vendor credit"},
            ]
        desc = memo or (
            f"{'Credit memo to' if kind == 'customer' else 'Vendor credit from'} "
            f"{party or 'party'}")
        posted = post_json(
            {
                "entry_date": credit_date,
                "description": desc,
                "payee": party,
                "post": True,
                "splits": splits,
            },
            str(tenancy.resolve_db(slug)),
        )
        entry_id = posted["entry_id"]

        cur = con.execute(
            """INSERT INTO credit_notes
               (kind, party, credit_date, amount, offset_code, reference, memo, entry_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (kind, party, credit_date, amount, offset_code, reference, memo, entry_id),
        )
        credit_id = cur.lastrowid

        targets_paid = []
        for tid, amt in norm_allocs:
            con.execute(
                """INSERT INTO credit_note_allocations
                   (credit_note_id, target_type, target_id, amount)
                   VALUES (?,?,?,?)""",
                (credit_id, spec["target_type"], tid, amt),
            )
            orig = con.execute(
                f"SELECT amount FROM {spec['table']} WHERE id=?", (tid,)
            ).fetchone()["amount"]
            if _relieved_so_far(con, spec["target_type"], tid) >= round(
                    float(orig), 2) - 0.005:
                con.execute(
                    f"UPDATE {spec['table']} SET status='paid', paid_date=? WHERE id=?",
                    (credit_date, tid),
                )
                targets_paid.append(tid)

        con.commit()
        return {
            "credit_note_id": credit_id,
            "entry_id": entry_id,
            "status": posted["status"],
            "amount": amount,
            "allocated": alloc_total,
            "unapplied": round(amount - alloc_total, 2),
            "targets_paid": targets_paid,
        }
    finally:
        con.close()


def list_credit_notes(slug: str) -> list[dict]:
    con = _book(slug)
    try:
        rows = con.execute(
            """SELECT c.*, (
                   SELECT COALESCE(SUM(amount),0) FROM credit_note_allocations
                   WHERE credit_note_id=c.id) AS allocated
               FROM credit_notes c ORDER BY c.credit_date, c.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def open_credits(slug: str, kind: str) -> list[dict]:
    """Credit notes with unapplied balance (on-account credits) of a given kind."""
    if kind not in _KIND:
        raise ValueError(f"invalid kind: {kind!r}")
    return [
        {**c, "unapplied": round(c["amount"] - c["allocated"], 2)}
        for c in list_credit_notes(slug)
        if c["kind"] == kind and round(c["amount"] - c["allocated"], 2) > 0.005
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_credit_json(argv) -> None:
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="credit_notes.py credit-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else sys.stdin.read()
        p = json.loads(raw)
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        res = record_credit_note(
            a.client,
            kind=str(p.get("kind") or ""),
            amount=p.get("amount") or 0,
            offset_code=str(p.get("offset_code") or ""),
            credit_date=p.get("credit_date"),
            party=p.get("party"),
            reference=p.get("reference"),
            memo=p.get("memo"),
            allocations=p.get("allocations") or [],
        )
        print(json.dumps({"ok": True, **res}))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:  # surface unexpected failures as JSON, never a traceback
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main() -> int:
    import sys
    argv = sys.argv[1:]
    if argv and argv[0] == "credit-json":
        _cli_credit_json(argv[1:])
        return 0

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("credit", help="issue and apply a credit note")
    c.add_argument("client_slug")
    c.add_argument("--kind", choices=["customer", "vendor"], required=True)
    c.add_argument("--amount", type=float, required=True)
    c.add_argument("--offset-code", required=True)
    c.add_argument("--date")
    c.add_argument("--party")
    c.add_argument("--reference")
    c.add_argument("--memo")
    c.add_argument("--allocations", help='JSON: [{"target_id":1,"amount":100.0}]')

    ls = sub.add_parser("list", help="list credit notes")
    ls.add_argument("client_slug")

    op = sub.add_parser("open", help="list on-account (unapplied) credits")
    op.add_argument("client_slug")
    op.add_argument("--kind", choices=["customer", "vendor"], default="customer")

    args = ap.parse_args()
    if args.cmd == "credit":
        allocs = json.loads(args.allocations) if args.allocations else []
        res = record_credit_note(
            args.client_slug, kind=args.kind, amount=args.amount,
            offset_code=args.offset_code, credit_date=args.date, party=args.party,
            reference=args.reference, memo=args.memo, allocations=allocs)
        print(json.dumps(res, indent=2))
    elif args.cmd == "list":
        print(json.dumps(list_credit_notes(args.client_slug), indent=2, default=str))
    elif args.cmd == "open":
        print(json.dumps(open_credits(args.client_slug, args.kind), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
