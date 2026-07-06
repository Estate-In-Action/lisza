import fixed_assets
import tenancy


def test_fixed_asset_depreciation_schedule(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    asset_id = fixed_assets.add_asset(
        "acme",
        name="Laptop fleet",
        purchase_date="2026-01-15",
        cost=1200.0,
        useful_life_months=12,
        salvage_value=0.0,
    )
    schedule = fixed_assets.depreciation_schedule("acme", asset_id, as_of="2026-03-31")
    assert schedule["monthly_depreciation"] == 100.0
    assert schedule["months_elapsed"] == 3
    assert schedule["accumulated_depreciation"] == 300.0
    assert schedule["net_book_value"] == 900.0


def test_fixed_asset_depreciation_caps_at_depreciable_value(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    asset_id = fixed_assets.add_asset(
        "acme",
        name="Server",
        purchase_date="2024-01-01",
        cost=1300.0,
        useful_life_months=12,
        salvage_value=100.0,
    )
    schedule = fixed_assets.depreciation_schedule("acme", asset_id, as_of="2026-07-31")
    assert schedule["accumulated_depreciation"] == 1200.0
    assert schedule["net_book_value"] == 100.0
