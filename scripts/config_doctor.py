#!/usr/bin/env python3
"""Check LISZA runtime configuration without printing secret values."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import sheet_sync


ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"


def _load_config(path: Path = CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {"_error": "invalid json"}


def check_config(*, config_path: Path = CONFIG) -> dict[str, Any]:
    cfg = _load_config(config_path)
    checks = []

    sheets_secret = bool(os.environ.get("LISZA_SHEETS_SA_JSON"))
    checks.append({
        "key": "LISZA_SHEETS_SA_JSON",
        "ok": sheets_secret,
        "message": "set" if sheets_secret else "missing",
    })

    spreadsheet_id = getattr(sheet_sync, "SPREADSHEET_ID", "")
    spreadsheet_ok = bool(spreadsheet_id and spreadsheet_id != "YOUR_SPREADSHEET_ID")
    checks.append({
        "key": "SPREADSHEET_ID",
        "ok": spreadsheet_ok,
        "message": "configured" if spreadsheet_ok else "placeholder",
    })

    if cfg.get("_error"):
        telegram_ok = False
        telegram_message = cfg["_error"]
    else:
        telegram_ok = bool(cfg.get("telegram_chat_id"))
        telegram_message = "bound" if telegram_ok else "not discovered"
    checks.append({
        "key": "telegram_chat_id",
        "ok": telegram_ok,
        "message": telegram_message,
    })

    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = check_config(config_path=args.config)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for item in result["checks"]:
            status = "OK" if item["ok"] else "MISSING"
            print(f"{status:7} {item['key']}: {item['message']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
