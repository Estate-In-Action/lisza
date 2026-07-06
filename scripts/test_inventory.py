import inventory
import tenancy


def test_inventory_movements_roll_to_stock_status(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    inventory.upsert_item("acme", sku="WOOD-001", name="Maple blank", unit="board", standard_cost=12.5)
    inventory.record_movement("acme", sku="WOOD-001", movement_date="2026-07-01", quantity=10, movement_type="receive")
    inventory.record_movement("acme", sku="WOOD-001", movement_date="2026-07-02", quantity=3, movement_type="consume")
    status = inventory.stock_status("acme")[0]
    assert status["sku"] == "WOOD-001"
    assert status["quantity_on_hand"] == 7.0
    assert status["inventory_value"] == 87.5


def test_inventory_unknown_sku_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    try:
        inventory.record_movement("acme", sku="MISSING", movement_date="2026-07-01", quantity=1, movement_type="receive")
    except ValueError as exc:
        assert "unknown active sku" in str(exc)
    else:
        raise AssertionError("expected unknown sku guard")
