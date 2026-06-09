#!/usr/bin/env python3
"""
LISZA Telegram intake — long-polls @fin_t_4me_bot, captures photos
from the LISZA Books group (auto-discovered on first message), and records each
arrival in pending_inbox for downstream coding.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.parse
import urllib.error

ROOT = Path("/home/workspace/LISZA")
DB = ROOT / "ledger.db"
INBOX = ROOT / "inbox" / "telegram"
CONFIG = ROOT / "config.json"
STATE = ROOT / "data" / "telegram_state.json"
LOG = Path("/dev/shm/fin_telegram_intake.log")

BOT_TOKEN = os.environ.get("FIN_T_B4ME") or os.environ.get("fin_t_b4me")
if not BOT_TOKEN:
    sys.stderr.write("FATAL: FIN_T_B4ME secret not set\n")
    sys.exit(1)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"


def log(msg: str) -> None:
    line = f"{datetime.utcnow().isoformat()}Z  {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http_get(url: str, timeout: int = 65) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_download(url: str, dest: Path, timeout: int = 60) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as r, dest.open("wb") as out:
        out.write(r.read())


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"offset": 0}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def load_config() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}


def save_config(c: dict) -> None:
    CONFIG.write_text(json.dumps(c, indent=2))


def discover_chat(cfg: dict, msg: dict) -> dict:
    """If we have not pinned a chat_id yet, learn it from the first group msg."""
    if cfg.get("telegram_chat_id"):
        return cfg
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    if chat_id is None:
        return cfg
    cfg["telegram_chat_id"] = chat_id
    cfg["telegram_chat_title"] = chat.get("title") or chat.get("username") or chat_type
    cfg["telegram_chat_type"] = chat_type
    cfg["discovered_at"] = datetime.utcnow().isoformat() + "Z"
    save_config(cfg)
    log(f"discovered chat_id={chat_id} title={cfg['telegram_chat_title']!r} type={chat_type}")
    return cfg


def send_message(chat_id: int, text: str) -> None:
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"{API}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        log(f"send_message failed: {e}")


def insert_pending(raw_path: str, parsed: dict) -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))
    try:
        cur = conn.execute(
            "INSERT INTO pending_inbox (source, raw_path, parsed_json, status) "
            "VALUES (?, ?, ?, 'new')",
            ("telegram", raw_path, json.dumps(parsed)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def best_photo(photos: list[dict]) -> dict:
    """Telegram returns multiple sizes; we want the largest."""
    return max(photos, key=lambda p: p.get("file_size", 0) or (p.get("width", 0) * p.get("height", 0)))


def download_file(file_id: str, suggested_name: str) -> Path:
    INBOX.mkdir(parents=True, exist_ok=True)
    info = http_get(f"{API}/getFile?file_id={urllib.parse.quote(file_id)}")
    if not info.get("ok"):
        raise RuntimeError(f"getFile failed: {info}")
    file_path = info["result"]["file_path"]
    dest = INBOX / suggested_name
    http_download(f"{FILE_API}/{file_path}", dest)
    return dest


def handle_message(cfg: dict, msg: dict) -> dict:
    cfg = discover_chat(cfg, msg)
    chat_id = msg.get("chat", {}).get("id")
    pinned = cfg.get("telegram_chat_id")
    if pinned is not None and chat_id != pinned:
        log(f"ignoring msg from non-pinned chat_id={chat_id}")
        return cfg

    sender = msg.get("from", {}) or {}
    sender_tag = sender.get("username") or str(sender.get("id") or "unknown")
    msg_id = msg.get("message_id")
    msg_date = datetime.utcfromtimestamp(msg.get("date", time.time())).strftime("%Y%m%d_%H%M%S")

    if "photo" in msg:
        photo = best_photo(msg["photo"])
        file_id = photo["file_id"]
        suggested = f"{msg_date}_{sender_tag}_{msg_id}_{photo.get('file_unique_id','x')}.jpg"
        try:
            dest = download_file(file_id, suggested)
        except Exception as e:
            log(f"download failed file_id={file_id}: {e}")
            return cfg
        meta = {
            "kind": "photo",
            "msg_id": msg_id,
            "from": sender_tag,
            "from_id": sender.get("id"),
            "caption": msg.get("caption"),
            "date_utc": datetime.utcfromtimestamp(msg.get("date", time.time())).isoformat() + "Z",
            "telegram_file_id": file_id,
        }
        pid = insert_pending(str(dest), meta)
        log(f"photo saved id={pid} from={sender_tag} -> {dest.name}")
        send_message(chat_id, f"Got it — receipt #{pid} queued for coding.")
    elif "document" in msg and (msg["document"].get("mime_type", "").startswith("image/")
                                  or msg["document"].get("mime_type") == "application/pdf"):
        doc = msg["document"]
        file_id = doc["file_id"]
        ext = (doc.get("file_name") or "").split(".")[-1] or "bin"
        suggested = f"{msg_date}_{sender_tag}_{msg_id}_{doc.get('file_unique_id','x')}.{ext}"
        try:
            dest = download_file(file_id, suggested)
        except Exception as e:
            log(f"document download failed: {e}")
            return cfg
        meta = {
            "kind": "document",
            "msg_id": msg_id,
            "from": sender_tag,
            "from_id": sender.get("id"),
            "mime": doc.get("mime_type"),
            "caption": msg.get("caption"),
            "date_utc": datetime.utcfromtimestamp(msg.get("date", time.time())).isoformat() + "Z",
            "telegram_file_id": file_id,
        }
        pid = insert_pending(str(dest), meta)
        log(f"document saved id={pid} from={sender_tag} -> {dest.name}")
        send_message(chat_id, f"Got it — document #{pid} queued.")
    else:
        text = (msg.get("text") or "").strip()
        if text and text.lower() in ("/ping", "ping"):
            send_message(chat_id, "Bookkeeper online.")
        elif text and text.lower().startswith("/status"):
            conn = sqlite3.connect(str(DB))
            try:
                n = conn.execute("SELECT COUNT(*) FROM pending_inbox WHERE status='new'").fetchone()[0]
            finally:
                conn.close()
            send_message(chat_id, f"Pending receipts: {n}")
    return cfg


def poll_loop() -> None:
    state = load_state()
    cfg = load_config()
    log(f"starting; offset={state.get('offset',0)} chat_id={cfg.get('telegram_chat_id')}")
    while True:
        try:
            url = f"{API}/getUpdates?timeout=50&offset={state.get('offset', 0)}"
            data = http_get(url, timeout=65)
            if not data.get("ok"):
                log(f"getUpdates not-ok: {data}")
                time.sleep(5)
                continue
            updates = data.get("result", [])
            for upd in updates:
                state["offset"] = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue
                try:
                    cfg = handle_message(cfg, msg)
                except Exception as e:
                    log(f"handler error: {e}")
            if updates:
                save_state(state)
        except urllib.error.URLError as e:
            log(f"network error: {e}")
            time.sleep(5)
        except Exception as e:
            log(f"loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    poll_loop()
