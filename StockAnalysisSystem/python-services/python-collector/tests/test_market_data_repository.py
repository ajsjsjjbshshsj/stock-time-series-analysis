from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock


def _repo():
    from app.repositories.market_data_repository import MarketDataRepository

    session = MagicMock()
    return MarketDataRepository(session), session


def test_save_daily_basic_empty_does_not_execute_sql():
    repo, session = _repo()

    assert repo.save_daily_basic([]) == 0
    session.execute.assert_not_called()


def test_save_daily_basic_batches_with_upsert():
    repo, session = _repo()
    records = [
        {
            'ts_code': f'{index:06d}.SZ',
            'trade_date': date(2026, 8, 14),
            'turnover_rate': Decimal('1.250000'),
            'pe': Decimal('6.500000'),
            'pe_ttm': Decimal('6.800000'),
            'pb': Decimal('0.720000'),
            'ps': Decimal('1.150000'),
            'total_mv': Decimal('21000000.2500'),
            'source': 'tushare',
        }
        for index in range(3)
    ]

    assert repo.save_daily_basic(records, batch_size=2) == 3
    assert session.execute.call_count == 2
    sql = str(session.execute.call_args_list[0].args[0])
    assert 'ON DUPLICATE KEY UPDATE' in sql
    assert 'turnover_rate = VALUES(turnover_rate)' in sql


def test_save_constituents_uses_snapshot_business_key_upsert():
    repo, session = _repo()
    records = [{
        'group_type': 'index',
        'group_code': '000300.SH',
        'ts_code': '000001.SZ',
        'as_of_date': date(2026, 8, 14),
        'weight': Decimal('0.500000'),
        'source': 'tushare',
    }]

    assert repo.save_constituents(records) == 1
    sql = str(session.execute.call_args.args[0])
    assert 'ON DUPLICATE KEY UPDATE' in sql
    assert 'weight = VALUES(weight)' in sql


def test_replace_constituents_deletes_old_snapshot_members_first():
    repo, session = _repo()
    records = [{
        'group_type': 'index',
        'group_code': '000300.SH',
        'ts_code': '000001.SZ',
        'as_of_date': date(2026, 8, 14),
        'weight': Decimal('0.500000'),
        'source': 'tushare',
    }]

    assert repo.replace_constituents(records) == 1
    delete_sql = str(session.execute.call_args_list[0].args[0])
    assert delete_sql.startswith('DELETE FROM `stock_constituent`')
    assert session.execute.call_args_list[0].args[1] == {
        'group_type': 'index',
        'group_code': '000300.SH',
        'as_of_date': date(2026, 8, 14),
    }


def test_list_stock_daily_trade_dates_returns_sorted_dates():
    repo, session = _repo()
    session.execute.return_value = [
        (date(2026, 8, 13),),
        (date(2026, 8, 14),),
    ]

    result = repo.list_stock_daily_trade_dates('20260813', '20260814')

    assert result == [date(2026, 8, 13), date(2026, 8, 14)]
    _, params = session.execute.call_args.args
    assert params == {
        'start_date': date(2026, 8, 13),
        'end_date': date(2026, 8, 14),
    }


def test_count_daily_basic_by_date_returns_scalar_count():
    repo, session = _repo()
    session.execute.return_value.scalar.return_value = 5540

    assert repo.count_daily_basic_by_date(date(2026, 8, 14)) == 5540
