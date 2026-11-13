#!/usr/bin/env python3
"""CAP-49 verify: wait for app health, compare transaction IDs, compute RPO/RTO, write the report row.

Run right after restore.py once the application has been started. Do not create new
transactions between starting the app and running this script.
"""
import argparse
import json
import sys
import time
import urllib.request

import common as c

log = c.setup_logging("verify")


def get_json(path: str):
    with urllib.request.urlopen(f"{c.APP_URL}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def wait_for_health(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if get_json("/actuator/health").get("status") == "UP":
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=180, help="seconds to wait for the app to become healthy")
    args = ap.parse_args()

    st = c.load_state()
    if not st.get("backup_id") or not st.get("recovery_start_utc"):
        log.error("No completed restore in drill state. Run restore.py first.")
        return 1
    base = {"run_id": st["run_id"], "backup_id": st["backup_id"],
            "failure_at_utc": st["failure_at_utc"], "backup_started_at_utc": st["backup_started_at_utc"],
            "recovery_start_utc": st["recovery_start_utc"],
            "records_at_failure": st["count_at_failure"], "records_in_backup": len(st["ids_in_backup"])}

    log.info("waiting up to %ds for %s/actuator/health ...", args.timeout, c.APP_URL)
    if not wait_for_health(args.timeout):
        log.error("Application did not become healthy.")
        c.append_report({**base, "status": "FAILED_APP_NOT_HEALTHY", "notes": "health endpoint never returned UP"})
        return 1
    log.info("health check: UP")

    # Integrity checks use what the *application* reports, so they prove the app can see the data.
    api_ids = set(get_json("/api/transactions/ids"))
    api_count = get_json("/api/transactions/count")["count"]
    backup_ids = set(st["ids_in_backup"])
    problems = []
    if api_ids != backup_ids:
        problems.append(f"ID mismatch: missing {sorted(backup_ids - api_ids)}, unexpected {sorted(api_ids - backup_ids)}")
    if api_count != len(backup_ids):
        problems.append(f"count mismatch: app={api_count}, backup={len(backup_ids)}")

    complete = c.utc_now()                    # recovery is complete only after health + integrity pass
    if problems:
        for p in problems:
            log.error(p)
        c.append_report({**base, "recovery_complete_utc": c.iso(complete), "records_restored": len(api_ids),
                         "status": "FAILED_INTEGRITY", "notes": "; ".join(problems)})
        return 1
    c.save_state(recovery_complete_utc=c.iso(complete))

    failure = c.parse_iso(st["failure_at_utc"])
    backup_t = c.parse_iso(st["backup_started_at_utc"])
    start = c.parse_iso(st["recovery_start_utc"])
    rpo = int((failure - backup_t).total_seconds())
    rto = int((complete - failure).total_seconds())              # downtime seen by users
    duration = int((complete - start).total_seconds())           # operator-start to complete
    lost = sorted(set(st["ids_at_failure"]) - api_ids)
    extra = sorted(api_ids - set(st["ids_at_failure"]))
    notes = "" if not extra else f"unexpected IDs after restore: {extra}"

    c.append_report({**base, "recovery_complete_utc": c.iso(complete), "status": "SUCCESS",
                     "rpo_seconds": rpo, "rpo_target_met": "YES" if rpo <= c.TARGET_RPO_SECONDS else "NO",
                     "rto_seconds_failure_to_complete": rto,
                     "rto_target_met": "YES" if rto <= c.TARGET_RTO_SECONDS else "NO",
                     "recovery_duration_seconds_start_to_complete": duration,
                     "records_restored": len(api_ids), "records_lost": len(lost),
                     "lost_transaction_ids": " ".join(lost), "notes": notes})

    print("\n========== RECOVERY RESULT ==========")
    print(f"Backup used            : {st['backup_id']}")
    print(f"Records at failure     : {st['count_at_failure']}")
    print(f"Records restored       : {len(api_ids)}")
    print(f"Records lost           : {len(lost)}  {lost}")
    print(f"RPO (backup -> failure): {rpo}s   target {c.TARGET_RPO_SECONDS}s -> {'MET' if rpo <= c.TARGET_RPO_SECONDS else 'MISSED'}")
    print(f"RTO (failure -> done)  : {rto}s   target {c.TARGET_RTO_SECONDS}s -> {'MET' if rto <= c.TARGET_RTO_SECONDS else 'MISSED'}")
    print(f"Restore start -> done  : {duration}s")
    print(f"Report appended to     : {c.REPORT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())