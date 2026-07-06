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

import post_pending

ROOT = Path("/home/workspace/LISZA")
DB = ROOT / "ledger.db"
INBOX = ROOT / "inbox" / "telegram"
CONFIG = ROOT / "config.json"
STATE = ROOT / "data" / "telegram_state.json"
LOG = Path("/dev/shm/fin_telegram_intake.log")

BOT_TOKEN = os.environ.get("FIN_T_B4ME") or os.environ.get("fin_t_b4me")
API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}" if BOT_TOKEN else ""


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


def http_post(method: str, payload: dict, timeout: int = 15) -> dict:
    if not BOT_TOKEN:
        raise RuntimeError("FIN_T_B4ME secret not set")
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
        http_post("sendMessage", {"chat_id": chat_id, "text": text})
    except Exception as e:
        log(f"send_message failed: {e}")


def send_message_with_keyboard(chat_id: int, text: str, keyboard: list[list[dict]]) -> dict | None:
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps({"inline_keyboard": keyboard}),
        }
        resp = http_post("sendMessage", payload)
        return resp.get("result") if resp.get("ok") else None
    except Exception as e:
        log(f"send_message_with_keyboard failed: {e}")
        return None


def edit_message_text(chat_id: int, message_id: int, text: str,
                      keyboard: list[list[dict]] | None = None) -> None:
    try:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if keyboard is not None:
            payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
        http_post("editMessageText", payload)
    except Exception as e:
        log(f"edit_message_text failed: {e}")


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        http_post("answerCallbackQuery", payload)
    except Exception as e:
        log(f"answer_callback_query failed: {e}")


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


def _approve_keyboard(row_id: int) -> list[list[dict]]:
    return [[
        {"text": "Approve", "callback_data": f"approve:{row_id}"},
        {"text": "Reject", "callback_data": f"reject:{row_id}"},
    ]]


def _parse_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _coded(payload: dict) -> dict:
    coded = payload.get("coded")
    return coded if isinstance(coded, dict) else payload


def _review_text(row: sqlite3.Row) -> str:
    payload = _parse_payload(row["parsed_json"])
    coded = _coded(payload)
    vendor = coded.get("vendor") or payload.get("description") or "unknown"
    receipt_date = coded.get("date") or payload.get("txn_date") or "-"
    confidence = coded.get("confidence") or payload.get("confidence") or "-"
    return (
        f"Receipt #{row['id']} ready for review\n"
        f"Payee: {vendor}\n"
        f"Date: {receipt_date}\n"
        f"Amount: {row['suggested_amount'] or '-'}\n"
        f"Account: {row['suggested_account'] or '-'}\n"
        f"Confidence: {confidence}"
    )


def _has_review_marker(notes: str | None) -> bool:
    return bool(notes and "tg_review_message_id=" in notes)


def _append_note(existing: str | None, note: str) -> str:
    current = (existing or "").strip()
    return f"{current}\n{note}".strip() if current else note


def notify_reviewed_rows(cfg: dict, *, limit: int = 10) -> int:
    """Send approve/reject keyboards for reviewed rows that have not been sent."""
    chat_id = cfg.get("telegram_chat_id")
    if not chat_id:
        return 0
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    post_pending.ensure_pending_inbox(conn)
    try:
        rows = conn.execute(
            """SELECT * FROM pending_inbox
               WHERE status='reviewed'
               ORDER BY received_at ASC, id ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        sent = 0
        for row in rows:
            if _has_review_marker(row["notes"]):
                continue
            msg = send_message_with_keyboard(chat_id, _review_text(row), _approve_keyboard(row["id"]))
            if not msg:
                continue
            marker = f"tg_review_message_id={msg.get('message_id')} sent_at={datetime.utcnow().isoformat()}Z"
            conn.execute(
                "UPDATE pending_inbox SET notes=? WHERE id=? AND status='reviewed'",
                (_append_note(row["notes"], marker), row["id"]),
            )
            sent += 1
        conn.commit()
        return sent
    finally:
        conn.close()


def handle_callback(cfg: dict, cq: dict) -> None:
    cq_id = cq.get("id", "")
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    pinned = cfg.get("telegram_chat_id")
    if pinned is not None and chat_id != pinned:
        answer_callback_query(cq_id, "wrong chat")
        return

    data = cq.get("data") or ""
    verb, _, raw_id = data.partition(":")
    try:
        row_id = int(raw_id)
    except ValueError:
        answer_callback_query(cq_id, "bad callback")
        return

    if verb == "approve":
        try:
            entry_id = post_pending.approve_row(row_id, db_path=DB, actor="telegram")
        except Exception as exc:
            answer_callback_query(cq_id, "error")
            if chat_id and message_id:
                edit_message_text(chat_id, message_id, f"Receipt #{row_id}\nERROR: {exc}", keyboard=[])
            return
        if entry_id:
            answer_callback_query(cq_id, "posted")
            if chat_id and message_id:
                edit_message_text(chat_id, message_id, f"Receipt #{row_id} posted as entry #{entry_id}.", keyboard=[])
        else:
            answer_callback_query(cq_id, "already actioned")
            if chat_id and message_id:
                edit_message_text(chat_id, message_id, f"Receipt #{row_id} was already actioned.", keyboard=[])
    elif verb == "reject":
        rejected = post_pending.reject_row(row_id, db_path=DB, reason="telegram reject button", actor="telegram")
        answer_callback_query(cq_id, "rejected" if rejected else "already actioned")
        if chat_id and message_id:
            text = f"Receipt #{row_id} rejected." if rejected else f"Receipt #{row_id} was already actioned."
            edit_message_text(chat_id, message_id, text, keyboard=[])
    else:
        answer_callback_query(cq_id, f"unknown action {verb}")


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
    if not BOT_TOKEN:
        sys.stderr.write("FATAL: FIN_T_B4ME secret not set\n")
        sys.exit(1)
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
                if "callback_query" in upd:
                    try:
                        handle_callback(cfg, upd["callback_query"])
                    except Exception as e:
                        log(f"callback handler error: {e}")
                    continue
                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue
                try:
                    cfg = handle_message(cfg, msg)
                except Exception as e:
                    log(f"handler error: {e}")
            try:
                notified = notify_reviewed_rows(cfg)
                if notified:
                    log(f"sent {notified} review prompt(s)")
            except Exception as e:
                log(f"review notifier error: {e}")
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
