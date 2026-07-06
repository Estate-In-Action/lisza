#!/usr/bin/env python3
"""Batch import bank/card statements from a folder into pending_inbox."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import import_statement_pdf
import ingest_txns


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INBOX = ROOT / "data" / "statement_inbox"
DEFAULT_MANIFEST = ROOT / "data" / "statement_import_manifest.json"
SUPPORTED_SUFFIXES = {".csv", ".pdf"}


def utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "sha256": digest,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "files": {}}
    data = json.loads(path.read_text())
    data.setdefault("version", 1)
    data.setdefault("files", {})
    return data


def save_manifest(manifest: dict[str, Any], path: Path = DEFAULT_MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def discover_files(inbox: Path = DEFAULT_INBOX) -> list[Path]:
    inbox.mkdir(parents=True, exist_ok=True)
    return sorted(
        p for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _manifest_key(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def already_processed(manifest: dict[str, Any], path: Path, fp: dict[str, Any]) -> bool:
    record = manifest.get("files", {}).get(_manifest_key(path))
    return bool(record and record.get("status") == "processed" and record.get("fingerprint") == fp)


def process_file(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        result = ingest_txns.ingest_text(
            path.read_text(),
            db_path=ingest_txns.DB_PATH,
            source_file=str(path),
            dry_run=dry_run,
        )
        return {"kind": "csv", "ok": True, **result}
    if suffix == ".pdf":
        rc = import_statement_pdf.import_pdfs([path], dry_run=dry_run)
        return {"kind": "pdf", "ok": rc == 0, "return_code": rc}
    raise ValueError(f"unsupported statement file type: {path.suffix}")


def run_once(
    *,
    inbox: Path = DEFAULT_INBOX,
    manifest_path: Path = DEFAULT_MANIFEST,
    dry_run: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    files = discover_files(inbox)
    summary = {
        "seen": len(files),
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": dry_run,
        "files": [],
    }

    for path in files:
        fp = fingerprint(path)
        key = _manifest_key(path)
        if already_processed(manifest, path, fp):
            summary["skipped"] += 1
            summary["files"].append({"path": key, "status": "skipped"})
            continue

        try:
            result = process_file(path, dry_run=dry_run)
            status = "processed" if result.get("ok") else "failed"
            summary["processed" if result.get("ok") else "failed"] += 1
            summary["files"].append({"path": key, "status": status, "result": result})
            if not dry_run:
                manifest["files"][key] = {
                    "status": status,
                    "fingerprint": fp,
                    "processed_at": utc_now(),
                    "result": result,
                }
        except Exception as exc:
            summary["failed"] += 1
            error = f"{type(exc).__name__}: {exc}"
            summary["files"].append({"path": key, "status": "failed", "error": error})
            if not dry_run:
                manifest["files"][key] = {
                    "status": "failed",
                    "fingerprint": fp,
                    "processed_at": utc_now(),
                    "error": error,
                }

    if not dry_run:
        save_manifest(manifest, manifest_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_once(inbox=args.inbox, manifest_path=args.manifest, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"seen={summary['seen']} processed={summary['processed']} "
            f"skipped={summary['skipped']} failed={summary['failed']} "
            f"dry_run={summary['dry_run']}"
        )
        for item in summary["files"]:
            print(f"  {item['status']}: {item['path']}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
