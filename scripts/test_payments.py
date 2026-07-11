import os
import sqlite3

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

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


def test_full_receipt_marks_invoice_paid_and_posts_cash(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=750.0)

    res = payments.record_payment(
        slug,
        direction="receipt",
        amount=750.0,
        payment_date="2026-07-15",
        party="Customer A",
        method="ach",
        cash_account="102",
        allocations=[{"target_id": inv, "amount": 750.0}],
    )

    assert res["status"] == "posted"
    assert res["allocated"] == 750.0
    assert res["unapplied"] == 0.0

    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    invoice = con.execute("SELECT status, paid_date FROM invoices WHERE id=?", (inv,)).fetchone()
    # GL entry balanced: Dr 102 cash 750 / Cr 110 AR 750
    entry_id = res["entry_id"]
    splits = {r["account"]: (r["dr"], r["cr"]) for r in
              con.execute("SELECT account, dr, cr FROM splits WHERE entry_id=?", (entry_id,))}
    status = con.execute("SELECT status FROM entries WHERE id=?", (entry_id,)).fetchone()[0]
    con.close()

    assert invoice["status"] == "paid"
    assert invoice["paid_date"] == "2026-07-15"
    assert splits["102"] == (750.0, 0.0)
    assert splits["110"] == (0.0, 750.0)
    assert status == "posted"


def test_partial_receipt_keeps_invoice_open(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0)

    payments.record_payment(
        slug, direction="receipt", amount=400.0, payment_date="2026-07-15",
        cash_account="102", allocations=[{"target_id": inv, "amount": 400.0}],
    )

    con = sqlite3.connect(tenancy.resolve_db(slug))
    status = con.execute("SELECT status FROM invoices WHERE id=?", (inv,)).fetchone()[0]
    con.close()
    assert status == "open"
    assert payments.target_balance(slug, "invoice", inv) == 600.0


def test_two_partials_settle_invoice(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0)
    payments.record_payment(slug, direction="receipt", amount=400.0,
                            payment_date="2026-07-15", cash_account="102",
                            allocations=[{"target_id": inv, "amount": 400.0}])
    payments.record_payment(slug, direction="receipt", amount=600.0,
                            payment_date="2026-07-20", cash_account="102",
                            allocations=[{"target_id": inv, "amount": 600.0}])
    con = sqlite3.connect(tenancy.resolve_db(slug))
    status, paid = con.execute("SELECT status, paid_date FROM invoices WHERE id=?", (inv,)).fetchone()
    con.close()
    assert status == "paid"
    assert paid == "2026-07-20"
    assert payments.target_balance(slug, "invoice", inv) == 0.0


def test_receipt_across_two_invoices(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    a = _mk_invoice(slug, party="Cust A", amount=200.0)
    b = _mk_invoice(slug, party="Cust A", amount=300.0)
    res = payments.record_payment(
        slug, direction="receipt", amount=500.0, payment_date="2026-07-18",
        cash_account="102",
        allocations=[{"target_id": a, "amount": 200.0}, {"target_id": b, "amount": 300.0}],
    )
    assert res["allocated"] == 500.0
    con = sqlite3.connect(tenancy.resolve_db(slug))
    sa = con.execute("SELECT status FROM invoices WHERE id=?", (a,)).fetchone()[0]
    sb = con.execute("SELECT status FROM invoices WHERE id=?", (b,)).fetchone()[0]
    con.close()
    assert sa == "paid" and sb == "paid"


def test_full_bill_disbursement_posts_and_marks_paid(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    bill = _mk_bill(slug, amount=300.0)
    res = payments.record_payment(
        slug, direction="disbursement", amount=300.0, payment_date="2026-07-16",
        cash_account="102", allocations=[{"target_id": bill, "amount": 300.0}],
    )
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    b = con.execute("SELECT status, paid_date FROM bills WHERE id=?", (bill,)).fetchone()
    splits = {r["account"]: (r["dr"], r["cr"]) for r in
              con.execute("SELECT account, dr, cr FROM splits WHERE entry_id=?", (res["entry_id"],))}
    con.close()
    assert b["status"] == "paid"
    assert b["paid_date"] == "2026-07-16"
    # Dr 200 AP 300 / Cr 102 cash 300
    assert splits["200"] == (300.0, 0.0)
    assert splits["102"] == (0.0, 300.0)


def test_over_allocation_beyond_payment_amount_is_rejected(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=1000.0)
    with pytest.raises(ValueError, match="exceeds payment"):
        payments.record_payment(
            slug, direction="receipt", amount=400.0, payment_date="2026-07-15",
            cash_account="102", allocations=[{"target_id": inv, "amount": 500.0}])


def test_over_allocation_beyond_invoice_balance_is_rejected(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=200.0)
    with pytest.raises(ValueError, match="exceeds .*balance"):
        payments.record_payment(
            slug, direction="receipt", amount=500.0, payment_date="2026-07-15",
            cash_account="102", allocations=[{"target_id": inv, "amount": 500.0}])


def test_wrong_direction_target_rejected(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    bill = _mk_bill(slug, amount=300.0)
    # a receipt cannot be applied to a bill
    with pytest.raises(ValueError):
        payments.record_payment(
            slug, direction="receipt", amount=300.0, payment_date="2026-07-15",
            cash_account="102", allocations=[{"target_id": bill, "amount": 300.0}])


def test_unapplied_receipt_posts_full_to_control(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=200.0)
    res = payments.record_payment(
        slug, direction="receipt", amount=500.0, payment_date="2026-07-15",
        cash_account="102", allocations=[{"target_id": inv, "amount": 200.0}])
    assert res["allocated"] == 200.0
    assert res["unapplied"] == 300.0
    con = sqlite3.connect(tenancy.resolve_db(slug))
    splits = {r[0]: (r[1], r[2]) for r in
              con.execute("SELECT account, dr, cr FROM splits WHERE entry_id=?", (res["entry_id"],))}
    con.close()
    # full 500 cash in, full 500 credited to AR control (200 applied + 300 on-account credit)
    assert splits["102"] == (500.0, 0.0)
    assert splits["110"] == (0.0, 500.0)


def test_pay_json_cli(tmp_path, monkeypatch):
    import json
    import subprocess

    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=250.0)
    payload = json.dumps({
        "direction": "receipt", "amount": 250.0, "payment_date": "2026-07-19",
        "cash_account": "102", "party": "Customer A",
        "allocations": [{"target_id": inv, "amount": 250.0}],
    })
    proc = subprocess.run(
        ["python3", "payments.py", "pay-json", "--client", slug, "--payload", payload],
        cwd=SCRIPTS_DIR,
        capture_output=True, text=True, env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    out = json.loads(proc.stdout.strip())
    assert out.get("ok") is True
    assert out["allocated"] == 250.0
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT status FROM invoices WHERE id=?", (inv,)).fetchone()[0] == "paid"
    con.close()


def test_open_items_lists_receivables_and_payables(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    inv = _mk_invoice(slug, amount=200.0)
    bill = _mk_bill(slug, amount=300.0)
    ar = payments.open_items(slug, "receipt")
    ap = payments.open_items(slug, "disbursement")
    assert any(i["target_id"] == inv and i["balance"] == 200.0 for i in ar)
    assert any(i["target_id"] == bill and i["balance"] == 300.0 for i in ap)
