import json
import client_profiles
import seed_client
import tenancy
import build_dashboard


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="sole_prop", filing_cadence="annual")
    seed_client.seed(client_profiles.JB_DESIGN, slug="jb-design")
    tenancy.register_client(slug="harborside-group", display_name="Harborside Group",
                            entity_type="llc", filing_cadence="quarterly")
    seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    tenancy.refresh_all()


def test_build_dashboard_shape_and_figures(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    data = build_dashboard.build_dashboard()
    assert data["prefs"]["layout"] == "tile"
    assert data["prefs"]["card_fields"] == ["cash", "open_ar", "open_ap", "last_entry"]
    assert "generated_at" in data
    by_slug = {c["slug"]: c for c in data["clients"]}
    # both active clients present, ordered by display name (Harborside before J.B.)
    assert [c["slug"] for c in data["clients"]] == ["harborside-group", "jb-design"]
    # figures equal the cached summary
    s = tenancy.refresh_summary("harborside-group")
    assert by_slug["harborside-group"]["cash"] == s["cash"]
    # consolidates iff entity_count > 1
    assert by_slug["harborside-group"]["consolidates"] is True
    assert by_slug["harborside-group"]["entity_count"] == 3
    assert by_slug["jb-design"]["consolidates"] is False
    assert by_slug["jb-design"]["entity_count"] == 1
    # filing due carried through
    assert by_slug["harborside-group"]["next_filing_due"] is not None


def test_build_dashboard_excludes_archived(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    import sqlite3
    reg = sqlite3.connect(tenancy.registry_path())
    reg.execute("UPDATE clients SET status='archived' WHERE slug='jb-design'")
    reg.commit()
    reg.close()
    data = build_dashboard.build_dashboard()
    assert [c["slug"] for c in data["clients"]] == ["harborside-group"]


def test_write_dashboard_writes_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = build_dashboard.write_dashboard(tmp_path / "out" / "dashboard.json")
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["prefs"]["layout"] == "tile"
    assert len(data["clients"]) == 2
