#!/usr/bin/env python3
"""Recurring / subscription invoicing for LISZA client books.

Schedule-driven auto-generation of invoices for the predictable, repeating
client need (rent, retainers, subscriptions, monthly service fees). The posture
mirrors the rest of LISZA: the planner is advisory, and nothing posts to a book
without either explicit per-run approval or a per-template ``auto_approve``
opt-in.

Design note: docs/specs/2026-07-11-recurring-invoicing-design.md.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta

import number_series
import tenancy
from automation_profile import _last_day_of_month
from post_entry import post_json

FREQUENCIES = ("weekly", "monthly", "quarterly", "annual")
_MONTHS_PER = {"monthly": 1, "quarterly": 3, "annual": 12}

SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring_invoice_templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    party         TEXT NOT NULL,
    amount        REAL NOT NULL,
    memo          TEXT,
    revenue_code  TEXT NOT NULL REFERENCES accounts(code),
    net_terms     INTEGER NOT NULL DEFAULT 30,
    frequency     TEXT NOT NULL CHECK(frequency IN
                    ('weekly','monthly','quarterly','annual')),
    anchor_day    INTEGER,
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    next_run_date TEXT NOT NULL,
    auto_approve  INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    series_key    TEXT NOT NULL DEFAULT 'invoice',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recurring_invoice_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id   INTEGER NOT NULL REFERENCES recurring_invoice_templates(id),
    period_key    TEXT NOT NULL,
    invoice_id    INTEGER REFERENCES invoices(id),
    document_number TEXT,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by   TEXT,
    UNIQUE(template_id, period_key)
);
"""


def ensure_recurring_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    ensure_recurring_schema(con)
    return con


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + n
    return idx // 12, idx % 12 + 1


def _resolve_day(year: int, month: int, anchor_day: int | None) -> int:
    eom = _last_day_of_month(date(year, month, 1)).day
    if anchor_day is None or anchor_day == -1 or anchor_day > eom:
        return eom
    return anchor_day


def next_period(frequency: str, anchor_day: int | None, current: date) -> date:
    """Next issue date after ``current`` for this frequency, on ``anchor_day``.

    Month-based frequencies land on ``anchor_day`` within the target month;
    ``anchor_day == -1`` (or an anchor past the month's length) resolves to the
    last calendar day, so an EOM template tracks 31 -> Feb 28/29 -> 31 correctly.
    Weekly advances seven days and ignores ``anchor_day``.
    """
    if frequency not in FREQUENCIES:
        raise ValueError(f"unknown frequency: {frequency!r}")
    if frequency == "weekly":
        return date.fromordinal(current.toordinal() + 7)
    year, month = _add_months(current.year, current.month, _MONTHS_PER[frequency])
    return date(year, month, _resolve_day(year, month, anchor_day))


def period_key(frequency: str, issue_date: date) -> str:
    """Dedup key for an issued period: 2026-07 | 2026-Q3 | 2026-W28 | 2026."""
    if frequency == "monthly":
        return f"{issue_date.year:04d}-{issue_date.month:02d}"
    if frequency == "quarterly":
        return f"{issue_date.year:04d}-Q{(issue_date.month - 1) // 3 + 1}"
    if frequency == "weekly":
        iso = issue_date.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    if frequency == "annual":
        return f"{issue_date.year:04d}"
    raise ValueError(f"unknown frequency: {frequency!r}")


