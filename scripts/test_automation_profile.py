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


def test_set_json_and_workflow_queue(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)

    profile = automation_profile.set_profile_from_json("acme", {
        "delivery": "telegram",
        "sales_tax_jurisdictions": ["DE"],
        "reports": {"weekly_digest": True, "monthly_close": False, "quarterly_packet": True},
    })
    queue = automation_profile.workflow_queue(as_of=date(2026, 7, 6))

    assert profile["delivery"] == "telegram"
    assert profile["reports"]["monthly_close"] is False
    assert queue["mode"] == "dry_run"
    assert queue["execution"] == "disabled"
    assert queue["summary"]["approval_required"] >= 1
    assert any(j["key"] == "sales_tax_review" for j in queue["queue"])


def test_setup_flow_marks_required_questions_and_suggestions(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    con = __import__("sqlite3").connect(tenancy.resolve_db("acme"))
    ent = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    con.execute(
        "INSERT INTO employees(entity_id,name,filing_status,work_state,"
        "residence_state,annual_salary) VALUES(?,?,?,?,?,?)",
        (ent, "Ada", "single", "DE", "DE", 52000.0))
    con.commit()
    con.close()
    profile = tenancy.get_automation_profile("acme")
    profile["payroll_schedule"] = "none"
    tenancy.set_automation_profile("acme", profile)

    setup = automation_profile.setup_flow("acme")
    payroll = next(q for q in setup["questions"] if q["key"] == "payroll_schedule")

    assert setup["status"] == "needs_setup"
    assert payroll["required"] is True
    assert payroll["complete"] is False
    assert setup["suggested_profile"]["payroll_schedule"] == "biweekly"
