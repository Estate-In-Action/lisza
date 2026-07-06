from datetime import date
import json
import subprocess
import sys

import automation_profile
import tenancy


def _client(tmp_path, monkeypatch, slug="acme", cadence="quarterly"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name="Acme", filing_cadence=cadence)
    con = __import__("sqlite3").connect(tenancy.resolve_db(slug))
    con.executescript("""
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-06-09', 'sale', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 1000, 0), (1, '400', 0, 1000);
    """)
    con.commit()
    con.close()
    tenancy.refresh_summary(slug)


def test_plan_due_jobs_is_advisory_and_profile_driven(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    profile = tenancy.get_automation_profile("acme")
    profile["sales_tax_jurisdictions"] = ["DE"]
    tenancy.set_automation_profile("acme", profile)

    jobs = automation_profile.plan_due_jobs("acme", as_of=date(2026, 7, 6))
    keys = {j["key"] for j in jobs}

    assert "weekly_digest" in keys
    assert "monthly_close" in keys
    assert "sales_tax_review" in keys
    assert all(j["status"] in ("due_now", "upcoming") for j in jobs)


def test_plan_all_lists_clients(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch, slug="a-co")
    _client(tmp_path, monkeypatch, slug="b-co")

    plan = automation_profile.plan_all(as_of=date(2026, 7, 6))

    assert {c["slug"] for c in plan["clients"]} == {"a-co", "b-co"}
    assert all("jobs" in c for c in plan["clients"])


def test_cli_set_get_and_plan(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    env = {"LISZA_HOME": str(tmp_path)}

    subprocess.run(
        [sys.executable, "automation_profile.py", "set", "acme",
         "--delivery", "email", "--sales-tax-jurisdictions", "DE,VA"],
        cwd=__import__("pathlib").Path(__file__).resolve().parent,
        env={**__import__("os").environ, **env},
        check=True,
        capture_output=True,
        text=True,
    )
    got = subprocess.run(
        [sys.executable, "automation_profile.py", "get", "acme"],
        cwd=__import__("pathlib").Path(__file__).resolve().parent,
        env={**__import__("os").environ, **env},
        check=True,
        capture_output=True,
        text=True,
    )
    profile = json.loads(got.stdout)

    assert profile["delivery"] == "email"
    assert profile["sales_tax_jurisdictions"] == ["DE", "VA"]
