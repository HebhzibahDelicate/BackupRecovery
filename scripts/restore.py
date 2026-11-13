#!/usr/bin/env python3
"""CAP-49 restore: choose backup in S3 -> preflight -> download -> verify SHA-256 -> restore files + database.

Nothing in the database is touched until the backup has passed every check.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tarfile

import common as c

log = c.setup_logging("restore")
WORK_DIR = c.ROOT / "restore_work"
OUTPUT_DIR = c.ROOT / "restore_output"


class BackupError(Exception):
    pass


class MissingArtifactError(BackupError):
    def __init__(self, backup_id, missing):
        super().__init__(f"{backup_id} is missing: {', '.join(missing)}")
        self.backup_id, self.missing = backup_id, missing


def select_backup(s3, requested):
    ids = c.list_backup_ids(s3)
    if requested:
        if requested not in ids:
            raise BackupError(f"Backup {requested} not found in S3")
        return requested
    for bid in reversed(ids):                      # newest first
        if "_COMPLETE" in c.list_backup_files(s3, bid):
            return bid
    raise BackupError("No complete backup found in S3")


def older_complete_backups(s3, backup_id):
    return [b for b in reversed(c.list_backup_ids(s3))
            if b < backup_id and "_COMPLETE" in c.list_backup_files(s3, b)]


def preflight(s3, backup_id):
    present = c.list_backup_files(s3, backup_id)
    missing = [a for a in c.REQUIRED_ARTIFACTS if a not in present]
    if missing:
        raise MissingArtifactError(backup_id, missing)


def download(s3, backup_id):
    work = WORK_DIR / backup_id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for name in c.DOWNLOAD_ARTIFACTS:
        s3.download_file(c.S3_BUCKET, f"{c.S3_PREFIX}/{backup_id}/{name}", str(work / name))
    return work


def verify_checksums(work):
    listed = {}
    for line in (work / "checksums.sha256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        listed[name] = expected
    for needed in ("database.sql", "application.tar.gz", "config.json", "backup_metadata.json"):
        if needed not in listed:
            raise BackupError(f"checksums.sha256 does not cover {needed}")
    for name, expected in listed.items():
        if c.sha256_of(work / name) != expected:
            raise BackupError(f"SHA-256 mismatch for {name} - backup is corrupted or modified")
    if not c.dump_is_complete(work / "database.sql"):
        raise BackupError("database.sql is truncated (pg_dump footer missing)")


def restore_app_files(work, backup_id):
    dest = OUTPUT_DIR / backup_id
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(work / "application.tar.gz") as tar:
        try:
            tar.extractall(dest, filter="data")      # blocks path-traversal members
        except TypeError:                            # older Python without extraction filters
            tar.extractall(dest)
    if not (dest / "application" / "pom.xml").exists():
        raise BackupError("Restored application folder has no pom.xml")
    shutil.copy(work / "config.json", dest / "config.json")
    return dest


def restore_database(work, expected_count):
    c.psql_restore_file(work / "database.sql")
    actual = int(c.psql_query("SELECT count(*) FROM transactions"))
    if actual != expected_count:
        raise BackupError(f"Row count after restore is {actual}, backup metadata says {expected_count}")
    return actual


def failure_row(state, backup_id, status, notes):
    return {"run_id": state.get("run_id"), "backup_id": backup_id, "status": status,
            "failure_at_utc": state.get("failure_at_utc"),
            "recovery_start_utc": state.get("recovery_start_utc"),
            "records_at_failure": state.get("count_at_failure"), "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup-id", help="restore this backup instead of the newest complete one")
    args = ap.parse_args()

    if not c.S3_BUCKET:
        log.error("S3_BUCKET environment variable is not set")
        return 1
    state = c.load_state()
    if "failure_at_utc" not in state:
        log.error("No failure recorded. Run simulate_failure.py first.")
        return 1

    recovery_start = c.utc_now()
    state = c.save_state(recovery_start_utc=c.iso(recovery_start), backup_id=None,
                         recovery_complete_utc=None)
    log.info("RECOVERY START %s UTC", c.iso(recovery_start))
    backup_id = args.backup_id
    fail_note = "see logs/restore.log"
    try:
        s3 = c.get_s3()
        backup_id = select_backup(s3, args.backup_id)
        log.info("selected backup: %s", backup_id)

        preflight(s3, backup_id)                     # all artifacts present? (before any download/restore)
        log.info("preflight OK: all %d artifacts present", len(c.REQUIRED_ARTIFACTS))
        work = download(s3, backup_id)
        verify_checksums(work)
        log.info("SHA-256 verification passed")

        meta = json.loads((work / "backup_metadata.json").read_text())
        out = restore_app_files(work, backup_id)
        log.info("application files + config restored to %s", out)
        count = restore_database(work, meta["database"]["record_count"])
        log.info("database restored: %d rows", count)

        c.save_state(backup_id=backup_id,
                     backup_started_at_utc=meta["backup_started_at_utc"],
                     ids_in_backup=meta["database"]["transaction_ids"],
                     db_restored_at_utc=c.iso(c.utc_now()),
                     app_restore_dir=str(out / "application"))
        print("\nRestore finished. NEXT: start the application, then run: python scripts/verify.py")
        print(f"  Restored app folder: {out / 'application'}")
        return 0

    except MissingArtifactError as e:
        log.error("RESTORE ABORTED - incomplete backup: %s", e)
        log.error("The database was NOT modified. Complete recovery cannot be demonstrated with %s.", e.backup_id)
        older = older_complete_backups(s3, e.backup_id)
        if older:
            log.info("Older complete backup available: python scripts/restore.py --backup-id %s", older[0])
        c.append_report(failure_row(state, e.backup_id, "FAILED_MISSING_ARTIFACT", f"missing: {', '.join(e.missing)}"))
        return 2
    except subprocess.CalledProcessError as e:
        fail_note = (e.stderr or "").strip()[:200]
        log.error("RESTORE FAILED: command error: %s", fail_note)
    except BackupError as e:
        fail_note = str(e)
        log.error("RESTORE FAILED: %s", e)
    except Exception as e:
        fail_note = f"{type(e).__name__}: {e}"
        log.exception("RESTORE FAILED")
    c.append_report(failure_row(state, backup_id, "FAILED_RESTORE", fail_note))
    return 1


if __name__ == "__main__":
    sys.exit(main())