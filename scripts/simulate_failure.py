#!/usr/bin/env python3
"""BackupRecovery failure simulation: record ground truth + failure time, then destroy the transactions table.

Run ONLY against the test database. Stop the Spring Boot app first.
"""
import argparse
import sys
import urllib.error
import urllib.request

import common as c

log = c.setup_logging("simulate_failure")


def app_is_running() -> bool:
    try:
        urllib.request.urlopen(f"{c.APP_URL}/actuator/health", timeout=3)
        return True
    except urllib.error.HTTPError:
        return True          # app answered (e.g. 503 DOWN) -> it is still running
    except Exception:
        return False         # connection refused / timeout -> app is stopped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    args = ap.parse_args()

    if app_is_running():
        log.error("The application is still running at %s. Stop it first (IntelliJ red square).", c.APP_URL)
        return 1
    try:
        if not c.psql_query("SELECT to_regclass('public.transactions')"):
            log.error("Table 'transactions' does not exist in %s - nothing to destroy.", c.DB_NAME)
            return 1
        # Ground truth BEFORE the damage: used later to measure exactly which records were lost.
        ids = c.psql_query("SELECT transaction_id FROM transactions ORDER BY transaction_id").splitlines()
    except Exception as e:
        log.error("Cannot read database: %s", getattr(e, "stderr", e))
        return 1

    print(f"\nTarget database : {c.DB_NAME} on {c.DB_HOST}:{c.DB_PORT}")
    print(f"Records present : {len(ids)}")
    print("This will DROP TABLE transactions (data and structure).")
    if not args.yes and input(f"Type the database name ({c.DB_NAME}) to continue: ").strip() != c.DB_NAME:
        log.error("Confirmation failed - nothing was changed.")
        return 1

    failure = c.utc_now()                       # failure start timestamp
    c.psql_execute("DROP TABLE transactions")   # the simulated data loss
    still_there = c.psql_query("SELECT to_regclass('public.transactions')")
    if still_there:
        log.error("Table still exists - failure was not simulated.")
        return 1

    run_id = "drill_" + failure.strftime("%Y%m%d_%H%M%S")
    c.reset_state(run_id=run_id, failure_at_utc=c.iso(failure),
                  ids_at_failure=ids, count_at_failure=len(ids))
    log.info("FAILURE SIMULATED run=%s at %s UTC; %d records destroyed", run_id, c.iso(failure), len(ids))
    print("\nNext: python scripts/restore.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())