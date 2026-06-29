import sqlite3

import pytest

import client_mgmt
import tenancy


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.registry_db()
    yield tmp_path


def test_schema_idempotent():
    cid = tenancy.register_client(slug="acme", display_name="Acme")
    db = tenancy.resolve_db("acme")
    con = sqlite3.connect(db)
    client_mgmt.ensure_client_mgmt_schema(con)
    client_mgmt.ensure_client_mgmt_schema(con)
    n_tbl = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='contracts'"
    ).fetchone()[0]
    n_col = sum(1 for r in con.execute("PRAGMA table_info(client_profile)")
                if r[1] == "monthly_fee")
    con.close()
    assert n_tbl == 1
    assert n_col == 1


def test_add_client_mints_book_and_persists_fee():
    cid = client_mgmt.add_client("riverside", "Riverside Cafe",
                                 entity_type="llc", monthly_fee=300.0)
    assert any(c.slug == "riverside" for c in tenancy.list_clients())
    assert tenancy.resolve_db("riverside").exists()
    got = client_mgmt.get_client("riverside")
    assert got["client_id"] == cid
    assert got["monthly_fee"] == 300.0
    assert got["display_name"] == "Riverside Cafe"


def test_add_client_rejects_taken_slug():
    client_mgmt.add_client("dup", "First")
    with pytest.raises(ValueError, match="slug"):
        client_mgmt.add_client("dup", "Second")


def test_add_client_rejects_system_slug():
    with pytest.raises(ValueError, match="slug"):
        client_mgmt.add_client("_house", "Sneaky")


def test_add_client_rejects_bad_slug():
    with pytest.raises(ValueError, match="slug"):
        client_mgmt.add_client("Bad Slug!", "Nope")


def test_list_all_and_status_filter():
    client_mgmt.add_client("a-co", "A Co")
    client_mgmt.add_client("b-co", "B Co")
    client_mgmt.archive_client("b-co")
    active = {c["slug"] for c in client_mgmt.list_all(status="active")}
    archived = {c["slug"] for c in client_mgmt.list_all(status="archived")}
    assert active == {"a-co"}
    assert archived == {"b-co"}
    assert {c["slug"] for c in client_mgmt.list_all()} == {"a-co", "b-co"}


def test_archive_then_restore():
    client_mgmt.add_client("toggle", "Toggle Inc")
    client_mgmt.archive_client("toggle")
    assert "toggle" not in {c.slug for c in tenancy.list_clients(status="active")}
    # book file survives archival (Rule #5: archive != delete)
    assert tenancy.resolve_db("toggle").exists()
    client_mgmt.restore_client("toggle")
    assert "toggle" in {c.slug for c in tenancy.list_clients(status="active")}


def test_house_never_listed_or_archived():
    tenancy.ensure_house()
    assert all(c["slug"] != tenancy.HOUSE_SLUG for c in client_mgmt.list_all())
    with pytest.raises(ValueError):
        client_mgmt.archive_client(tenancy.HOUSE_SLUG)


def test_update_terms_patches_book_and_mirrors_registry():
    client_mgmt.add_client("terms-co", "Terms Co", entity_type="llc")
    client_mgmt.update_terms("terms-co", display_name="Terms Co LLC",
                             entity_type="s_corp", filing_cadence="monthly",
                             monthly_fee=450.0, ein="12-3456789")
    got = client_mgmt.get_client("terms-co")
    assert got["display_name"] == "Terms Co LLC"
    assert got["entity_type"] == "s_corp"
    assert got["filing_cadence"] == "monthly"
    assert got["monthly_fee"] == 450.0
    assert got["ein"] == "12-3456789"
    # registry cache mirrors display_name + entity_type
    reg = next(c for c in tenancy.list_clients() if c.slug == "terms-co")
    assert reg.display_name == "Terms Co LLC"
    assert reg.entity_type == "s_corp"


def test_update_terms_rejects_unknown_field():
    client_mgmt.add_client("x-co", "X Co")
    with pytest.raises(ValueError, match="unknown"):
        client_mgmt.update_terms("x-co", not_a_term="x")


def test_contracts_roundtrip_and_status():
    client_mgmt.add_client("c-co", "C Co")
    cid1 = client_mgmt.add_contract("c-co", "FY26 Engagement",
                                    effective_date="2026-01-01",
                                    end_date="2026-12-31", monthly_fee=300.0)
    client_mgmt.add_contract("c-co", "Cleanup project", monthly_fee=0.0)
    rows = client_mgmt.list_contracts("c-co")
    assert len(rows) == 2
    assert {r["title"] for r in rows} == {"FY26 Engagement", "Cleanup project"}
    eng = next(r for r in rows if r["id"] == cid1)
    assert eng["status"] == "draft"
    client_mgmt.set_contract_status("c-co", cid1, "active")
    assert client_mgmt.list_contracts("c-co")
    updated = next(r for r in client_mgmt.list_contracts("c-co") if r["id"] == cid1)
    assert updated["status"] == "active"


def test_set_contract_status_rejects_invalid():
    client_mgmt.add_client("d-co", "D Co")
    cid = client_mgmt.add_contract("d-co", "Doc")
    with pytest.raises(ValueError, match="status"):
        client_mgmt.set_contract_status("d-co", cid, "bogus")
