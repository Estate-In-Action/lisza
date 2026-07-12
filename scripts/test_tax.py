import json
import os
import sqlite3
import subprocess

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

import payments
import tax
import tenancy


def _mk_client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name=slug.title())
    return slug


def _splits(slug, entry_id):
    con = sqlite3.connect(tenancy.resolve_db(slug))
    try:
        return {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT account, dr, cr FROM splits WHERE entry_id=?", (entry_id,))}
    finally:
        con.close()


# ---- compute_tax --------------------------------------------------------

def test_compute_tax_exclusive():
    r = tax.compute_tax(1000.0, 8.5, inclusive=False)
    assert r == {"net": 1000.0, "tax": 85.0, "gross": 1085.0}


def test_compute_tax_inclusive():
    r = tax.compute_tax(1085.0, 8.5, inclusive=True)
    assert r == {"net": 1000.0, "tax": 85.0, "gross": 1085.0}


# ---- rate table ---------------------------------------------------------

def test_set_and_get_rates_roundtrip(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tax.set_rate(slug, code="CA", name="California", rate_pct=7.25,
                 jurisdiction="CA", kind="sales")
    tax.set_rate(slug, code="VAT20", name="UK VAT", rate_pct=20.0,
                 jurisdiction="UK", kind="vat")
    rates = {r["code"]: r for r in tax.get_rates(slug)}
    assert rates["CA"]["rate_pct"] == 7.25
    assert rates["VAT20"]["kind"] == "vat"


# ---- taxed invoice (output tax) -----------------------------------------

def test_tax_invoice_posts_three_way_split(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    res = tax.record_tax_invoice(
        slug, party="Customer A", amount=1000.0, rate_pct=8.5,
        revenue_code="400", issue_date="2026-07-01", due_date="2026-07-31")
    assert res["net"] == 1000.0
    assert res["tax"] == 85.0
    assert res["gross"] == 1085.0
    sp = _splits(slug, res["entry_id"])
    assert sp["110"] == (1085.0, 0.0)   # A/R gross
    assert sp["400"] == (0.0, 1000.0)   # revenue net
    assert sp["230"] == (0.0, 85.0)     # sales tax payable


def test_tax_invoice_stored_at_gross(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    res = tax.record_tax_invoice(
        slug, party="Customer A", amount=1000.0, rate_pct=8.5,
        revenue_code="400", issue_date="2026-07-01", due_date="2026-07-31")
    # payments see the full gross receivable
    assert payments.target_balance(slug, "invoice", res["invoice_id"]) == 1085.0


def test_tax_invoice_inclusive_carves_out_tax(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    res = tax.record_tax_invoice(
        slug, party="Customer A", amount=1085.0, rate_pct=8.5, inclusive=True,
        revenue_code="400", issue_date="2026-07-01", due_date="2026-07-31")
    sp = _splits(slug, res["entry_id"])
    assert sp["110"] == (1085.0, 0.0)
    assert sp["400"] == (0.0, 1000.0)
    assert sp["230"] == (0.0, 85.0)


def test_tax_invoice_resolves_rate_code(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tax.set_rate(slug, code="CA", name="California", rate_pct=7.25)
    res = tax.record_tax_invoice(
        slug, party="Customer A", amount=1000.0, rate_code="CA",
        revenue_code="400", issue_date="2026-07-01", due_date="2026-07-31")
    assert res["tax"] == 72.5
    assert res["rate_code"] == "CA"


def test_tax_invoice_rejects_unknown_rate(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tax.record_tax_invoice(
            slug, party="X", amount=100.0, rate_code="NOPE",
            revenue_code="400", issue_date="2026-07-01", due_date="2026-07-31")


def test_tax_invoice_requires_a_rate(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tax.record_tax_invoice(
            slug, party="X", amount=100.0, revenue_code="400",
            issue_date="2026-07-01", due_date="2026-07-31")


def test_tax_invoice_rejects_non_revenue_account(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tax.record_tax_invoice(
            slug, party="X", amount=100.0, rate_pct=8.5, revenue_code="110",
            issue_date="2026-07-01", due_date="2026-07-31")


# ---- taxed bill (input / recoverable tax) -------------------------------

def test_tax_bill_posts_input_tax(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    res = tax.record_tax_bill(
        slug, party="Vendor B", amount=1000.0, rate_pct=8.5,
        expense_code="500", bill_date="2026-07-02", due_date="2026-08-01")
    sp = _splits(slug, res["entry_id"])
    assert sp["500"] == (1000.0, 0.0)   # expense net
    assert sp["230"] == (85.0, 0.0)     # input tax reduces liability
    assert sp["200"] == (0.0, 1085.0)   # A/P gross


# ---- tax liability report -----------------------------------------------

def test_tax_liability_nets_output_minus_input(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tax.record_tax_invoice(
        slug, party="Customer A", amount=1000.0, rate_pct=8.5,
        revenue_code="400", issue_date="2026-07-01", due_date="2026-07-31")
    tax.record_tax_bill(
        slug, party="Vendor B", amount=400.0, rate_pct=8.5,
        expense_code="500", bill_date="2026-07-05", due_date="2026-08-01")
    rep = tax.tax_liability(slug, start="2026-07-01", end="2026-07-31")
    assert rep["output_tax"] == 85.0
    assert rep["input_tax"] == 34.0
    assert rep["net_payable"] == 51.0


def test_tax_liability_excludes_out_of_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tax.record_tax_invoice(
        slug, party="Customer A", amount=1000.0, rate_pct=8.5,
        revenue_code="400", issue_date="2026-06-15", due_date="2026-07-15")
    rep = tax.tax_liability(slug, start="2026-07-01", end="2026-07-31")
    assert rep["output_tax"] == 0.0


# ---- 1099 ---------------------------------------------------------------

def test_1099_report_thresholds_and_flags(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    tax.set_vendor_1099(slug, "Contractor X")
    tax.set_vendor_1099(slug, "Small Vendor")
    # Contractor X paid 700 total (>= 600), Small Vendor 200 (< 600)
    payments.record_payment(slug, direction="disbursement", amount=500.0,
                            payment_date="2026-03-01", party="Contractor X",
                            cash_account="102")
    payments.record_payment(slug, direction="disbursement", amount=200.0,
                            payment_date="2026-05-01", party="Contractor X",
                            cash_account="102")
    payments.record_payment(slug, direction="disbursement", amount=200.0,
                            payment_date="2026-04-01", party="Small Vendor",
                            cash_account="102")
    rep = tax.form_1099_report(slug, year=2026)
    parties = {r["party"]: r for r in rep["vendors"]}
    assert parties["Contractor X"]["total_paid"] == 700.0
    assert parties["Contractor X"]["reportable"] is True
    assert parties["Small Vendor"]["reportable"] is False


def test_1099_ignores_unflagged_vendor(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    payments.record_payment(slug, direction="disbursement", amount=900.0,
                            payment_date="2026-03-01", party="Random LLC",
                            cash_account="102")
    rep = tax.form_1099_report(slug, year=2026)
    assert all(v["party"] != "Random LLC" for v in rep["vendors"])


# ---- CLI ----------------------------------------------------------------

def test_cli_tax_json_invoice(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    payload = json.dumps({"action": "invoice", "party": "Customer A",
                          "amount": 1000.0, "rate_pct": 8.5,
                          "revenue_code": "400", "issue_date": "2026-07-01",
                          "due_date": "2026-07-31"})
    out = subprocess.run(
        ["python3", "tax.py", "tax-json", "--client", slug, "--payload", payload],
        cwd=SCRIPTS_DIR, capture_output=True, text=True,
        env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    res = json.loads(out.stdout)
    assert res["ok"] is True
    assert res["tax"] == 85.0
