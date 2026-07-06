import sqlite3

import purchasing_pipeline
import tenancy


def test_purchase_order_to_bill(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")

    po_id = purchasing_pipeline.create_purchase_order(
        "acme",
        vendor="Vendor B",
        amount=425.0,
        order_date="2026-07-01",
        expected_date="2026-07-10",
        memo="Materials",
        status="sent",
    )
    purchasing_pipeline.approve_purchase_order("acme", po_id)
    bill_id = purchasing_pipeline.bill_purchase_order(
        "acme",
        po_id,
        issue_date="2026-07-11",
        due_date="2026-08-10",
    )

    con = sqlite3.connect(tenancy.resolve_db("acme"))
    po = con.execute("SELECT status, bill_id FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    bill = con.execute("SELECT party, amount, status FROM bills WHERE id=?", (bill_id,)).fetchone()
    entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()

    assert po == ("billed", bill_id)
    assert bill == ("Vendor B", 425.0, "unpaid")
    assert entries == 0


def test_billed_purchase_order_cannot_bill_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    po_id = purchasing_pipeline.create_purchase_order("acme", vendor="Vendor B", amount=100.0, status="approved")
    purchasing_pipeline.bill_purchase_order("acme", po_id, issue_date="2026-07-11")
    try:
        purchasing_pipeline.bill_purchase_order("acme", po_id, issue_date="2026-07-12")
    except ValueError as exc:
        assert "billed" in str(exc)
    else:
        raise AssertionError("expected billed purchase order guard")
