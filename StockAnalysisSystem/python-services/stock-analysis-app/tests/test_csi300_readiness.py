from datetime import datetime, timezone, timedelta
import importlib
import numpy as np
import pandas as pd
import pytest

TZ = timezone(timedelta(hours=8))


def data(n=500):
    dates = pd.bdate_range('2024-01-01', periods=n)
    codes = [f'{i+1:06d}.SZ' for i in range(300)]
    universe = dict(schema_version=1, pool_version='csi300_test', codes=codes,
                    stockid2idx={c:i for i,c in enumerate(codes)}, survivorship_bias=True)
    frame = pd.DataFrame({'ts_code': np.repeat(codes, n), 'trade_date': np.tile(dates, 300)})
    for col, value in dict(open=10., high=11., low=9., close=10., vol=100., amount=1000.,
                           turnover_rate=1., pe=10., pe_ttm=10., pb=1., ps=1., total_mv=10000.).items(): frame[col] = value
    basic = pd.DataFrame(dict(ts_code=codes, list_date='2020-01-01'))
    calendar = dict(source='tushare.trade_cal', exchange='SSE', start=str(dates[0].date()), end=str(dates[-1].date()),
                    rows=[dict(cal_date=str(d.date()), is_open=int(d in dates)) for d in pd.date_range(dates[0], dates[-1])])
    return universe, frame, basic, calendar


def module():
    try: return importlib.import_module('analysis.csi300_readiness')
    except ModuleNotFoundError: pytest.fail('CSI300 data readiness diagnostic missing')


def diagnose(universe, frame, basic, calendar, **kwargs):
    end = calendar['end'] if calendar else None
    options = dict(start_date='2024-01-01', end_date=end, selected_stock='000001.SZ',
                   request_time=datetime.fromisoformat(end or '2025-01-01').replace(hour=17, tzinfo=TZ),
                   source_identity=dict(source_mode='strict_database'))
    options.update(kwargs)
    return module().diagnose_readiness(universe, frame, basic, calendar, **options)


def test_good_data_readiness_does_not_claim_a_trained_model():
    pool, frame, basic, calendar = data()
    factors = frame[['ts_code', 'trade_date']].assign(adj_factor=1.)
    report = diagnose(pool, frame, basic, calendar, factors=factors, factor_verified=True)
    assert len(report['stocks']) == 300
    assert all(v['data_ready'] for v in report['strategies'].values())
    assert all(not v['model_ready'] for v in report['strategies'].values())
    assert report['survivorship_bias'] is True


def test_missing_one_stock_or_calendar_day_does_not_shrink_pool():
    pool, frame, basic, calendar = data()
    frame = frame[frame.ts_code != '000002.SZ']
    frame = frame[~((frame.ts_code == '000001.SZ') & (frame.trade_date == pd.Timestamp('2024-05-02')))]
    report = diagnose(pool, frame, basic, calendar)
    assert len(report['stocks']) == 300
    assert report['stocks']['000002.SZ']['daily_missing_count'] == 500
    assert report['stocks']['000001.SZ']['daily_missing_count'] == 1
    assert not report['strategies']['single_xgb']['data_ready']
    assert not report['strategies']['lightgbm']['data_ready']


def test_single_strategy_can_be_ready_while_other_strategies_are_not():
    pool, frame, basic, calendar = data(150)
    report = diagnose(pool, frame, basic, calendar)
    assert report['strategies']['single_xgb']['data_ready']
    assert not report['strategies']['lightgbm']['data_ready']
    assert not report['strategies']['transformer']['data_ready']


def test_listing_boundary_is_not_fake_missing_history():
    pool, frame, basic, calendar = data()
    listing = pd.Timestamp('2024-07-01')
    basic.loc[basic.ts_code == '000001.SZ', 'list_date'] = str(listing.date())
    frame = frame[~((frame.ts_code == '000001.SZ') & (frame.trade_date < listing))]
    report = diagnose(pool, frame, basic, calendar)
    assert report['stocks']['000001.SZ']['daily_missing_count'] == 0
    assert report['stocks']['000001.SZ']['effective_start'] == '2024-07-01'


def test_unverified_factor_and_missing_valuation_are_not_filled():
    pool, frame, basic, calendar = data()
    frame.loc[frame.trade_date == frame.trade_date.max(), 'pe'] = np.nan
    factors = frame[['ts_code', 'trade_date']].assign(adj_factor=1.)
    report = diagnose(pool, frame, basic, calendar, factors=factors, factor_verified=False)
    assert not report['strategies']['lightgbm']['data_ready']
    assert not report['strategies']['transformer']['data_ready']
    assert 'UNVERIFIED_FACTORS' in report['strategies']['transformer']['reasons']


def test_no_calendar_returns_complete_not_ready_without_claiming_latest():
    pool, frame, basic, _ = data(20)
    report = diagnose(pool, frame, basic, None, source_identity=dict(queries=0))
    assert len(report['stocks']) == 300
    assert report['target_trade_date'] is None
    assert report['status'] == 'NOT_READY'
    assert 'CALENDAR_UNAVAILABLE' in report['reasons']


def test_unknown_listing_does_not_use_first_price_as_listing_date():
    pool, frame, basic, calendar = data(150)
    basic.loc[0, 'list_date'] = None
    report = diagnose(pool, frame, basic, calendar)
    assert 'UNKNOWN_LISTING' in report['stocks']['000001.SZ']['reasons']
    assert not report['strategies']['single_xgb']['data_ready']


def test_single_stock_window_is_not_restricted_by_another_members_listing():
    pool, frame, basic, calendar = data()
    basic.loc[basic.ts_code == '000002.SZ', 'list_date'] = '2024-07-01'
    frame = frame[~((frame.ts_code == '000002.SZ') & (frame.trade_date < pd.Timestamp('2024-07-01')))]
    report = diagnose(pool, frame, basic, calendar)
    assert report['strategies']['single_xgb']['common_evaluation_start'] == '2024-03-22'
