import json
import os
import sqlite3
import subprocess

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

import credit_notes
import payments
import sales_pipeline
import tenancy


def _mk_client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name=slug.title())
    return slug


def _mk_invoice(slug, party="Customer A", amount=750.0, due="2026-08-03"):
    quote_id = sales_pipeline.create_quote(slug, party=party, amount=amount)
    order_id = sales_pipeline.accept_quote(slug, quote_id)
    return sales_pipeline.invoice_order(slug, order_id, issue_date="2026-07-04", due_date=due)


def _mk_bill(slug, party="Vendor B", amount=300.0, due="2026-08-01"):
    con = sqlite3.connect(tenancy.resolve_db(slug))
    cur = con.execute(
        "INSERT INTO bills(party, issue_date, due_date, amount) VALUES (?,?,?,?)",
        (party, "2026-07-02", due, amount),
    )
    con.commit()
    bill_id = cur.lastrowid
    con.close()
    return bill_id


def _splits(slug, entry_id):
    con = sqlite3.connect(tenancy.resolve_db(slug))
    rows = {r[0]: (r[1], r[2]) for r in
            con.execute("SELECT account, dr, cr FROM splits WHERE entry_id=?", (entry_id,))}
    con.close()
    return rows


# --- customer credit notes (sales credit memos) -----------------------------

def test_customer_credit_posts_reversing_entry_dr_revenue_cr_ar(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    res = credit_notes.record_credit_note(
        slug, kind="customer", amount=250.0, credit_date="2026-07-20",
        party="Customer A", offset_code="400", memo="goodwill credit",
    )
    assert res["status"] == "posted"
    assert res["allocated"] == 0.0
    assert res["unapplied"] == 250.0
    # Dr 400 revenue 250 / Cr 110 AR 250 — reverses the sale, reduces receivable
    splits = _splits(slug, res["entry_id"])
    assert splits["400"] == (250.0, 0.0)
    assert splits["110"] == (0.0, 250.0)


def test_customer_credit_applied_to_invoice_marks_paid(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=750.0)
    res = credit_notes.record_credit_note(
        slug, kind="customer", amount=750.0, credit_date="2026-07-21",
        party="Customer A", offset_code="400",
        allocations=[{"target_id": inv, "amount": 750.0}],
    )
    assert res["allocated"] == 750.0
    assert res["targets_paid"] == [inv]
    con = sqlite3.connect(tenancy.resolve_db(slug))
    status, paid = con.execute(
        "SELECT status, paid_date FROM invoices WHERE id=?", (inv,)).fetchone()
    con.close()
    assert status == "paid"
    assert paid == "2026-07-21"


def test_partial_customer_credit_reduces_invoice_balance(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0)
    credit_notes.record_credit_note(
        slug, kind="customer", amount=300.0, credit_date="2026-07-21",
        party="Customer A", offset_code="400",
        allocations=[{"target_id": inv, "amount": 300.0}],
    )
    con = sqlite3.connect(tenancy.resolve_db(slug))
    status = con.execute("SELECT status FROM invoices WHERE id=?", (inv,)).fetchone()[0]
    con.close()
    assert status == "open"
    assert credit_notes.target_balance(slug, "invoice", inv) == 700.0


def test_credit_then_payment_settles_invoice(tmp_path, monkeypatch):
    """A credit and a cash receipt together relieve one invoice (cross-module)."""
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0)
    credit_notes.record_credit_note(
        slug, kind="customer", amount=300.0, credit_date="2026-07-21",
        party="Customer A", offset_code="400",
        allocations=[{"target_id": inv, "amount": 300.0}],
    )
    # payments must see the credit-reduced balance of 700 and settle the rest
    assert payments.target_balance(slug, "invoice", inv) == 700.0
    res = payments.record_payment(
        slug, direction="receipt", amount=700.0, payment_date="2026-07-22",
        cash_account="102", allocations=[{"target_id": inv, "amount": 700.0}])
    assert res["targets_paid"] == [inv]
    con = sqlite3.connect(tenancy.resolve_db(slug))
    status = con.execute("SELECT status FROM invoices WHERE id=?", (inv,)).fetchone()[0]
    con.close()
    assert status == "paid"


