from datetime import date
import json
import sqlite3

import automation_profile
import tenancy
import workflow_runner


def _client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name="Acme Co", filing_cadence="quarterly")
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.executescript("""
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-06-09', 'sale', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 1000, 0), (1, '400', 0, 1000);
    """)
    con.commit()
    con.close()
    tenancy.refresh_summary(slug)
    profile = tenancy.get_automation_profile(slug)
    profile["sales_tax_jurisdictions"] = ["DE"]
    tenancy.set_automation_profile(slug, profile)


def test_sync_creates_pending_approval_jobs(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)

    result = workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    listed = workflow_runner.list_jobs()

    assert result["mode"] == "approval_control_plane"
    assert result["execution"] == "approval_required"
    assert listed["summary"]["pending_approval"] >= 1
    assert any(j["job_key"] == "sales_tax_review" for j in listed["jobs"])


def test_approve_and_run_safe_report_prep(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    job = workflow_runner.list_jobs(status="pending_approval")["jobs"][0]

    approved = workflow_runner.decide(job["workflow_job_id"], "approve", note="test ok")
    result = workflow_runner.run_approved()

    assert approved["workflow_status"] == "approved"
    assert result["execution"] == "report_prep_only"
    assert result["completed"] == 1
    receipt = result["receipts"][0]
    assert receipt["ledger_write"] == "disabled"
    assert (tmp_path / receipt["artifact"]).exists()
    stored = json.loads((tmp_path / receipt["artifact"]).read_text())
    assert stored["tax_or_payment_action"] == "disabled"


def test_skip_records_terminal_decision_without_running(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    job = workflow_runner.list_jobs(status="pending_approval")["jobs"][0]

    skipped = workflow_runner.decide(job["workflow_job_id"], "skip", note="not needed")
    result = workflow_runner.run_approved()

    assert skipped["workflow_status"] == "skipped"
    assert result["completed"] == 0


def test_dashboard_queue_can_still_be_generated(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    queue = automation_profile.workflow_queue(as_of=date(2026, 7, 6))

    assert queue["mode"] == "dry_run"
    assert queue["summary"]["approval_required"] >= 1


def test_sync_includes_ar_ap_workflow_items(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO invoices(id, party, issue_date, due_date, amount, status)
          VALUES(7, 'Customer A', '2026-06-01', '2026-07-05', 250.00, 'open');
        INSERT INTO bills(id, party, issue_date, due_date, amount, status)
          VALUES(9, 'Vendor B', '2026-06-01', '2026-07-08', 125.00, 'unpaid');
        """
    )
    con.commit()
    con.close()

    workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    jobs = workflow_runner.list_jobs()["jobs"]

    assert any(j["job_key"] == "ar_invoice_reminder:7" for j in jobs)
    assert any(j["job_key"] == "ap_bill_approval:9" for j in jobs)


def test_ar_ap_workflows_run_as_prep_receipts_only(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.execute(
        """INSERT INTO invoices(id, party, issue_date, due_date, amount, status)
           VALUES(7, 'Customer A', '2026-06-01', '2026-07-05', 250.00, 'open')"""
    )
    con.commit()
    con.close()

    workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    job = next(j for j in workflow_runner.list_jobs(status="pending_approval")["jobs"]
               if j["job_key"] == "ar_invoice_reminder:7")
    workflow_runner.decide(job["workflow_job_id"], "approve")
    result = workflow_runner.run_approved(limit=1)
    receipt = result["receipts"][0]

    assert receipt["execution"] == "client_payment_reminder_prep"
    assert receipt["external_delivery"] == "disabled"
    assert receipt["ledger_write"] == "disabled"
    assert receipt["payload"]["invoice_id"] == 7
