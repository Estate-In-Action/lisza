#!/usr/bin/env python3
"""One-shot: ensure the house tenant exists in this LISZA_HOME. Idempotent."""
import tenancy

if __name__ == "__main__":
    cid = tenancy.ensure_house()
    print(f"_house ready: client_id={cid}")
    print(f"book: {tenancy.resolve_db('_house')}")
