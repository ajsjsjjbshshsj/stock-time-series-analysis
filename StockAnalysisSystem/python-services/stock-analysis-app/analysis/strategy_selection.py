"""Shared local-calendar and completed-session selection for CLI and dashboard.

No collection, database writes or calendar guessing is permitted here.
"""
from datetime import datetime
import json
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def load_strategy_calendar(calendar):
    """Read an explicit trusted calendar; never fall back to weekdays/network."""
    from analysis.strategy_dates import validate_session_calendar
    if calendar is None:
        raise ValueError('A trusted --calendar is required for strategy validation')
    if not isinstance(calendar, dict):
        with open(calendar, encoding='utf-8') as stream:
            calendar = json.load(stream)
    validate_session_calendar(calendar)
    return calendar


def select_strategy_snapshot(frame, calendar, request_time=None, *, stock_codes=None,
                             required_columns=('open', 'high', 'low', 'close', 'vol', 'amount')):
    from analysis.strategy_dates import select_data_cutoff, SHANGHAI
    from analysis.ranking_accounting import validate_ranking_history
    calendar = load_strategy_calendar(calendar)
    if frame.empty or 'ts_code' not in frame:
        raise ValueError('Dated stock pool required')
    expected = stock_codes if stock_codes is not None else frame.attrs.get('requested_codes')
    if not expected:
        raise ValueError('Independent requested stock pool evidence required')
    selected = select_data_cutoff(calendar, request_time or datetime.now(SHANGHAI), frame,
                                  expected, required_columns=required_columns)
    result = frame[pd.to_datetime(frame.trade_date) <= pd.Timestamp(selected['data_cutoff'])].copy()
    result['trade_date'] = pd.to_datetime(result.trade_date)
    validate_ranking_history(result, calendar)
    logger.info('目标交易日=%s 实际截止=%s 回退原因=%s', selected['target_trade_date'],
                selected['data_cutoff'], selected['fallback_reason'])
    return result, selected
