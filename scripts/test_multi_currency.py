import sqlite3

import multi_currency
import tenancy


def _client_with_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-07-10', 'euro sale', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 110.00, 0), (1, '400', 0, 110.00);
        """
    )
    con.commit()
    con.close()


def test_fx_rate_lookup_uses_latest_on_or_before_date(tmp_path, monkeypatch):
    _client_with_entry(tmp_path, monkeypatch)
    multi_currency.set_fx_rate("acme", rate_date="2026-07-01", from_currency="EUR", rate=1.10)
    multi_currency.set_fx_rate("acme", rate_date="2026-07-15", from_currency="EUR", rate=1.20)
    assert multi_currency.get_fx_rate("acme", rate_date="2026-07-10", from_currency="EUR") == 1.10
    assert multi_currency.get_fx_rate("acme", rate_date="2026-07-20", from_currency="EUR") == 1.20


def test_annotate_entry_currency_records_translation(tmp_path, monkeypatch):
    _client_with_entry(tmp_path, monkeypatch)
    multi_currency.set_fx_rate("acme", rate_date="2026-07-01", from_currency="EUR", rate=1.10)
    meta = multi_currency.annotate_entry_currency(
        "acme",
        entry_id=1,
        source_currency="EUR",
        source_amount=100.0,
        rate_date="2026-07-10",
    )
    assert meta["functional_amount"] == 110.0
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    row = con.execute("SELECT source_currency, fx_rate, functional_amount FROM entry_currency_meta WHERE entry_id=1").fetchone()
    con.close()
    assert row == ("EUR", 1.10, 110.0)


def test_missing_fx_rate_is_guarded(tmp_path, monkeypatch):
    _client_with_entry(tmp_path, monkeypatch)
    try:
        multi_currency.annotate_entry_currency(
            "acme",
            entry_id=1,
            source_currency="EUR",
            source_amount=100.0,
        )
    except ValueError as exc:
        assert "missing FX rate" in str(exc)
    else:
        raise AssertionError("expected missing FX rate guard")
