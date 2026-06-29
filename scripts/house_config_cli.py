#!/usr/bin/env python3
"""Emit the house housekeeping config as JSON (for the /api/lisza house_config mode)."""
import json
import tenancy

if __name__ == "__main__":
    print(json.dumps(tenancy.get_house_config()))