def add_template(
    slug: str,
    *,
    party: str,
    amount: float,
    frequency: str,
    revenue_code: str,
    anchor_day: int | None = None,
    net_terms: int = 30,
    memo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    auto_approve: bool = False,
    series_key: str = "invoice",
) -> dict:
    if frequency not in FREQUENCIES:
        raise ValueError(f"unknown frequency: {frequency!r}")
    party = (party or "").strip()
    if not party:
        raise ValueError("party is required")
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("amount must be positive")
    start = start_date or date.today().isoformat()
    con = _book(slug)
    try:
        if not con.execute(
            "SELECT 1 FROM accounts WHERE code=? AND active=1", (revenue_code,)
        ).fetchone():
            raise ValueError(f"revenue account '{revenue_code}' not found")
        cur = con.execute(
            """INSERT INTO recurring_invoice_templates
               (party, amount, memo, revenue_code, net_terms, frequency,
                anchor_day, start_date, end_date, next_run_date, auto_approve,
                series_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (party, amount, memo, revenue_code, int(net_terms), frequency,
             anchor_day, start, end_date, start, 1 if auto_approve else 0,
             series_key),
        )
        con.commit()
        template_id = int(cur.lastrowid)
    finally:
        con.close()
    return {"id": template_id, "next_run_date": start}


def list_templates(slug: str) -> list[dict]:
    con = _book(slug)
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM recurring_invoice_templates ORDER BY id"
        )]
    finally:
        con.close()


def _due_templates(con: sqlite3.Connection, as_of: str, template_id: int | None):
    sql = ("SELECT * FROM recurring_invoice_templates "
           "WHERE active=1 AND next_run_date<=? "
           "AND (end_date IS NULL OR next_run_date<=end_date)")
    params: list = [as_of]
    if template_id is not None:
        sql += " AND id=?"
        params.append(template_id)
    return con.execute(sql + " ORDER BY id", params).fetchall()


def _draft(tpl: sqlite3.Row) -> dict:
    issue = tpl["next_run_date"]
    due = (date.fromisoformat(issue) + timedelta(days=int(tpl["net_terms"]))).isoformat()
    return {
        "template_id": tpl["id"],
        "party": tpl["party"],
        "amount": round(float(tpl["amount"]), 2),
        "issue_date": issue,
        "due_date": due,
        "revenue_code": tpl["revenue_code"],
        "memo": tpl["memo"] or f"Recurring invoice — {tpl['party']}",
        "period_key": period_key(tpl["frequency"], date.fromisoformat(issue)),
    }


def preview_due(slug: str, as_of: str, template_id: int | None = None) -> list[dict]:
    """What would generate on/before ``as_of`` — read-only, writes nothing.

    Already-billed periods (a matching ``recurring_invoice_runs`` row) are
    excluded. Each item carries the filled draft plus its posting ``mode``
    (``auto`` if the template auto-approves, else ``review``).
    """
    con = _book(slug)
    try:
        out: list[dict] = []
        for tpl in _due_templates(con, as_of, template_id):
            draft = _draft(tpl)
            if con.execute(
                "SELECT 1 FROM recurring_invoice_runs WHERE template_id=? AND period_key=?",
                (tpl["id"], draft["period_key"]),
            ).fetchone():
                continue
            draft["mode"] = "auto" if tpl["auto_approve"] else "review"
            out.append(draft)
        return out
    finally:
        con.close()


def generate_due(
    slug: str,
    as_of: str,
    *,
    approve: bool = False,
    template_id: int | None = None,
    approved_by: str | None = None,
) -> list[dict]:
    """Generate (or propose) invoices for templates due on/before ``as_of``.

    A template posts when ``approve`` is True or its ``auto_approve`` flag is
    set; otherwise the filled draft is returned as a proposal with no writes.
    The ``UNIQUE(template_id, period_key)`` guard makes a re-run for an
    already-billed period a no-op, so double-billing is structurally impossible.
    """
    db_path = str(tenancy.resolve_db(slug))
    con = _book(slug)
    results: list[dict] = []
    try:
        for tpl in _due_templates(con, as_of, template_id):
            draft = _draft(tpl)
            pk = draft["period_key"]
            if con.execute(
                "SELECT 1 FROM recurring_invoice_runs WHERE template_id=? AND period_key=?",
                (tpl["id"], pk),
            ).fetchone():
                results.append({"template_id": tpl["id"], "period_key": pk,
                                "action": "skipped", "invoice_id": None,
                                "document_number": None})
                continue

            post_it = approve or bool(tpl["auto_approve"])
            if not post_it:
                results.append({"template_id": tpl["id"], "period_key": pk,
                                "action": "proposed", "invoice_id": None,
                                "document_number": None, "draft": draft})
                continue

            # Do all separate-connection writes first (each opens its own
            # connection to the same book), then open one write txn on `con`.
            # Reversing this order deadlocks under WAL: `con`'s uncommitted
            # invoice INSERT would hold the write lock these calls need.
            posted = post_json(
                {
                    "entry_date": draft["issue_date"],
                    "description": draft["memo"],
                    "payee": draft["party"],
                    "post": True,
                    "splits": [
                        {"account": "110", "dr": draft["amount"], "cr": 0, "memo": "A/R"},
                        {"account": draft["revenue_code"], "dr": 0,
                         "cr": draft["amount"], "memo": "revenue"},
                    ],
                },
                db_path,
            )
            doc_no = number_series.reserve_next(
                slug, tpl["series_key"], document_type="invoice",
            )["document_number"]

            cur = con.execute(
                """INSERT INTO invoices(party, issue_date, due_date, amount,
                                        status, entry_id, memo)
                   VALUES (?,?,?,?, 'open', ?, ?)""",
                (draft["party"], draft["issue_date"], draft["due_date"],
                 draft["amount"], posted["entry_id"], draft["memo"]),
            )
            invoice_id = int(cur.lastrowid)
            who = "auto" if not approve else (approved_by or "operator")
            con.execute(
                """INSERT INTO recurring_invoice_runs
                   (template_id, period_key, invoice_id, document_number, approved_by)
                   VALUES (?,?,?,?,?)""",
                (tpl["id"], pk, invoice_id, doc_no, who),
            )
            con.execute(
                "UPDATE recurring_invoice_templates SET next_run_date=? WHERE id=?",
                (next_period(tpl["frequency"], tpl["anchor_day"],
                             date.fromisoformat(draft["issue_date"])).isoformat(),
                 tpl["id"]),
            )
            con.commit()
            results.append({"template_id": tpl["id"], "period_key": pk,
                            "action": "generated", "invoice_id": invoice_id,
                            "document_number": doc_no})
    finally:
        con.close()
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_add_json(argv) -> None:
    import sys
    from pathlib import Path
    ap = argparse.ArgumentParser(prog="recurring_invoicing.py add-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        p = json.loads(a.payload if a.payload is not None else sys.stdin.read())
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        res = add_template(
            a.client,
            party=str(p.get("party") or ""),
            amount=p.get("amount") or 0,
            frequency=str(p.get("frequency") or ""),
            revenue_code=str(p.get("revenue_code") or ""),
            anchor_day=p.get("anchor_day"),
            net_terms=int(p.get("net_terms") or 30),
            memo=p.get("memo"),
            start_date=p.get("start_date"),
            end_date=p.get("end_date"),
            auto_approve=bool(p.get("auto_approve")),
            series_key=str(p.get("series_key") or "invoice"),
        )
        print(json.dumps({"ok": True, **res}))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def _cli_generate_json(argv) -> None:
    import sys
    from pathlib import Path
    ap = argparse.ArgumentParser(prog="recurring_invoicing.py generate-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        p = json.loads(a.payload if a.payload is not None else (sys.stdin.read() or "{}"))
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        as_of = p.get("as_of") or date.today().isoformat()
        results = generate_due(
            a.client, as_of,
            approve=bool(p.get("approve")),
            template_id=p.get("template_id"),
            approved_by=p.get("approved_by"),
        )
        print(json.dumps({"ok": True, "as_of": as_of, "results": results}))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main() -> int:
    import sys
    argv = sys.argv[1:]
    if argv and argv[0] == "add-json":
        _cli_add_json(argv[1:])
        return 0
    if argv and argv[0] == "generate-json":
        _cli_generate_json(argv[1:])
        return 0

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="list templates")
    ls.add_argument("client_slug")

    ad = sub.add_parser("add", help="add a template")
    ad.add_argument("client_slug")
    ad.add_argument("--party", required=True)
    ad.add_argument("--amount", type=float, required=True)
    ad.add_argument("--frequency", required=True, choices=FREQUENCIES)
    ad.add_argument("--revenue-code", required=True)
    ad.add_argument("--anchor-day", type=int)
    ad.add_argument("--net-terms", type=int, default=30)
    ad.add_argument("--memo")
    ad.add_argument("--start")
    ad.add_argument("--end")
    ad.add_argument("--auto", action="store_true")

    pv = sub.add_parser("preview", help="what would generate")
    pv.add_argument("client_slug")
    pv.add_argument("--as-of")
    pv.add_argument("--template", type=int)

    ge = sub.add_parser("generate", help="generate due invoices")
    ge.add_argument("client_slug")
    ge.add_argument("--as-of")
    ge.add_argument("--approve", action="store_true")
    ge.add_argument("--template", type=int)
    ge.add_argument("--approved-by")

    args = ap.parse_args()
    as_of = getattr(args, "as_of", None) or date.today().isoformat()
    if args.cmd == "list":
        print(json.dumps(list_templates(args.client_slug), indent=2, sort_keys=True))
    elif args.cmd == "add":
        print(json.dumps(add_template(
            args.client_slug, party=args.party, amount=args.amount,
            frequency=args.frequency, revenue_code=args.revenue_code,
            anchor_day=args.anchor_day, net_terms=args.net_terms, memo=args.memo,
            start_date=args.start, end_date=args.end, auto_approve=args.auto,
        ), indent=2, sort_keys=True))
    elif args.cmd == "preview":
        print(json.dumps(preview_due(args.client_slug, as_of, args.template),
                         indent=2, sort_keys=True))
    elif args.cmd == "generate":
        print(json.dumps(generate_due(
            args.client_slug, as_of, approve=args.approve,
            template_id=args.template, approved_by=args.approved_by,
        ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
