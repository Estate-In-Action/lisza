#!/usr/bin/env python3
"""
Code a pending receipt with Mistral Pixtral vision.

Usage:
    code_receipt.py <pending_id>     # code one
    code_receipt.py --all            # code every 'new' row
    code_receipt.py --watch          # poll loop, 30s

Outputs JSON like:
{
  "vendor":      "Trader Joe's",
  "date":        "2026-05-04",
  "total":       42.18,
  "currency":    "USD",
  "tax":         3.18,
  "tip":         null,
  "category_hint": "groceries",
  "suggested_account_code": "525",
  "suggested_payment_account_code": "201",
  "line_items":  [{"desc": "Apples", "amount": 3.99}, ...],
  "confidence":  0.82,
  "notes":       "Faded ink on bottom; tax inferred from line items."
}

The structured JSON is stored in pending_inbox.parsed_json (merged with
the raw telegram metadata), and suggested_account / suggested_amount are
populated.  Status moves new -> reviewed.  Posting is a separate step
(see post_pending.py — TODO).
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/workspace/LISZA")
DB = ROOT / "ledger.db"
COA = ROOT / "coa.csv"
LOG = Path("/dev/shm/fin_code_receipt.log")

MISTRAL_KEY = os.environ.get("MISTRAL_AI_API")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MODEL = "pixtral-12b-2409"


def log(msg: str) -> None:
    line = f"{datetime.utcnow().isoformat()}Z  {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_coa() -> list[dict]:
    with COA.open() as f:
        return list(csv.DictReader(f))


def expense_account_menu(coa: list[dict]) -> str:
    """Compact expense+liability+asset listing for the model prompt."""
    rows = [r for r in coa if r["type"] in ("expense", "liability", "asset") and r.get("active", "1") in ("1", "")]
    return "\n".join(f"  {r['code']}  {r['name']}  ({r['type']}, {r['group']})" for r in rows)


def build_prompt(coa: list[dict], caption: str | None) -> str:
    menu = expense_account_menu(coa)
    cap = f"\nUser caption: {caption!r}" if caption else ""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return f"""You are a careful bookkeeper coding a receipt photo for a double-entry ledger.

Today (UTC) is {today}.{cap}

Read the receipt image and return STRICT JSON only — no prose, no markdown
fences. Schema:

{{
  "vendor": str,                       // best-guess merchant name
  "date":   "YYYY-MM-DD" | null,       // receipt date if visible, else null
  "total":  number,                    // grand total paid
  "currency": "USD" | "EUR" | ...,
  "tax":    number | null,
  "tip":    number | null,
  "category_hint": str,                // free text e.g. "groceries", "fuel"
  "suggested_account_code": str,       // pick ONE code from the menu below
  "suggested_payment_account_code": str | null,  // CC/cash/checking from menu
  "line_items": [{{"desc": str, "amount": number}}, ...],
  "confidence": number,                // 0.0 - 1.0 your overall confidence
  "notes": str                         // anything ambiguous; empty string OK
}}

Rules:
- The expense account is normally a 5xx code. Credit card payments are 201/202.
  Cash is 101. Checking is 102.
- If you cannot read the total, set total to 0 and confidence < 0.3 and explain
  in notes.
- Do not invent codes. If nothing fits, set suggested_account_code to "595"
  (Misc).

Account menu (code  name  (type, group)):
{menu}
"""


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def call_pixtral(image_path: Path, prompt: str) -> dict:
    if not MISTRAL_KEY:
        raise RuntimeError("MISTRAL_AI_API secret not set")
    suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    data_url = f"data:image/{suffix};base64,{encode_image(image_path)}"
    body = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": data_url},
                ],
            }
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        MISTRAL_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    content = resp["choices"][0]["message"]["content"]
    return json.loads(content)


def code_one(conn: sqlite3.Connection, pid: int, coa: list[dict]) -> bool:
    row = conn.execute(
        "SELECT id, raw_path, parsed_json, status FROM pending_inbox WHERE id = ?",
        (pid,),
    ).fetchone()
    if not row:
        log(f"#{pid} not found")
        return False
    _id, raw_path, parsed_json, status = row
    if status not in ("new",):
        log(f"#{pid} status={status}, skipping")
        return False
    if not raw_path or not Path(raw_path).exists():
        log(f"#{pid} raw_path missing: {raw_path}")
        return False

    raw_meta = {}
    if parsed_json:
        try:
            raw_meta = json.loads(parsed_json)
        except Exception:
            raw_meta = {"_unparsed": parsed_json}

    caption = raw_meta.get("caption")
    prompt = build_prompt(coa, caption)
    try:
        coded = call_pixtral(Path(raw_path), prompt)
    except Exception as e:
        log(f"#{pid} vision error: {e}")
        return False

    merged = {**raw_meta, "coded": coded, "coded_at": datetime.utcnow().isoformat() + "Z"}
    conn.execute(
        """UPDATE pending_inbox
              SET parsed_json = ?,
                  suggested_account = ?,
                  suggested_amount  = ?,
                  status = 'reviewed'
            WHERE id = ?""",
        (
            json.dumps(merged),
            coded.get("suggested_account_code"),
            coded.get("total"),
            pid,
        ),
    )
    conn.commit()
    log(
        f"#{pid} coded vendor={coded.get('vendor')!r} "
        f"total={coded.get('total')} "
        f"acct={coded.get('suggested_account_code')} "
        f"conf={coded.get('confidence')}"
    )
    return True


def code_all_new() -> int:
    coa = load_coa()
    conn = sqlite3.connect(str(DB))
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM pending_inbox WHERE status='new' ORDER BY id"
        ).fetchall()]
        n = 0
        for pid in ids:
            if code_one(conn, pid, coa):
                n += 1
        return n
    finally:
        conn.close()


def watch_loop(interval: int = 30) -> None:
    log(f"watch loop, interval={interval}s")
    while True:
        try:
            n = code_all_new()
            if n:
                log(f"watch: coded {n} receipts")
        except Exception as e:
            log(f"watch error: {e}")
        time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", nargs="?", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--watch", action="store_true")
    args = ap.parse_args()

    if args.watch:
        watch_loop()
        return
    if args.all:
        n = code_all_new()
        print(f"coded {n} receipts")
        return
    if args.pid:
        coa = load_coa()
        conn = sqlite3.connect(str(DB))
        try:
            ok = code_one(conn, args.pid, coa)
        finally:
            conn.close()
        sys.exit(0 if ok else 1)
    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
