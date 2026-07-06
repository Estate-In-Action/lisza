import sqlite3

import sales_pipeline
import tenancy


def test_quote_to_order_to_invoice(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")

    quote_id = sales_pipeline.create_quote(
        "acme",
        party="Customer A",
        quote_date="2026-07-01",
        valid_until="2026-07-31",
        amount=750.0,
        memo="Website redesign",
        status="sent",
    )
    order_id = sales_pipeline.accept_quote("acme", quote_id, order_date="2026-07-03")
    invoice_id = sales_pipeline.invoice_order(
        "acme",
        order_id,
        issue_date="2026-07-04",
        due_date="2026-08-03",
    )

    con = sqlite3.connect(tenancy.resolve_db("acme"))
    quote_status = con.execute("SELECT status FROM sales_quotes WHERE id=?", (quote_id,)).fetchone()[0]
    order = con.execute("SELECT status, invoice_id FROM sales_orders WHERE id=?", (order_id,)).fetchone()
    invoice = con.execute("SELECT party, amount, status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()

    assert quote_status == "accepted"
    assert order == ("invoiced", invoice_id)
    assert invoice == ("Customer A", 750.0, "open")
    assert entries == 0


def test_invoice_order_is_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    quote_id = sales_pipeline.create_quote("acme", party="Customer A", amount=100.0)
    order_id = sales_pipeline.accept_quote("acme", quote_id)
    sales_pipeline.invoice_order("acme", order_id, issue_date="2026-07-04")
    try:
        sales_pipeline.invoice_order("acme", order_id, issue_date="2026-07-05")
    except ValueError as exc:
        assert "invoiced" in str(exc)
    else:
        raise AssertionError("expected invoiced order guard")
