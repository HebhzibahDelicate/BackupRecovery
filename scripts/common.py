"""Shared helpers for simulate_failure.py, restore.py and verify.py."""
import csv
import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
REPORTS_DIR = ROOT / "reports"
STATE_FILE = REPORTS_DIR / "drill_state.json"
REPORT_CSV = REPORTS_DIR / "recovery-report.csv"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "hebhzi_db")
DB_USER = os.getenv("DB_USER", "hebhzi_user")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = "backups"
APP_URL = os.getenv("APP_URL", "http://localhost:8080")

TARGET_RPO_SECONDS = 5 * 60    # proposed target, not a measured result
TARGET_RTO_SECONDS = 10 * 60   # proposed target, not a measured result

# Everything a backup must contain. _COMPLETE is written by backup.py after S3 upload is verified.
REQUIRED_ARTIFACTS = ["database.sql", "application.tar.gz", "config.json",
                      "checksums.sha256", "backup_metadata.json", "_COMPLETE"]
DOWNLOAD_ARTIFACTS = [a for a in REQUIRED_ARTIFACTS if a != "_COMPLETE"]

REPORT_FIELDS = [
    "run_id", "backup_id", "status", "failure_at_utc", "backup_started_at_utc",
    "recovery_start_utc", "recovery_complete_utc",
    "rpo_seconds", "rpo_target_met",
    "rto_seconds_failure_to_complete", "rto_target_met",
    "recovery_duration_seconds_start_to_complete",
    "records_at_failure", "records_in_backup", "records_restored", "records_lost",
    "lost_transaction_ids", "notes",
]


def setup_logging(name: str) -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG_DIR / f"{name}.log"),
                                  logging.StreamHandler()])
    return logging.getLogger(name)


# ---------- time ----------
def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------- drill state (shared between the three scripts) ----------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(**updates) -> dict:
    REPORTS_DIR.mkdir(exist_ok=True)
    state = load_state()
    state.update(updates)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return state


def reset_state(**fresh) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(fresh, indent=2))


def append_report(row: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    new_file = not REPORT_CSV.exists()
    with open(REPORT_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in REPORT_FIELDS})


# ---------- files ----------
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_is_complete(sql_path: Path) -> bool:
    with open(sql_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 2000))
        return b"PostgreSQL database dump complete" in f.read()


# ---------- PostgreSQL (password comes from the PGPASSWORD environment variable) ----------
def _psql_base() -> list:
    return ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
            "--no-password", "-X", "-v", "ON_ERROR_STOP=1"]


def psql_query(sql: str) -> str:
    r = subprocess.run(_psql_base() + ["-t", "-A", "-c", sql],
                       check=True, capture_output=True, text=True)
    return r.stdout.strip()


def psql_execute(sql: str) -> None:
    subprocess.run(_psql_base() + ["-c", sql], check=True, capture_output=True, text=True)


def psql_restore_file(path: Path) -> None:
    # --single-transaction: either the whole dump is applied or nothing is (no half-restored DB)
    subprocess.run(_psql_base() + ["--single-transaction", "-f", str(path)],
                   check=True, capture_output=True, text=True)


# ---------- S3 ----------
def get_s3():
    import boto3
    return boto3.client("s3")


def list_backup_ids(s3) -> list:
    ids = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            ids.append(cp["Prefix"].rstrip("/").split("/")[-1])
    return sorted(ids)


def list_backup_files(s3, backup_id: str) -> set:
    names = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/{backup_id}/"):
        for obj in page.get("Contents", []):
            names.add(obj["Key"].split("/")[-1])
    return names