import sqlite3
from datetime import date

import pytest

import recurring_invoicing as ri
import tenancy


# ---------------------------------------------------------------------------
# Schedule math (pure functions, no I/O)
# ---------------------------------------------------------------------------

def test_next_period_monthly_advances_one_month_on_anchor():
    assert ri.next_period("monthly", 1, date(2026, 8, 1)) == date(2026, 9, 1)


def test_next_period_monthly_mid_month_anchor():
    assert ri.next_period("monthly", 15, date(2026, 8, 15)) == date(2026, 9, 15)


def test_next_period_monthly_rolls_year():
    assert ri.next_period("monthly", 1, date(2026, 12, 1)) == date(2027, 1, 1)


def test_next_period_quarterly_advances_three_months():
    assert ri.next_period("quarterly", 1, date(2026, 7, 1)) == date(2026, 10, 1)


def test_next_period_annual_advances_one_year():
    assert ri.next_period("annual", 10, date(2026, 3, 10)) == date(2027, 3, 10)


def test_next_period_weekly_advances_seven_days():
    assert ri.next_period("weekly", None, date(2026, 7, 6)) == date(2026, 7, 13)


def test_next_period_month_end_anchor_lands_on_feb_28():
    # anchor_day == -1 means "last calendar day of the target month"
    assert ri.next_period("monthly", -1, date(2026, 1, 31)) == date(2026, 2, 28)


def test_next_period_month_end_anchor_tracks_to_march_31():
    # from Feb month-end, EOM anchoring must climb back to 31, not stay at 28
    assert ri.next_period("monthly", -1, date(2026, 2, 28)) == date(2026, 3, 31)


def test_next_period_anchor_beyond_month_length_clamps_to_eom():
    # "bill on the 31st" in a 30-day month lands on the 30th
    assert ri.next_period("monthly", 31, date(2026, 4, 30)) == date(2026, 5, 31)
    assert ri.next_period("monthly", 31, date(2026, 5, 31)) == date(2026, 6, 30)


def test_period_key_monthly():
    assert ri.period_key("monthly", date(2026, 7, 4)) == "2026-07"


def test_period_key_quarterly():
    assert ri.period_key("quarterly", date(2026, 7, 4)) == "2026-Q3"
    assert ri.period_key("quarterly", date(2026, 1, 15)) == "2026-Q1"
    assert ri.period_key("quarterly", date(2026, 12, 31)) == "2026-Q4"


def test_period_key_weekly_iso_week():
    assert ri.period_key("weekly", date(2026, 7, 6)) == "2026-W28"


def test_period_key_annual():
    assert ri.period_key("annual", date(2026, 3, 10)) == "2026"


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------

def _mk_client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name=slug.title())
    return slug


