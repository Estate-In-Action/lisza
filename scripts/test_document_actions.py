import json
import sqlite3

import document_actions
import tenancy
import workflow_runner


def test_queue_invoice_send_is_approval_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.execute(
        """INSERT INTO invoices(id, party, issue_date, due_date, amount, status)
           VALUES(7, 'Customer A', '2026-07-01', '2026-07-31', 1200.0, 'open')"""
    )
    con.commit(); con.close()

    queued = document_actions.queue_invoice_send("acme", 7)

    assert queued["workflow_status"] == "pending_approval"
    assert queued["external_delivery"] == "disabled_until_approved"
    assert queued["ledger_write"] == "disabled"

    job = next(j for j in workflow_runner.list_jobs(status="pending_approval")["jobs"]
               if j["job_key"] == "send_invoice:7")
    workflow_runner.decide(job["workflow_job_id"], "approve")
    result = workflow_runner.run_approved(limit=1)
    receipt = result["receipts"][0]

    assert receipt["execution"] == "invoice_send_prep"
    assert receipt["external_delivery"] == "disabled"
    assert receipt["ledger_write"] == "disabled"
    stored = json.loads((tmp_path / receipt["artifact"]).read_text())
    assert stored["payload"]["invoice_id"] == 7


def test_paid_invoice_cannot_be_queued_for_send(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.execute(
        """INSERT INTO invoices(id, party, issue_date, due_date, amount, status)
           VALUES(8, 'Customer B', '2026-07-01', '2026-07-31', 99.0, 'paid')"""
    )
    con.commit(); con.close()
    try:
        document_actions.queue_invoice_send("acme", 8)
    except ValueError as exc:
        assert "paid" in str(exc)
    else:
        raise AssertionError("expected paid invoice guard")
