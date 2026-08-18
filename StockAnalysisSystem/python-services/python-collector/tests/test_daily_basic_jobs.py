from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def _daily_basic_frame(trade_date=date(2026, 8, 14)):
    return pd.DataFrame({
        'ts_code': ['000001.SZ'],
        'trade_date': [trade_date],
        'turnover_rate': [1.25],
        'pe': [6.5],
        'pe_ttm': [6.8],
        'pb': [0.72],
        'ps': [1.15],
        'total_mv': [21000000.25],
    })


def test_daily_basic_failure_updates_only_daily_basic_task():
    from app.jobs.daily_basic_collection_job import DailyBasicCollectionJob

    collector = MagicMock(source_name='tushare')
    collector.collect_daily_basic.side_effect = RuntimeError('provider unavailable')
    market_repo = MagicMock()
    task_repo = MagicMock()
    task_repo.is_task_success.return_value = False
    task_repo.create_task.return_value = 17

    result = DailyBasicCollectionJob(
        collector, market_repo, task_repo
    ).execute('20260814')

    assert result['success'] is False
    task_repo.is_task_success.assert_called_once_with(
        'daily_basic', '20260814', 'tushare'
    )
    statuses = [call.args[1] for call in task_repo.update_status.call_args_list]
    assert statuses == ['RUNNING', 'FAILED']
    assert all(call.args[0] == 17 for call in task_repo.update_status.call_args_list)


def test_daily_basic_success_adds_source_and_persists_records():
    from app.jobs.daily_basic_collection_job import DailyBasicCollectionJob

    collector = MagicMock(source_name='tushare')
    collector.collect_daily_basic.return_value = _daily_basic_frame()
    market_repo = MagicMock()
    market_repo.save_daily_basic.return_value = 1
    task_repo = MagicMock()
    task_repo.is_task_success.return_value = False
    task_repo.create_task.return_value = 18

    result = DailyBasicCollectionJob(
        collector, market_repo, task_repo
    ).execute('2026-08-14')

    assert result == {
        'success': True,
        'record_count': 1,
        'skipped': False,
        'error': None,
    }
    records = market_repo.save_daily_basic.call_args.args[0]
    assert records[0]['source'] == 'tushare'
    assert records[0]['trade_date'] == date(2026, 8, 14)
    task_repo.update_status.assert_called_with(18, 'SUCCESS', record_count=1)


def test_backfill_reads_bounded_stock_daily_dates_and_resumes():
    from app.jobs.daily_basic_backfill_job import DailyBasicBackfillJob

    collector = MagicMock(source_name='tushare')
    collector.collect_daily_basic.side_effect = [
        _daily_basic_frame(date(2026, 8, 4)),
        _daily_basic_frame(date(2026, 8, 5)),
    ]
    market_repo = MagicMock()
    market_repo.list_stock_daily_trade_dates.return_value = [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
    ]
    market_repo.save_daily_basic.return_value = 1
    task_repo = MagicMock()
    task_repo.is_task_success.side_effect = [True, False, False]
    task_repo.create_task.side_effect = [24, 25]

    with patch('app.jobs.daily_basic_backfill_job.time.sleep') as sleep:
        result = DailyBasicBackfillJob(
            collector, market_repo, task_repo, request_interval=0.5
        ).execute('20260801', '20260805')

    assert collector.collect_daily_basic.call_args_list[0].args == ('20260804',)
    assert collector.collect_daily_basic.call_args_list[1].args == ('20260805',)
    assert result == {
        'success_count': 2,
        'failed_count': 0,
        'skipped_count': 1,
    }
    market_repo.list_stock_daily_trade_dates.assert_called_once_with(
        '20260801', '20260805'
    )
    sleep.assert_called_once_with(0.5)


def test_cli_parser_accepts_new_market_data_commands():
    from app.main import build_parser

    parser = build_parser()

    daily_basic = parser.parse_args(['daily-basic', '--date', '20260814'])
    history = parser.parse_args([
        'daily-basic-history', '--start', '20260801', '--end', '20260814'
    ])
    constituent = parser.parse_args([
        'constituent', '--type', 'index', '--code', '000300.SH',
        '--date', '20260814',
    ])
    daily_market = parser.parse_args(['daily-market', '--date', '20260814'])

    assert daily_basic.command == 'daily-basic'
    assert history.command == 'daily-basic-history'
    assert constituent.group_type == 'index'
    assert daily_market.command == 'daily-market'


def test_daily_market_runs_both_independent_commands_when_one_fails():
    from app.main import cmd_daily_market

    args = MagicMock(date='20260814')
    with (
        patch('app.main.cmd_daily', return_value=0) as daily,
        patch('app.main.cmd_daily_basic', return_value=1) as daily_basic,
    ):
        exit_code = cmd_daily_market(args)

    assert exit_code == 1
    daily.assert_called_once_with(args)
    daily_basic.assert_called_once_with(args)


def test_task_types_include_independent_market_jobs():
    from app.models.collection_task import TaskType

    assert TaskType.DAILY_BASIC.value == 'daily_basic'
    assert TaskType.CONSTITUENT.value == 'constituent'