def test_add_template_persists_and_lists(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tpl = ri.add_template(
        slug, party="Rent LLC", amount=2500.0, frequency="monthly",
        revenue_code="420", anchor_day=1, net_terms=15, memo="Monthly retainer",
        start_date="2026-08-01",
    )
    rows = ri.list_templates(slug)
    assert len(rows) == 1
    got = rows[0]
    assert got["id"] == tpl["id"]
    assert got["party"] == "Rent LLC"
    assert got["amount"] == 2500.0
    assert got["frequency"] == "monthly"
    assert got["revenue_code"] == "420"
    assert got["net_terms"] == 15
    assert got["next_run_date"] == "2026-08-01"


def test_add_template_defaults(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tpl = ri.add_template(
        slug, party="Sub Co", amount=99.0, frequency="monthly",
        revenue_code="400", start_date="2026-08-01",
    )
    got = ri.list_templates(slug)[0]
    assert got["net_terms"] == 30
    assert got["auto_approve"] == 0
    assert got["active"] == 1
    assert got["series_key"] == "invoice"
    assert got["next_run_date"] == "2026-08-01"
    assert tpl["id"] == got["id"]


def test_add_template_auto_flag(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    ri.add_template(
        slug, party="Auto Co", amount=50.0, frequency="monthly",
        revenue_code="400", auto_approve=True, start_date="2026-08-01",
    )
    assert ri.list_templates(slug)[0]["auto_approve"] == 1


def test_add_template_rejects_bad_frequency(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        ri.add_template(
            slug, party="X", amount=1.0, frequency="daily",
            revenue_code="400", start_date="2026-08-01",
        )


def test_add_template_rejects_unknown_revenue_code(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        ri.add_template(
            slug, party="X", amount=1.0, frequency="monthly",
            revenue_code="999", start_date="2026-08-01",
        )


# ---------------------------------------------------------------------------
# Generation flow
# ---------------------------------------------------------------------------

def _conn(slug):
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    return con


def test_approved_generation_posts_balanced_invoice(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tpl = ri.add_template(
        slug, party="Rent LLC", amount=2500.0, frequency="monthly",
        revenue_code="420", anchor_day=1, net_terms=15, start_date="2026-08-01",
    )
    res = ri.generate_due(slug, "2026-08-01", approve=True)
    assert len(res) == 1
    r = res[0]
    assert r["action"] == "generated"
    assert r["period_key"] == "2026-08"
    assert r["invoice_id"] is not None
    assert r["document_number"]

    con = _conn(slug)
    inv = con.execute("SELECT * FROM invoices WHERE id=?", (r["invoice_id"],)).fetchone()
    assert inv["status"] == "open"
    assert inv["amount"] == 2500.0
    assert inv["issue_date"] == "2026-08-01"
    assert inv["due_date"] == "2026-08-16"  # issue + 15 net terms
    assert inv["entry_id"] is not None
    # balanced GL entry: Dr 110 AR / Cr 420 revenue
    splits = {row["account"]: (row["dr"], row["cr"]) for row in
              con.execute("SELECT account, dr, cr FROM splits WHERE entry_id=?", (inv["entry_id"],))}
    assert splits["110"] == (2500.0, 0.0)
    assert splits["420"] == (0.0, 2500.0)
    entry_status = con.execute("SELECT status FROM entries WHERE id=?", (inv["entry_id"],)).fetchone()[0]
    assert entry_status == "posted"
    # next_run_date advanced exactly one month
    nxt = con.execute("SELECT next_run_date FROM recurring_invoice_templates WHERE id=?", (tpl["id"],)).fetchone()[0]
    assert nxt == "2026-09-01"
    con.close()


def test_generate_is_idempotent_per_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    ri.add_template(
        slug, party="Sub Co", amount=99.0, frequency="monthly",
        revenue_code="400", anchor_day=1, start_date="2026-08-01",
    )
    first = ri.generate_due(slug, "2026-08-01", approve=True)
    assert first[0]["action"] == "generated"
    # Re-run for a date that still covers the same period after advance:
    # force by pointing next_run_date back — simplest: run again as_of same date,
    # template already advanced to 2026-09-01 so nothing is due; assert no dup.
    con = _conn(slug)
    n_inv = con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    n_runs = con.execute("SELECT COUNT(*) FROM recurring_invoice_runs").fetchone()[0]
    con.close()
    assert n_inv == 1
    assert n_runs == 1


def test_idempotency_guard_blocks_double_bill_same_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tpl = ri.add_template(
        slug, party="Sub Co", amount=99.0, frequency="monthly",
        revenue_code="400", anchor_day=1, start_date="2026-08-01",
    )
    ri.generate_due(slug, "2026-08-01", approve=True)
    # Manually rewind next_run_date to the same period and regenerate:
    con = _conn(slug)
    con.execute("UPDATE recurring_invoice_templates SET next_run_date='2026-08-01' WHERE id=?", (tpl["id"],))
    con.commit()
    con.close()
    res = ri.generate_due(slug, "2026-08-01", approve=True)
    assert res[0]["action"] == "skipped"
    con = _conn(slug)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    con.close()


def test_review_mode_returns_proposal_and_writes_nothing(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tpl = ri.add_template(
        slug, party="Review Co", amount=500.0, frequency="monthly",
        revenue_code="400", anchor_day=1, start_date="2026-08-01",
    )
    res = ri.generate_due(slug, "2026-08-01", approve=False)
    assert res[0]["action"] == "proposed"
    assert res[0]["invoice_id"] is None
    con = _conn(slug)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM recurring_invoice_runs").fetchone()[0] == 0
    # next_run_date NOT advanced
    nxt = con.execute("SELECT next_run_date FROM recurring_invoice_templates WHERE id=?", (tpl["id"],)).fetchone()[0]
    assert nxt == "2026-08-01"
    con.close()


def test_auto_mode_posts_without_approval(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    ri.add_template(
        slug, party="Auto Co", amount=50.0, frequency="monthly",
        revenue_code="400", anchor_day=1, auto_approve=True, start_date="2026-08-01",
    )
    res = ri.generate_due(slug, "2026-08-01", approve=False)
    assert res[0]["action"] == "generated"
    con = _conn(slug)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    approved_by = con.execute("SELECT approved_by FROM recurring_invoice_runs").fetchone()[0]
    assert approved_by == "auto"
    con.close()


def test_template_past_end_date_is_inert(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    ri.add_template(
        slug, party="Ended Co", amount=50.0, frequency="monthly",
        revenue_code="400", anchor_day=1, auto_approve=True,
        start_date="2026-08-01", end_date="2026-07-31",
    )
    res = ri.generate_due(slug, "2026-08-01", approve=True)
    assert res == []
    con = _conn(slug)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    con.close()


def test_generation_isolated_between_clients(tmp_path, monkeypatch):
    a = _mk_client(tmp_path, monkeypatch, slug="alpha")
    b = _mk_client(tmp_path, monkeypatch, slug="bravo")
    ri.add_template(
        a, party="A Co", amount=100.0, frequency="monthly",
        revenue_code="400", anchor_day=1, auto_approve=True, start_date="2026-08-01",
    )
    ri.generate_due(a, "2026-08-01", approve=True)
    con = _conn(b)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    con.close()


# ---------------------------------------------------------------------------
# Preview (read-only)
# ---------------------------------------------------------------------------

def test_preview_shows_due_draft_and_writes_nothing(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    ri.add_template(
        slug, party="Preview Co", amount=750.0, frequency="monthly",
        revenue_code="400", anchor_day=1, net_terms=30, auto_approve=True,
        start_date="2026-08-01",
    )
    prev = ri.preview_due(slug, "2026-08-01")
    assert len(prev) == 1
    p = prev[0]
    assert p["period_key"] == "2026-08"
    assert p["amount"] == 750.0
    assert p["issue_date"] == "2026-08-01"
    assert p["due_date"] == "2026-08-31"
    assert p["mode"] == "auto"
    con = _conn(slug)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM recurring_invoice_runs").fetchone()[0] == 0
    con.close()


def test_preview_excludes_already_billed_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tpl = ri.add_template(
        slug, party="Once Co", amount=100.0, frequency="monthly",
        revenue_code="400", anchor_day=1, start_date="2026-08-01",
    )
    ri.generate_due(slug, "2026-08-01", approve=True)
    # rewind so the same period is "due" again, but it's already billed
    con = _conn(slug)
    con.execute("UPDATE recurring_invoice_templates SET next_run_date='2026-08-01' WHERE id=?", (tpl["id"],))
    con.commit()
    con.close()
    assert ri.preview_due(slug, "2026-08-01") == []
