import sqlite3

import number_series
import tenancy


def test_number_series_preview_and_reserve(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")

    assert number_series.preview_next("acme", "invoice") == "INV-00001"
    reserved = number_series.reserve_next(
        "acme", "invoice", document_type="invoice", document_id=7
    )

    assert reserved["document_number"] == "INV-00001"
    assert number_series.preview_next("acme", "invoice") == "INV-00002"

    con = sqlite3.connect(tenancy.resolve_db("acme"))
    stored = con.execute(
        "SELECT document_number, document_type, document_id, status FROM number_series_reservations"
    ).fetchone()
    con.close()
    assert stored == ("INV-00001", "invoice", 7, "reserved")


def test_series_state_has_core_document_types(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    state = number_series.series_state("acme")
    assert set(state) >= {"invoice", "bill", "journal", "payment"}
    assert state["payment"]["next_number"] == "PAY-00001"
