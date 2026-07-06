#!/usr/bin/env python3
"""Project each client ledger.db into public/clients/<slug>.json (read-only)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import payroll_rollup
import tenancy

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
    finally:
        con.close()

    ref_date = as_of or date.today().isoformat()

    next_due = None
    if as_of:
        next_due = tenancy.compute_next_filing_due(
            (prof["filing_cadence"] or "quarterly"),
            date.fromisoformat(as_of),
            (prof["fiscal_year_end"] or "12-31")).isoformat()

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
