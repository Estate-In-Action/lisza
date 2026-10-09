import sqlite3

import pytest

import online_payments
import sales_pipeline
import tenancy


def _invoice(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme")
    quote = sales_pipeline.create_quote("acme", party="Customer A", amount=250.0)
    order = sales_pipeline.accept_quote("acme", quote)
    return sales_pipeline.invoice_order("acme", order)


def test_preview_and_queue_do_not_post_or_call_provider(tmp_path, monkeypatch):
    invoice_id = _invoice(tmp_path, monkeypatch)
    preview = online_payments.prepare_request("acme", invoice_id)
    assert preview["dry_run"] is True
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    assert con.execute("SELECT COUNT(*) FROM online_payment_requests").fetchone()[0] == 0
    entries_before = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()

    queued = online_payments.prepare_request("acme", invoice_id, commit=True)
    assert queued["status"] == "pending_approval"
    assert queued["provider_call"] is False
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == entries_before
    assert con.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0] == "open"
    con.close()


def test_checkout_payload_requires_feature_gate_and_approval(tmp_path, monkeypatch):
    invoice_id = _invoice(tmp_path, monkeypatch)
    request_id = online_payments.prepare_request("acme", invoice_id, commit=True)["request_id"]
    with pytest.raises(PermissionError, match="disabled"):
        online_payments.checkout_payload("acme", request_id, success_url="https://ok", cancel_url="https://cancel")
    monkeypatch.setenv("LISZA_ONLINE_PAYMENTS_ENABLED", "1")
    with pytest.raises(PermissionError, match="approval"):
        online_payments.checkout_payload("acme", request_id, success_url="https://ok", cancel_url="https://cancel")

    online_payments.approve_request("acme", request_id, approved_by="operator")
    payload = online_payments.checkout_payload(
        "acme", request_id, success_url="https://ok", cancel_url="https://cancel"
    )
    assert payload["amount_minor"] == 25000
    assert payload["provider_call"] is False
    assert payload["settlement_authority"] is False
