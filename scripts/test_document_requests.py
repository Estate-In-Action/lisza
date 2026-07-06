from datetime import date
import sqlite3

import document_requests
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


def test_default_document_requests_are_idempotent(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)

    first = document_requests.ensure_defaults("acme", as_of=date(2026, 7, 6))
    second = document_requests.ensure_defaults("acme", as_of=date(2026, 7, 6))
    listed = document_requests.list_requests("acme")

    assert first["inserted"] == 4
    assert second["inserted"] == 0
    assert listed["summary"]["open"] == 4
    assert {r["doc_key"] for r in listed["requests"]} >= {"w9", "bank_statement_current"}


def test_document_request_status_transition(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    document_requests.ensure_defaults("acme", as_of=date(2026, 7, 6))
    req = document_requests.list_requests("acme")["requests"][0]

    received = document_requests.transition(req["document_request_id"], "received", note="uploaded")
    listed = document_requests.list_requests("acme")

    assert received["status"] == "received"
    assert listed["summary"]["received"] == 1
    assert listed["summary"]["open"] == 3


def test_document_requests_feed_workflow_queue_and_followup(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    document_requests.upsert_request(
        "acme", "statement_june", "June bank statement",
        due_date="2026-07-01")

    workflow_runner.sync_jobs(as_of=date(2026, 7, 6))
    jobs = workflow_runner.list_jobs(status="pending_approval")["jobs"]
    doc_job = next(j for j in jobs if j["job_key"].startswith("document_request:"))
    approved = workflow_runner.decide(doc_job["workflow_job_id"], "approve")
    result = workflow_runner.run_approved()
    request = document_requests.list_requests("acme")["requests"][0]

    assert approved["workflow_status"] == "approved"
    assert result["completed"] >= 1
    assert any(r["execution"] == "document_followup" for r in result["receipts"])
    assert request["followup_count"] == 1
