import sqlite3

import pytest

import crm
import tenancy


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    # registry + COA available for conversion path
    tenancy.registry_db()
    yield tmp_path


def test_schema_idempotent():
    con = sqlite3.connect(tenancy.registry_path())
    crm.ensure_crm_schema(con)
    crm.ensure_crm_schema(con)
    n = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='leads'"
    ).fetchone()[0]
    con.close()
    assert n == 1


def test_add_and_list_roundtrip():
    lid = crm.add_lead("Riverside Cafe", contact_name="Jo", email="jo@x.com",
                       est_monthly_fee=300.0, source="referral")
    leads = crm.list_leads()
    assert len(leads) == 1
    assert leads[0]["lead_id"] == lid
    assert leads[0]["display_name"] == "Riverside Cafe"
    assert leads[0]["stage"] == "intake"
    assert leads[0]["est_monthly_fee"] == 300.0


def test_list_stage_filter():
    a = crm.add_lead("A")
    crm.add_lead("B")
    crm.set_stage(a, "in_process")
    assert {l["display_name"] for l in crm.list_leads(stage="in_process")} == {"A"}
    assert {l["display_name"] for l in crm.list_leads(stage="intake")} == {"B"}


def test_set_stage_rejects_invalid():
    lid = crm.add_lead("A")
    with pytest.raises(ValueError, match="stage"):
        crm.set_stage(lid, "bogus")


def test_update_rejects_unknown_column():
    lid = crm.add_lead("A")
    with pytest.raises(ValueError, match="unknown"):
        crm.update_lead(lid, not_a_field="x")


def test_update_patches_fields():
    lid = crm.add_lead("A")
    crm.update_lead(lid, email="new@x.com", est_monthly_fee=500.0)
    lead = crm.get_lead(lid)
    assert lead["email"] == "new@x.com"
    assert lead["est_monthly_fee"] == 500.0


def test_convert_lead_mints_client_and_backlinks():
    lid = crm.add_lead("Northwind LLC", entity_type="llc")
    client_id = crm.convert_lead(lid, "northwind", display_name="Northwind LLC")
    # registry row exists and is visible in the roster
    assert any(c.slug == "northwind" for c in tenancy.list_clients())
    # ledger book exists
    assert tenancy.resolve_db("northwind").exists()
    lead = crm.get_lead(lid)
    assert lead["stage"] == "won"
    assert lead["converted_client_id"] == client_id
    assert lead["converted_at"]


def test_convert_is_idempotent_guarded():
    lid = crm.add_lead("Acme")
    crm.convert_lead(lid, "acme", display_name="Acme")
    with pytest.raises(ValueError, match="already converted"):
        crm.convert_lead(lid, "acme2", display_name="Acme 2")


def test_convert_rejects_taken_slug():
    tenancy.register_client(slug="taken", display_name="Taken Inc")
    lid = crm.add_lead("Other")
    with pytest.raises(ValueError, match="slug"):
        crm.convert_lead(lid, "taken", display_name="Other")


def test_set_stage_refuses_converted_lead():
    lid = crm.add_lead("Acme")
    crm.convert_lead(lid, "acme", display_name="Acme")
    with pytest.raises(ValueError, match="converted"):
        crm.set_stage(lid, "intake")


def test_funnel_summary():
    a = crm.add_lead("A", est_monthly_fee=100.0)
    b = crm.add_lead("B", est_monthly_fee=200.0)
    crm.add_lead("C", est_monthly_fee=50.0)
    crm.set_stage(a, "in_process")
    crm.convert_lead(b, "b-co", display_name="B")
    s = crm.funnel_summary()
    assert s["counts"]["intake"] == 1
    assert s["counts"]["in_process"] == 1
    assert s["counts"]["won"] == 1
    # est pipeline sums OPEN stages only (intake + in_process): 50 + 100
    assert s["est_pipeline"] == 150.0
    assert s["total"] == 3
