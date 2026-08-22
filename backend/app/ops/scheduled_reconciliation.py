"""Daily reconciliation job (Commercial Billing Operating Standard doc §31
"daily and period-close reconciliation across Zoiko Local, ZoikoNex and each
live provider"). Run standalone, on a schedule external to this process (see
render.yaml's zoiko-local-daily-reconciliation cron service):

    python -m app.ops.scheduled_reconciliation

Not wired into the FastAPI app itself - owns its own DB session, same
pattern as app.events.consumer. Deliberately a thin script: it only calls
existing, already-tested service functions (run_zoikonex_reconciliation,
list_due_renewals, expire_overdue_cases, purge_expired_recordings) and logs
a summary; it contains no reconciliation logic of its own. Exits 1 if this
run found new exceptions, numbers overdue for renewal, or any purge
failures, so cron/host alerting can key off the exit code without parsing
log output.

expire_overdue_cases and purge_expired_recordings both existed with no
scheduler calling them (see their own docstrings) - this is that scheduler.
Nothing new was built for it; this reuses the one daily job slot that
already existed for reconciliation/renewals. expire_overdue_kill_switches
(Commercial Billing Operating Standard doc §U2 "time-bounded overrides")
was added to this same slot when kill switches gained an expires_at.
"""

import logging
import sys

from app.billing.service import run_zoikonex_reconciliation
from app.compliance.service import expire_overdue_cases
from app.core.database import SessionLocal
from app.numbering.numbers.service import list_due_renewals
from app.ops.service import expire_overdue_kill_switches
from app.retention.service import purge_expired_recordings

logger = logging.getLogger("zoiko.ops.reconciliation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> int:
    db = SessionLocal()
    exit_code = 0
    try:
        run = run_zoikonex_reconciliation(db)
        logger.info(
            "zoikonex_reconciliation_run id=%s total_subscriptions=%d unsynced_subscriptions=%d "
            "total_usage_events=%d unsynced_usage_events=%d total_completed_calls=%d "
            "unmatched_completed_calls=%d exceptions_found=%d",
            run.id, run.total_subscriptions, run.unsynced_subscriptions,
            run.total_usage_events, run.unsynced_usage_events, run.total_completed_calls,
            run.unmatched_completed_calls, run.exceptions_found,
        )
        if run.exceptions_found > 0:
            exit_code = 1

        # list_due_renewals is a staff-visible worklist, not an automated
        # charge (there's no real per-number payment gateway yet - see its
        # own docstring) - this job only surfaces the count for visibility,
        # it never renews/charges anything on its own.
        due = list_due_renewals(db)
        logger.info("numbers_due_for_renewal count=%d", len(due))
        if due:
            exit_code = 1

        expired = expire_overdue_cases(db)
        logger.info("compliance_cases_expired count=%d", expired["expired"])

        expired_switches = expire_overdue_kill_switches(db)
        logger.info(
            "kill_switches_expired platform=%d account=%d",
            expired_switches["platform"], expired_switches["account"],
        )

        purged = purge_expired_recordings(db)
        total_failed = sum(bucket["failed"] for bucket in purged.values())
        logger.info(
            "retention_purge voicemail=%s call_recording=%s video_recording=%s",
            purged["voicemail"], purged["call_recording"], purged["video_recording"],
        )
        if total_failed > 0:
            exit_code = 1
    except Exception:
        logger.exception("scheduled_reconciliation run failed")
        exit_code = 1
    finally:
        db.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
