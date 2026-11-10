#!/usr/bin/env python3
"""CAP-49 backup: PostgreSQL dump + app archive + checksums + metadata -> S3, verified."""
import hashlib
import json
import logging
import os
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "BackupRecovery"
BACKUP_ROOT = ROOT / "backup"
LOG_DIR = ROOT / "logs"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME","hebhzi_db")
DB_USER = os.getenv("DB_USER", "hebhzi_user")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = "backups"
# pg_dump reads the password from the PGPASSWORD environment variable.

# metadata is uploaded last; _COMPLETE is written only after every upload is verified
ARTIFACTS = ["database.sql", "application.tar.gz", "config.json",
             "checksums.sha256", "backup_metadata.json"]
CHECKSUMMED = ["database.sql", "application.tar.gz", "config.json", "backup_metadata.json"]
EXCLUDED_DIRS = {"target", ".git", ".idea", "node_modules", "__pycache__"}

log = logging.getLogger("backup")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_database(out: Path) -> None:
    # pg_dump uses one consistent snapshot, so no table-locking flag is needed.
    subprocess.run(
        ["pg_dump", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "--no-password", "--format=plain", "--clean", "--if-exists",
         "--no-owner", "--file", str(out)],
        check=True, capture_output=True, text=True)


def read_ids_from_dump(sql_path: Path) -> list:
    """Transaction IDs actually present in the dump (COPY block of the transactions table)."""
    ids, in_copy = [], False
    with open(sql_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("COPY public.transactions "):
                in_copy = True
                continue
            if in_copy:
                if line.startswith("\\."):
                    break
                ids.append(line.split("\t")[1])  # columns: id, transaction_id, ...
    return ids


def dump_is_complete(sql_path: Path) -> bool:
    """pg_dump writes this footer last; a truncated dump will not have it."""
    with open(sql_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 2000))
        return b"PostgreSQL database dump complete" in f.read()


def create_app_archive(out: Path) -> None:
    def keep(info):
        name = Path(info.name)
        if EXCLUDED_DIRS & set(name.parts) or name.name.endswith(".env"):
            return None
        return info
    with tarfile.open(out, "w:gz") as tar:
        tar.add(APP_DIR, arcname="application", filter=keep)


def write_config(out: Path) -> None:
    # NON-secret settings only. Passwords and AWS keys never go in here.
    config = {"db_engine": "postgresql", "db_host": DB_HOST, "db_port": DB_PORT,
              "db_name": DB_NAME, "db_user": DB_USER, "app_port": 8080,
              "health_url": "http://localhost:8080/actuator/health"}
    out.write_text(json.dumps(config, indent=2))


def write_metadata(out_dir: Path, backup_id: str, started: str, ids: list) -> None:
    version = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True).stdout.strip()
    meta = {
        "backup_id": backup_id,
        "backup_started_at_utc": started,  # snapshot time; used as the RPO reference point
        "database": {"engine": "postgresql", "name": DB_NAME,
                     "record_count": len(ids), "transaction_ids": ids},
        "artifact_sizes_bytes": {n: (out_dir / n).stat().st_size
                                 for n in ["database.sql", "application.tar.gz", "config.json"]},
        "pg_dump_version": version,
    }
    (out_dir / "backup_metadata.json").write_text(json.dumps(meta, indent=2))


def write_checksums(out_dir: Path) -> None:
    # Same format as `sha256sum`, so `sha256sum -c checksums.sha256` also works on Linux.
    lines = [f"{sha256_of(out_dir / n)}  {n}\n" for n in CHECKSUMMED]
    (out_dir / "checksums.sha256").write_text("".join(lines))


def verify_local(out_dir: Path) -> None:
    for name in ARTIFACTS:
        p = out_dir / name
        if not p.exists() or p.stat().st_size == 0:
            raise RuntimeError(f"Local artifact missing or empty: {name}")
    for line in (out_dir / "checksums.sha256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        if sha256_of(out_dir / name) != expected:
            raise RuntimeError(f"Checksum mismatch: {name}")
    if not dump_is_complete(out_dir / "database.sql"):
        raise RuntimeError("database.sql is truncated (pg_dump footer missing)")


def upload_and_verify(out_dir: Path, backup_id: str) -> None:
    import boto3  # imported here so the error is clear if boto3 is not installed
    s3 = boto3.client("s3")
    base = f"{S3_PREFIX}/{backup_id}"
    for name in ARTIFACTS:
        s3.upload_file(str(out_dir / name), S3_BUCKET, f"{base}/{name}")
        log.info("uploaded %s", name)
    for name in ARTIFACTS:  # confirm every object exists in S3 with the right size
        head = s3.head_object(Bucket=S3_BUCKET, Key=f"{base}/{name}")
        if head["ContentLength"] != (out_dir / name).stat().st_size:
            raise RuntimeError(f"S3 size mismatch for {name}")
    s3.put_object(Bucket=S3_BUCKET, Key=f"{base}/_COMPLETE", Body=b"")

def main() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    BACKUP_ROOT.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG_DIR / "backup.log"), logging.StreamHandler()])
    if not S3_BUCKET:
        log.error("S3_BUCKET environment variable is not set")
        return 1
    
    if not APP_DIR.exists():
        log.error("Application folder not found: %s", APP_DIR)
        return 1


    t0 = time.time()
    backup_id = "backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = BACKUP_ROOT / backup_id
    out_dir.mkdir(parents=True)
    try:
        started = utc_now()
        log.info("START %s", backup_id)
        export_database(out_dir / "database.sql")
        ids = read_ids_from_dump(out_dir / "database.sql")
        log.info("database exported: %d transactions in dump", len(ids))
        create_app_archive(out_dir / "application.tar.gz")
        write_config(out_dir / "config.json")
        write_metadata(out_dir, backup_id, started, ids)
        write_checksums(out_dir)
        verify_local(out_dir)
        log.info("local verification passed")
        upload_and_verify(out_dir, backup_id)
        log.info("SUCCESS %s in %.1fs (s3://%s/%s/%s/)", backup_id, time.time() - t0,
                 S3_BUCKET, S3_PREFIX, backup_id)
        return 0
    except subprocess.CalledProcessError as e:
        log.error("FAILED %s: command failed: %s", backup_id, (e.stderr or "").strip())
    except Exception:
        log.exception("FAILED %s", backup_id)
    return 1


if __name__ == "__main__":
    sys.exit(main())