import csv
import json
import sqlite3

import custom_reports
import tenancy


def _entry(con, entry_id, entry_date, account, dr, cr, offset):
    con.execute(
        "INSERT INTO entries(id, entry_date, description, source, status) VALUES (?, ?, 'x', 'test', 'posted')",
        (entry_id, entry_date),
    )
    con.execute("INSERT INTO splits(entry_id, account, dr, cr) VALUES (?, ?, ?, ?)", (entry_id, account, dr, cr))
    con.execute("INSERT INTO splits(entry_id, account, dr, cr) VALUES (?, ?, ?, ?)", (entry_id, offset, cr, dr))


def test_variance_report_calculates_pnl_delta(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    _entry(con, 1, "2026-06-10", "400", 0, 1000, "102")
    _entry(con, 2, "2026-06-11", "500", 300, 0, "102")
    _entry(con, 3, "2026-07-10", "400", 0, 1200, "102")
    _entry(con, 4, "2026-07-11", "500", 450, 0, "102")
    con.commit()
    con.close()

    report = custom_reports.variance_report(
        "acme",
        current_start="2026-07-01",
        current_end="2026-07-31",
        prior_start="2026-06-01",
        prior_end="2026-06-30",
    )
    rows = {r["account"]: r for r in report["rows"]}
    assert rows["400"]["current"] == 1200.0
    assert rows["400"]["prior"] == 1000.0
    assert rows["400"]["delta"] == 200.0
    assert rows["500"]["current"] == 450.0
    assert rows["500"]["prior"] == 300.0
    assert rows["500"]["delta_pct"] == 50.0
    assert report["totals"]["current_income"] == 1200.0
    assert report["totals"]["current_expense"] == 450.0


def test_write_variance_report_outputs_json_and_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    out = custom_reports.write_variance_report(
        "acme",
        out_dir=tmp_path / "reports",
        current_start="2026-07-01",
        current_end="2026-07-31",
        prior_start="2026-06-01",
        prior_end="2026-06-30",
    )
    json_path = tmp_path / "reports" / "pnl-variance-2026-07-01-2026-07-31.json"
    csv_path = tmp_path / "reports" / "pnl-variance-2026-07-01-2026-07-31.csv"
    assert json.loads(json_path.read_text())["report"] == "pnl_variance"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert out["artifacts"]["json"] == str(json_path)
