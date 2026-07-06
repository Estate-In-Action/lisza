import json

import config_doctor


def test_config_doctor_reports_missing_runtime_config(tmp_path, monkeypatch):
    monkeypatch.delenv("LISZA_SHEETS_SA_JSON", raising=False)
    monkeypatch.setattr(config_doctor.sheet_sync, "SPREADSHEET_ID", "YOUR_SPREADSHEET_ID")

    result = config_doctor.check_config(config_path=tmp_path / "missing.json")

    assert result["ok"] is False
    checks = {item["key"]: item for item in result["checks"]}
    assert checks["LISZA_SHEETS_SA_JSON"]["message"] == "missing"
    assert checks["SPREADSHEET_ID"]["message"] == "placeholder"
    assert checks["telegram_chat_id"]["message"] == "not discovered"


def test_config_doctor_reports_configured_runtime_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"telegram_chat_id": -12345}))
    monkeypatch.setenv("LISZA_SHEETS_SA_JSON", "{}")
    monkeypatch.setattr(config_doctor.sheet_sync, "SPREADSHEET_ID", "sheet123")

    result = config_doctor.check_config(config_path=cfg)

    assert result["ok"] is True
    assert all(item["ok"] for item in result["checks"])


def test_config_doctor_handles_invalid_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{bad")
    monkeypatch.setenv("LISZA_SHEETS_SA_JSON", "{}")
    monkeypatch.setattr(config_doctor.sheet_sync, "SPREADSHEET_ID", "sheet123")

    result = config_doctor.check_config(config_path=cfg)
    telegram = next(item for item in result["checks"] if item["key"] == "telegram_chat_id")

    assert result["ok"] is False
    assert telegram["message"] == "invalid json"
