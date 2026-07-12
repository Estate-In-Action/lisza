import json
import os
import sqlite3
import subprocess

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

import period_close
import post_entry
import tenancy


def _mk_client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name=slug.title())
    return slug


def _post(slug, splits, *, entry_date="2026-07-10", desc="test"):
    return post_entry.post_json(
        {"entry_date": entry_date, "description": desc, "post": True, "splits": splits},
        str(tenancy.resolve_db(slug)),
    )


def _splits(slug, entry_id):
    con = sqlite3.connect(tenancy.resolve_db(slug))
    try:
        return {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT account, dr, cr FROM splits WHERE entry_id=?", (entry_id,))}
    finally:
        con.close()


def _revenue_and_expense(slug):
    # $1,000 revenue (Cr 400 / Dr 110) and $400 expense (Dr 500 / Cr 102)
    _post(slug, [{"account": "110", "dr": 1000.0, "cr": 0},
                 {"account": "400", "dr": 0, "cr": 1000.0}], desc="revenue")
    _post(slug, [{"account": "500", "dr": 400.0, "cr": 0},
                 {"account": "102", "dr": 0, "cr": 400.0}], desc="expense")


# ---- income summary (read-only) -----------------------------------------

def test_income_summary_nets_revenue_minus_expense(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    s = period_close.income_summary(slug, start="2026-07-01", end="2026-07-31")
    assert s["total_income"] == 1000.0
    assert s["total_expense"] == 400.0
    assert s["net_income"] == 600.0


def test_income_summary_excludes_out_of_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _post(slug, [{"account": "110", "dr": 500.0, "cr": 0},
                 {"account": "400", "dr": 0, "cr": 500.0}], entry_date="2026-06-15")
    s = period_close.income_summary(slug, start="2026-07-01", end="2026-07-31")
    assert s["total_income"] == 0.0
    assert s["net_income"] == 0.0


# ---- closing entry ------------------------------------------------------

def test_close_period_posts_closing_split(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    res = period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    assert res["net_income"] == 600.0
    sp = _splits(slug, res["entry_id"])
    assert sp["400"] == (1000.0, 0.0)          # zero the revenue (Dr)
    assert sp["500"] == (0.0, 400.0)           # zero the expense (Cr)
    assert sp[res["retained_account"]] == (0.0, 600.0)  # net income to equity


def test_close_period_defaults_to_retained_earnings_310(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    res = period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    assert res["retained_account"] == "310"


def test_close_period_zeroes_pnl_over_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    s = period_close.income_summary(slug, start="2026-07-01", end="2026-07-31")
    assert s["net_income"] == 0.0              # temporaries reset


def test_close_period_loss_debits_retained(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    # expense 900 > income 300 → loss of 600
    _post(slug, [{"account": "110", "dr": 300.0, "cr": 0},
                 {"account": "400", "dr": 0, "cr": 300.0}])
    _post(slug, [{"account": "500", "dr": 900.0, "cr": 0},
                 {"account": "102", "dr": 0, "cr": 900.0}])
    res = period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    assert res["net_income"] == -600.0
    sp = _splits(slug, res["entry_id"])
    assert sp[res["retained_account"]] == (600.0, 0.0)  # loss debits equity


def test_close_period_rejects_non_equity_retained(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    with pytest.raises(ValueError):
        period_close.close_period(slug, start="2026-07-01", end="2026-07-31",
                                  retained_account="110")


def test_close_period_requires_pnl_activity(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        period_close.close_period(slug, start="2026-07-01", end="2026-07-31")


def test_close_period_rejects_double_close(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    with pytest.raises(ValueError):
        period_close.close_period(slug, start="2026-07-01", end="2026-07-31")


# ---- period lock --------------------------------------------------------

def test_lock_blocks_posting_in_closed_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    with pytest.raises(ValueError):
        _post(slug, [{"account": "110", "dr": 50.0, "cr": 0},
                     {"account": "400", "dr": 0, "cr": 50.0}], entry_date="2026-07-15")


def test_lock_allows_posting_after_closed_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    r = _post(slug, [{"account": "110", "dr": 50.0, "cr": 0},
                     {"account": "400", "dr": 0, "cr": 50.0}], entry_date="2026-08-01")
    assert r["status"] == "posted"


def test_is_period_locked_reports_locking_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    assert period_close.is_period_locked(slug, "2026-07-20") == "2026-07-31"
    assert period_close.is_period_locked(slug, "2026-08-05") is None


def test_list_periods_returns_closed(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    period_close.close_period(slug, start="2026-07-01", end="2026-07-31")
    periods = period_close.list_periods(slug)
    assert len(periods) == 1
    assert periods[0]["period_end"] == "2026-07-31"
    assert periods[0]["status"] == "closed"
    assert periods[0]["net_income"] == 600.0


# ---- CLI ----------------------------------------------------------------

def test_cli_close_json(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _revenue_and_expense(slug)
    payload = json.dumps({"action": "close", "start": "2026-07-01",
                          "end": "2026-07-31"})
    out = subprocess.run(
        ["python3", "period_close.py", "close-json", "--client", slug,
         "--payload", payload],
        cwd=SCRIPTS_DIR, capture_output=True, text=True,
        env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    res = json.loads(out.stdout)
    assert res["ok"] is True
    assert res["net_income"] == 600.0
