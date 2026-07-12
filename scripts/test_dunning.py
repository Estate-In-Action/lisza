import json
import os
import sqlite3
import subprocess

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

import dunning
import payments
import sales_pipeline
import tenancy


def _mk_client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name=slug.title())
    return slug


def _mk_invoice(slug, party="Customer A", amount=1000.0, due="2026-07-04"):
    quote_id = sales_pipeline.create_quote(slug, party=party, amount=amount)
    order_id = sales_pipeline.accept_quote(slug, quote_id)
    return sales_pipeline.invoice_order(
        slug, order_id, issue_date="2026-06-20", due_date=due
    )


# ---- policy -------------------------------------------------------------

def test_default_policy_stages_ordered_by_days():
    stages = dunning.default_policy()
    days = [s["min_days_overdue"] for s in stages]
    assert days == sorted(days)
    assert days[0] >= 1  # nothing escalates before the due date passes


def test_set_and_get_policy_roundtrips(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    custom = [
        {"stage": 1, "label": "nudge", "min_days_overdue": 5,
         "late_fee_pct": 0.0, "late_fee_flat": 0.0},
        {"stage": 2, "label": "demand", "min_days_overdue": 20,
         "late_fee_pct": 2.0, "late_fee_flat": 10.0},
    ]
    dunning.set_policy(slug, custom)
    got = dunning.get_policy(slug)
    assert [s["label"] for s in got] == ["nudge", "demand"]
    assert got[1]["late_fee_pct"] == 2.0


# ---- ladder (read-only) -------------------------------------------------

def test_ladder_flags_overdue_invoice_with_stage(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    # 28 days past due -> past stage-2 (15d) but not stage-3 (30d)
    ladder = dunning.dunning_ladder(slug, as_of="2026-08-01")
    row = next(r for r in ladder if r["invoice_id"] == inv)
    assert row["days_overdue"] == 28
    assert row["stage"] == 2
    assert row["balance"] == 1000.0
    assert row["suggested_fee"] == 15.0  # 1.5% of 1000


def test_ladder_ignores_not_yet_due(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _mk_invoice(slug, amount=500.0, due="2026-09-01")
    ladder = dunning.dunning_ladder(slug, as_of="2026-08-01")
    assert ladder == []


def test_ladder_ignores_paid_invoice(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=400.0, due="2026-07-04")
    payments.record_payment(
        slug, direction="receipt", amount=400.0, payment_date="2026-07-20",
        cash_account="102", allocations=[{"target_id": inv, "amount": 400.0}],
    )
    ladder = dunning.dunning_ladder(slug, as_of="2026-08-01")
    assert all(r["invoice_id"] != inv for r in ladder)


# ---- late-fee assessment (ledger-affecting) -----------------------------

def test_assess_late_fee_posts_dr_ar_cr_income(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")

    res = dunning.assess_late_fee(slug, inv, as_of="2026-08-01")
    assert res["amount"] == 15.0
    assert res["stage"] == 2

    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    splits = {r["account"]: (r["dr"], r["cr"]) for r in
              con.execute("SELECT account, dr, cr FROM splits WHERE entry_id=?",
                          (res["entry_id"],))}
    income = res["income_code"]
    con.close()
    assert splits["110"] == (15.0, 0.0)          # A/R goes up
    assert splits[income] == (0.0, 15.0)         # late-fee income booked


def test_late_fee_percent_of_balance(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=2000.0, due="2026-07-04")
    res = dunning.assess_late_fee(slug, inv, as_of="2026-08-01")
    assert res["amount"] == 30.0  # 1.5% of 2000


def test_late_fee_idempotent_per_stage(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    dunning.assess_late_fee(slug, inv, as_of="2026-08-01")
    with pytest.raises(ValueError):
        dunning.assess_late_fee(slug, inv, as_of="2026-08-01")  # same stage again


def test_ladder_marks_fee_already_assessed(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    dunning.assess_late_fee(slug, inv, as_of="2026-08-01")
    ladder = dunning.dunning_ladder(slug, as_of="2026-08-01")
    row = next(r for r in ladder if r["invoice_id"] == inv)
    assert row["fee_assessed"] is True


def test_assess_rejects_non_income_account(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    with pytest.raises(ValueError):
        dunning.assess_late_fee(slug, inv, income_code="110", as_of="2026-08-01")


def test_assess_rejects_not_overdue(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-09-01")
    with pytest.raises(ValueError):
        dunning.assess_late_fee(slug, inv, as_of="2026-08-01")  # not yet due


def test_invoice_dunning_state_totals(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    dunning.assess_late_fee(slug, inv, as_of="2026-08-01")
    st = dunning.invoice_dunning_state(slug, inv, as_of="2026-08-01")
    assert st["base_balance"] == 1000.0
    assert st["fees"] == 15.0
    assert st["total_due"] == 1015.0
    assert st["days_overdue"] == 28


def test_flat_fee_stage(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    # 63 days overdue -> final_demand (flat $25)
    res = dunning.assess_late_fee(slug, inv, as_of="2026-09-05")
    assert res["stage"] == 4
    assert res["amount"] == 25.0


def test_list_fees(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    dunning.assess_late_fee(slug, inv, as_of="2026-08-01")
    fees = dunning.list_fees(slug)
    assert len(fees) == 1
    assert fees[0]["invoice_id"] == inv
    assert fees[0]["amount"] == 15.0


def test_cli_dun_json_assess(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0, due="2026-07-04")
    payload = json.dumps({"action": "assess", "invoice_id": inv,
                          "as_of": "2026-08-01"})
    out = subprocess.run(
        ["python3", "dunning.py", "dun-json", "--client", slug,
         "--payload", payload],
        cwd=SCRIPTS_DIR, capture_output=True, text=True,
        env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    res = json.loads(out.stdout)
    assert res["ok"] is True
    assert res["amount"] == 15.0
