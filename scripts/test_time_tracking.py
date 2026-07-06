import sqlite3

import tenancy
import time_tracking


def test_time_entries_invoice_to_open_ar(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    time_tracking.add_time_entry("acme", party="Customer A", work_date="2026-07-01", hours=2.5, rate=100)
    time_tracking.add_time_entry("acme", party="Customer A", work_date="2026-07-02", hours=1.0, rate=125)
    time_tracking.add_time_entry("acme", party="Customer B", work_date="2026-07-03", hours=3.0, rate=80)

    unbilled = time_tracking.unbilled_time("acme", party="Customer A")
    assert [r["amount"] for r in unbilled] == [250.0, 125.0]
    invoice_id = time_tracking.invoice_time("acme", party="Customer A", issue_date="2026-07-10")

    con = sqlite3.connect(tenancy.resolve_db("acme"))
    invoice = con.execute("SELECT party, amount, status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    linked = con.execute("SELECT COUNT(*) FROM time_entries WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
    remaining = con.execute("SELECT COUNT(*) FROM time_entries WHERE invoice_id IS NULL").fetchone()[0]
    entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()

    assert invoice == ("Customer A", 375.0, "open")
    assert linked == 2
    assert remaining == 1
    assert entries == 0


def test_invoice_time_requires_unbilled_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    try:
        time_tracking.invoice_time("acme", party="Customer A", issue_date="2026-07-10")
    except ValueError as exc:
        assert "no unbilled" in str(exc)
    else:
        raise AssertionError("expected no unbilled guard")
