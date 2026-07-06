import party_directory
import tenancy


def test_upsert_party_and_role_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    cid = party_directory.upsert_party("acme", name="Customer A", role="customer", email="a@example.com")
    vid = party_directory.upsert_party("acme", name="Vendor B", role="vendor")
    assert cid != vid

    customers = party_directory.list_parties("acme", role="customer")
    assert [p["name"] for p in customers] == ["Customer A"]
    vendors = party_directory.list_parties("acme", role="vendor")
    assert [p["name"] for p in vendors] == ["Vendor B"]


def test_upsert_party_updates_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    first = party_directory.upsert_party("acme", name="Customer A", role="customer", email="old@example.com")
    second = party_directory.upsert_party("acme", name="Customer A", role="customer", email="new@example.com")
    assert first == second
    party = party_directory.list_parties("acme", role="customer")[0]
    assert party["email"] == "new@example.com"


def test_archive_party_hides_from_active_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    party_id = party_directory.upsert_party("acme", name="Vendor B", role="vendor")
    party_directory.archive_party("acme", party_id)
    assert party_directory.list_parties("acme", role="vendor") == []
    archived = party_directory.list_parties("acme", role="vendor", active_only=False)
    assert archived[0]["active"] == 0
