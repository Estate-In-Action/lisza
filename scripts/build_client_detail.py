#!/usr/bin/env python3
"""Project each client ledger.db into public/clients/<slug>.json (read-only)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import payroll_rollup
import tenancy
import automation_profile
import document_requests

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
TOP_N = 8
SPREADSHEET_ROW_LIMIT = 500


def _window_months(active_window: str | None) -> int:
    raw = (active_window or "1y").strip().lower().replace("_", "-")
    aliases = {
        "last-month": 1, "1m": 1, "month": 1, "monthly": 1,
        "last-quarter": 3, "1q": 3, "quarter": 3, "quarterly": 3,
        "last-year": 12, "1y": 12, "year": 12, "annual": 12,
        "2y": 24, "2-years": 24, "two-years": 24,
    }
    return aliases.get(raw, 12)


def active_window_bounds(as_of: str | None, active_window: str | None) -> dict:
    months = _window_months(active_window)
    if not as_of:
        return {"label": active_window or "1y", "months": months, "start": None, "end": None}
    return {
        "label": active_window or "1y",
        "months": months,
        "start": _months_back(as_of, months)[0] + "-01",
        "end": as_of,
    }


def aging_buckets(items, as_of: str) -> dict:
    """Bucket AR/AP items by days-past-due vs as_of. items: dicts with
    party, due_date (ISO), amount."""
    ref = date.fromisoformat(as_of)
    buckets = {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0,
               "d61_90": 0.0, "d90_plus": 0.0}
    total = 0.0
    count = 0
    enriched = []
    for it in items:
        amt = float(it["amount"])
        dpd = (ref - date.fromisoformat(it["due_date"])).days
        if dpd <= 0:
            buckets["current"] += amt
        elif dpd <= 30:
            buckets["d1_30"] += amt
        elif dpd <= 60:
            buckets["d31_60"] += amt
        elif dpd <= 90:
            buckets["d61_90"] += amt
        else:
            buckets["d90_plus"] += amt
        total += amt
        count += 1
        enriched.append({"party": it["party"], "due_date": it["due_date"],
                         "amount": round(amt, 2), "days_past_due": dpd})
    buckets = {k: round(v, 2) for k, v in buckets.items()}
    overdue_total = round(
        buckets["d1_30"] + buckets["d31_60"] + buckets["d61_90"] + buckets["d90_plus"], 2)
    top = sorted(enriched, key=lambda r: r["amount"], reverse=True)[:TOP_N]
    return {"open_total": round(sum(buckets.values()), 2), "open_count": count,
            "overdue_total": overdue_total,
            "overdue_count": sum(1 for r in enriched if r["days_past_due"] > 0),
            "current_count": sum(1 for r in enriched if r["days_past_due"] <= 0),
            "status": "overdue" if overdue_total else ("clear" if count == 0 else "current"),
            "aging": buckets, "top_open": top}


def mask_ein(ein: str | None) -> str | None:
    if not ein:
        return None
    digits = "".join(ch for ch in ein if ch.isdigit())
    if len(digits) < 4:
        return None
    return "••-•••" + digits[-4:]


def _months_back(as_of: str, n: int) -> list[str]:
    ref = date.fromisoformat(as_of)
    y, m = ref.year, ref.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def monthly_trend(con: sqlite3.Connection, as_of: str, n: int = 12) -> list[dict]:
    months = _months_back(as_of, n)
    rows = con.execute(
        """SELECT substr(e.entry_date,1,7) AS ym,
                  ROUND(SUM(CASE WHEN a.type='income'  THEN s.cr-s.dr ELSE 0 END),2) rev,
                  ROUND(SUM(CASE WHEN a.type='expense' THEN s.dr-s.cr ELSE 0 END),2) exp,
                  COUNT(DISTINCT e.id) ent
           FROM splits s
           JOIN entries e ON e.id=s.entry_id AND e.status='posted'
           JOIN accounts a ON a.code=s.account
           WHERE substr(e.entry_date,1,7) >= ? AND substr(e.entry_date,1,7) <= ?
           GROUP BY ym""", (months[0], months[-1])).fetchall()
    by = {r[0]: r for r in rows}
    out = []
    for ym in months:
        r = by.get(ym)
        rev = (r[1] if r and r[1] is not None else 0.0)
        exp = (r[2] if r and r[2] is not None else 0.0)
        ent = (r[3] if r else 0)
        out.append({"month": ym, "revenue": rev, "expense": exp,
                    "net": round(rev - exp, 2), "entries": ent})
    return out


def posted_span(con: sqlite3.Connection):
    first, last = con.execute(
        "SELECT MIN(entry_date), MAX(entry_date) FROM entries "
        "WHERE status='posted'").fetchone()
    n = con.execute(
        "SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
    return first, last, n


def active_window_entry_counts(con: sqlite3.Connection, start: str | None) -> dict:
    if not start:
        total = con.execute(
            "SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
        return {"posted_entries": total, "prior_entries": 0}
    posted = con.execute(
        "SELECT COUNT(*) FROM entries WHERE status='posted' AND entry_date >= ?",
        (start,)).fetchone()[0]
    prior = con.execute(
        "SELECT COUNT(*) FROM entries WHERE status='posted' AND entry_date < ?",
        (start,)).fetchone()[0]
    return {"posted_entries": posted, "prior_entries": prior}


def _quarter_start(ref: date) -> date:
    month = ((ref.month - 1) // 3) * 3 + 1
    return date(ref.year, month, 1)


def _period_bounds(as_of: str | None) -> dict:
    if not as_of:
        return {}
    ref = date.fromisoformat(as_of)
    return {
        "month": (date(ref.year, ref.month, 1), ref),
        "quarter": (_quarter_start(ref), ref),
        "year": (date(ref.year, 1, 1), ref),
    }


def spreadsheet_periods(con: sqlite3.Connection, as_of: str | None) -> dict:
    periods = {}
    for key, (start, end) in _period_bounds(as_of).items():
        rows = [dict(r) for r in con.execute(
            """SELECT e.entry_date, e.id AS entry_id, e.description, e.payee,
                      e.source, e.source_ref, COALESCE(ent.name, 'Main') AS entity,
                      s.account, a.name AS account_name, a.type AS account_type,
                      ROUND(s.dr, 2) AS debit, ROUND(s.cr, 2) AS credit,
                      s.memo
               FROM entries e
               JOIN splits s ON s.entry_id=e.id
               JOIN accounts a ON a.code=s.account
               LEFT JOIN entities ent ON ent.id=e.entity_id
               WHERE e.status='posted'
                 AND e.entry_date >= ? AND e.entry_date <= ?
               ORDER BY e.entry_date, e.id, s.id
               LIMIT ?""",
            (start.isoformat(), end.isoformat(), SPREADSHEET_ROW_LIMIT + 1))]
        visible = rows[:SPREADSHEET_ROW_LIMIT]
        periods[key] = {
            "label": key, "start": start.isoformat(), "end": end.isoformat(),
            "row_count": len(rows), "truncated": len(rows) > SPREADSHEET_ROW_LIMIT,
            "debit_total": round(sum(float(r["debit"] or 0) for r in visible), 2),
            "credit_total": round(sum(float(r["credit"] or 0) for r in visible), 2),
            "rows": visible,
        }
    return periods

def _posted_filter(start: str | None = None, end: str | None = None) -> tuple[str, list]:
    clauses = ["e.status='posted'"]
    params = []
    if start:
        clauses.append("e.entry_date >= ?")
        params.append(start)
    if end:
        clauses.append("e.entry_date <= ?")
        params.append(end)
    return " AND ".join(clauses), params


def cash_flow_summary(con: sqlite3.Connection, as_of: str | None, start: str | None) -> dict:
    cash_accounts = ("101", "102", "103", "106")
    qmarks = ",".join("?" * len(cash_accounts))
    if not as_of:
        return {"status": "no_posted_activity", "start": start, "end": as_of,
                "inflow": 0.0, "outflow": 0.0, "net": 0.0,
                "starting_cash": 0.0, "ending_cash": 0.0}
    where, params = _posted_filter(start, as_of)
    row = con.execute(
        f"""SELECT ROUND(COALESCE(SUM(CASE WHEN s.dr>s.cr THEN s.dr-s.cr ELSE 0 END),0),2),
                   ROUND(COALESCE(SUM(CASE WHEN s.cr>s.dr THEN s.cr-s.dr ELSE 0 END),0),2)
            FROM splits s JOIN entries e ON e.id=s.entry_id
            WHERE {where} AND s.account IN ({qmarks})""",
        (*params, *cash_accounts),
    ).fetchone()
    inflow, outflow = float(row[0] or 0), float(row[1] or 0)
    ending = con.execute(
        f"""SELECT ROUND(COALESCE(SUM(s.dr-s.cr),0),2)
            FROM splits s JOIN entries e ON e.id=s.entry_id
            WHERE e.status='posted' AND e.entry_date <= ? AND s.account IN ({qmarks})""",
        (as_of, *cash_accounts),
    ).fetchone()[0]
    net = round(inflow - outflow, 2)
    return {"status": "active", "start": start, "end": as_of,
            "inflow": round(inflow, 2), "outflow": round(outflow, 2),
            "net": net, "starting_cash": round(float(ending or 0) - net, 2),
            "ending_cash": round(float(ending or 0), 2)}


def pnl_balance_summary(con: sqlite3.Connection, as_of: str | None, start: str | None) -> dict:
    if not as_of:
        return {"status": "no_posted_activity", "period": {"start": start, "end": as_of}}
    period_where, period_params = _posted_filter(start, as_of)
    period = con.execute(
        f"""SELECT
              ROUND(COALESCE(SUM(CASE WHEN a.type='income' THEN s.cr-s.dr ELSE 0 END),0),2),
              ROUND(COALESCE(SUM(CASE WHEN a.type='expense' THEN s.dr-s.cr ELSE 0 END),0),2)
            FROM splits s JOIN entries e ON e.id=s.entry_id JOIN accounts a ON a.code=s.account
            WHERE {period_where}""",
        period_params,
    ).fetchone()
    income, expense = float(period[0] or 0), float(period[1] or 0)
    life = con.execute(
        """SELECT a.type,
                  ROUND(COALESCE(SUM(
                    CASE WHEN e.id IS NULL THEN 0
                         WHEN a.sign_normal='debit' THEN s.dr-s.cr
                         ELSE s.cr-s.dr END),0),2)
           FROM accounts a
           LEFT JOIN splits s ON s.account=a.code
           LEFT JOIN entries e ON e.id=s.entry_id AND e.status='posted' AND e.entry_date <= ?
           GROUP BY a.type""",
        (as_of,),
    ).fetchall()
    by_type = {r[0]: float(r[1] or 0) for r in life}
    retained = round(by_type.get("income", 0.0) - by_type.get("expense", 0.0), 2)
    equity_total = round(by_type.get("equity", 0.0) + retained, 2)
    return {
        "status": "active",
        "period": {"start": start, "end": as_of, "income": round(income, 2),
                   "expense": round(expense, 2), "net_income": round(income - expense, 2)},
        "balance_sheet": {"as_of": as_of, "assets": round(by_type.get("asset", 0.0), 2),
                          "liabilities": round(by_type.get("liability", 0.0), 2),
                          "equity_accounts": round(by_type.get("equity", 0.0), 2),
                          "retained_earnings": retained, "equity_total": equity_total},
    }


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def reconciliation_status(con: sqlite3.Connection) -> dict:
    if not _table_exists(con, "statement_lines"):
        return {"status": "not_started", "statement_count": 0, "matched_count": 0,
                "unmatched_count": 0, "ignored_count": 0, "latest_statement_date": None,
                "lines": []}
    rows = con.execute(
        "SELECT status, COUNT(*), ROUND(COALESCE(SUM(amount),0),2) FROM statement_lines GROUP BY status"
    ).fetchall()
    counts = {r[0]: int(r[1]) for r in rows}
    amounts = {r[0]: float(r[2] or 0) for r in rows}
    latest = con.execute("SELECT MAX(statement_date) FROM statement_lines").fetchone()[0]
    total = sum(counts.values())
    unmatched = counts.get("unmatched", 0)
    if _table_exists(con, "reconciliation_matches"):
        line_sql = """
            SELECT sl.statement_account, sl.statement_date, sl.description,
                   ROUND(sl.amount, 2) AS amount, sl.external_ref, sl.status,
                   rm.entry_id AS matched_entry_id, rm.method AS match_method,
                   rm.matched_at,
                   CASE rm.method
                     WHEN 'auto_exact' THEN 'high'
                     WHEN 'auto_date_window' THEN 'medium'
                     WHEN 'manual' THEN 'reviewed'
                     ELSE NULL
                   END AS match_confidence
            FROM statement_lines sl
            LEFT JOIN reconciliation_matches rm ON rm.statement_line_id=sl.id
            ORDER BY sl.status='unmatched' DESC, sl.statement_date DESC, sl.id DESC
            LIMIT 25"""
    else:
        line_sql = """
            SELECT statement_account, statement_date, description,
                   ROUND(amount, 2) AS amount, external_ref, status,
                   NULL AS matched_entry_id, NULL AS match_method,
                   NULL AS matched_at, NULL AS match_confidence
            FROM statement_lines
            ORDER BY status='unmatched' DESC, statement_date DESC, id DESC
            LIMIT 25"""
    lines = [dict(r) for r in con.execute(line_sql)]
    return {
        "status": "needs_review" if unmatched else ("clear" if total else "not_started"),
        "statement_count": total, "matched_count": counts.get("matched", 0),
        "unmatched_count": unmatched, "ignored_count": counts.get("ignored", 0),
        "status_amounts": amounts, "latest_statement_date": latest,
        "lines": lines,
    }


def filing_obligations(con: sqlite3.Connection, prof: sqlite3.Row, as_of: str | None, next_due: str | None) -> dict:
    ref = date.fromisoformat(as_of) if as_of else date.today()
    year_start = f"{ref.year}-01-01"
    estimated_tax_paid = con.execute(
        """SELECT ROUND(COALESCE(SUM(s.dr-s.cr),0),2)
           FROM splits s JOIN entries e ON e.id=s.entry_id
           WHERE e.status='posted' AND e.entry_date >= ? AND e.entry_date <= ? AND s.account='592'""",
        (year_start, ref.isoformat()),
    ).fetchone()[0]
    liability_accounts = ("245", "246", "247", "248", "249")
    qmarks = ",".join("?" * len(liability_accounts))
    payroll_tax_liability = con.execute(
        f"""SELECT ROUND(COALESCE(SUM(s.cr-s.dr),0),2)
            FROM splits s JOIN entries e ON e.id=s.entry_id
            WHERE e.status='posted' AND e.entry_date <= ? AND s.account IN ({qmarks})""",
        (ref.isoformat(), *liability_accounts),
    ).fetchone()[0]
    days_until_due = (date.fromisoformat(next_due) - ref).days if next_due else None
    if days_until_due is None:
        status = "not_scheduled"
    elif days_until_due < 0:
        status = "overdue"
    elif days_until_due <= 30:
        status = "due_soon"
    else:
        status = "scheduled"
    return {
        "status": status, "filing_cadence": prof["filing_cadence"],
        "fiscal_year_end": prof["fiscal_year_end"], "next_filing_due": next_due,
        "days_until_due": days_until_due,
        "estimated_tax_paid_ytd": round(float(estimated_tax_paid or 0), 2),
        "payroll_tax_liability": round(float(payroll_tax_liability or 0), 2),
    }

def build_client_detail(slug: str) -> dict:
    db = tenancy.resolve_db(slug)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        as_of = con.execute(
            "SELECT MAX(entry_date) FROM entries WHERE status='posted'").fetchone()[0]
        inv = [dict(r) for r in con.execute(
            "SELECT party, due_date, amount FROM invoices WHERE status='open'")]
        bil = [dict(r) for r in con.execute(
            "SELECT party, due_date, amount FROM bills WHERE status='unpaid'")]
        prof = con.execute(
            "SELECT slug, display_name, legal_name, ein, entity_type, "
            "fiscal_year_end, filing_cadence, active_window FROM client_profile").fetchone()
        ents = [dict(r) for r in con.execute(
            "SELECT name, type FROM entities WHERE active=1 "
            "ORDER BY is_default DESC, id")]
        first, last, entry_count = posted_span(con)
        monthly = monthly_trend(con, as_of) if as_of else []
        payroll = payroll_rollup.build_payroll(con)
        window = active_window_bounds(as_of, prof["active_window"])
        window_counts = active_window_entry_counts(con, window["start"])
        inspection = spreadsheet_periods(con, as_of)
        cash_flow = cash_flow_summary(con, as_of, window["start"])
        pnl_balance = pnl_balance_summary(con, as_of, window["start"])
        reconciliation = reconciliation_status(con)
        next_due = None
        if as_of:
            next_due = tenancy.compute_next_filing_due(
                (prof["filing_cadence"] or "quarterly"),
                date.fromisoformat(as_of),
                (prof["fiscal_year_end"] or "12-31")).isoformat()
        filing = filing_obligations(con, prof, as_of, next_due)
    finally:
        con.close()


    ref_date = as_of or date.today().isoformat()
    workflow_profile = tenancy.get_automation_profile(slug)
    workflow_jobs = automation_profile.plan_due_jobs(
        slug,
        as_of=date.fromisoformat(ref_date),
        include_scheduled=True,
    )
    workflow_setup = automation_profile.setup_flow(slug)
    doc_requests = document_requests.list_requests(slug)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slug": prof["slug"],
        "display_name": prof["display_name"],
        "entity_type": prof["entity_type"],
        "status": "active",
        "as_of": as_of,
        "ar": aging_buckets(inv, ref_date),
        "ap": aging_buckets(bil, ref_date),
        "admin": {
            "legal_name": prof["legal_name"],
            "ein_masked": mask_ein(prof["ein"]),
            "entity_type": prof["entity_type"],
            "fiscal_year_end": prof["fiscal_year_end"],
            "filing_cadence": prof["filing_cadence"],
            "next_filing_due": next_due,
            "active_window": prof["active_window"] or "1y",
            "status": "configured",
            "entities": ents,
        },
        "historical": {
            "span": {"first": first, "last": last},
            "monthly": monthly,
            "entry_count": entry_count,
            "active_window": {**window, **window_counts},
            "status": "read_only",
        },
        "inspection": {
            "mode": "read_only",
            "views": ["month", "quarter", "year"],
            "periods": inspection,
        },
        "cash_flow": cash_flow,
        "pnl_balance": pnl_balance,
        "reconciliation": reconciliation,
        "filing_obligations": filing,
        "automation_profile": workflow_profile,
        "automation_setup": workflow_setup,
        "due_jobs": workflow_jobs,
        "document_requests": doc_requests,
        "payroll": payroll,
    }


def write_client_detail(slug: str, path=None) -> Path:
    out = Path(path) if path else PUBLIC_DIR / "clients" / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_client_detail(slug), indent=2))
    return out


def write_all() -> int:
    n = 0
    for row in tenancy.list_clients():
        write_client_detail(row.slug)
        n += 1
    return n


if __name__ == "__main__":
    print(f"wrote {write_all()} client detail files")