def test_over_allocation_beyond_credit_amount_rejected(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0)
    with pytest.raises(ValueError, match="exceeds credit"):
        credit_notes.record_credit_note(
            slug, kind="customer", amount=200.0, credit_date="2026-07-21",
            party="Customer A", offset_code="400",
            allocations=[{"target_id": inv, "amount": 500.0}])


def test_over_allocation_beyond_invoice_balance_rejected(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=200.0)
    with pytest.raises(ValueError, match="exceeds .*balance"):
        credit_notes.record_credit_note(
            slug, kind="customer", amount=500.0, credit_date="2026-07-21",
            party="Customer A", offset_code="400",
            allocations=[{"target_id": inv, "amount": 500.0}])


def test_customer_credit_requires_income_offset(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    # 500 is an expense account — invalid offset for a customer credit
    with pytest.raises(ValueError, match="income"):
        credit_notes.record_credit_note(
            slug, kind="customer", amount=100.0, credit_date="2026-07-21",
            party="Customer A", offset_code="500")


# --- vendor credits (debit notes) -------------------------------------------

def test_vendor_credit_posts_dr_ap_cr_expense(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    res = credit_notes.record_credit_note(
        slug, kind="vendor", amount=120.0, credit_date="2026-07-20",
        party="Vendor B", offset_code="500", memo="returned supplies",
    )
    assert res["status"] == "posted"
    # Dr 200 AP 120 / Cr 500 expense 120 — reduces payable, reverses expense
    splits = _splits(slug, res["entry_id"])
    assert splits["200"] == (120.0, 0.0)
    assert splits["500"] == (0.0, 120.0)


def test_vendor_credit_applied_to_bill_marks_paid(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    bill = _mk_bill(slug, amount=300.0)
    res = credit_notes.record_credit_note(
        slug, kind="vendor", amount=300.0, credit_date="2026-07-21",
        party="Vendor B", offset_code="500",
        allocations=[{"target_id": bill, "amount": 300.0}])
    assert res["targets_paid"] == [bill]
    con = sqlite3.connect(tenancy.resolve_db(slug))
    status = con.execute("SELECT status FROM bills WHERE id=?", (bill,)).fetchone()[0]
    con.close()
    assert status == "paid"


def test_vendor_credit_requires_expense_offset(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="expense"):
        credit_notes.record_credit_note(
            slug, kind="vendor", amount=100.0, credit_date="2026-07-21",
            party="Vendor B", offset_code="400")


def test_wrong_kind_target_rejected(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    bill = _mk_bill(slug, amount=300.0)
    # a customer credit cannot be applied to a bill
    with pytest.raises(ValueError):
        credit_notes.record_credit_note(
            slug, kind="customer", amount=300.0, credit_date="2026-07-21",
            party="X", offset_code="400",
            allocations=[{"target_id": bill, "amount": 300.0}])


def test_list_credit_notes(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    credit_notes.record_credit_note(
        slug, kind="customer", amount=100.0, credit_date="2026-07-20",
        party="Customer A", offset_code="400")
    rows = credit_notes.list_credit_notes(slug)
    assert len(rows) == 1
    assert rows[0]["kind"] == "customer"
    assert rows[0]["amount"] == 100.0


# --- CLI --------------------------------------------------------------------

def test_credit_json_cli(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=250.0)
    payload = json.dumps({
        "kind": "customer", "amount": 250.0, "credit_date": "2026-07-19",
        "party": "Customer A", "offset_code": "400",
        "allocations": [{"target_id": inv, "amount": 250.0}],
    })
    proc = subprocess.run(
        ["python3", "credit_notes.py", "credit-json", "--client", slug, "--payload", payload],
        cwd=SCRIPTS_DIR,
        capture_output=True, text=True, env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    out = json.loads(proc.stdout.strip())
    assert out.get("ok") is True
    assert out["allocated"] == 250.0
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT status FROM invoices WHERE id=?", (inv,)).fetchone()[0] == "paid"
    con.close()
