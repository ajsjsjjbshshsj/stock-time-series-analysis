from datetime import date
from unittest.mock import MagicMock, patch


def test_history_backfill_passes_output_to_daily_job():
    output = MagicMock()
    collector = MagicMock(source_name="tushare")

    with (
        patch(
            "app.jobs.history_backfill_job.load_trade_calendar",
            return_value={date(2026, 8, 7)},
        ),
        patch(
            "app.jobs.history_backfill_job.get_trade_days",
            return_value=[date(2026, 8, 7)],
        ),
        patch("app.jobs.history_backfill_job.DailyCollectionJob") as daily_job,
    ):
        daily_job.return_value.execute.return_value = {
            "success": True,
            "skipped": False,
            "record_count": 1,
        }
        from app.jobs.history_backfill_job import HistoryBackfillJob

        HistoryBackfillJob(
            collector,
            MagicMock(),
            MagicMock(),
            output=output,
        ).execute("20260807", "20260807")

    assert daily_job.call_args.kwargs["output"] is output


def test_retry_job_passes_output_to_daily_job():
    output = MagicMock()
    collector = MagicMock(source_name="tushare")
    task_repository = MagicMock()
    task_repository.get_failed_tasks.return_value = [{
        "id": 5,
        "task_type": "daily",
        "business_date": "20260807",
        "source": "tushare",
        "retry_count": 0,
    }]

    with (
        patch(
            "app.jobs.retry_failed_job.load_trade_calendar",
            return_value={date(2026, 8, 7)},
        ),
        patch("app.jobs.retry_failed_job.DailyCollectionJob") as daily_job,
    ):
        daily_job.return_value.execute.return_value = {
            "success": True,
            "skipped": False,
            "record_count": 1,
        }
        from app.jobs.retry_failed_job import RetryFailedJob

        RetryFailedJob(
            collector,
            MagicMock(),
            task_repository,
            output=output,
        ).execute()

    assert daily_job.call_args.kwargs["output"] is output
