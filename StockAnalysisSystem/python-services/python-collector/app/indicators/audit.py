"""Independent bounded Decimal audit of a sealed stock projection.

This checks values and logical coverage, not activation authority. The caller
must also verify the manifest, broker/ingestion receipts and checkpoint/tail
barrier. Only twenty genuine observations are retained; missing trading dates
are never filled with synthetic prices.
"""
from collections import deque
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from itertools import zip_longest

from .outbox import identity, integer
from .snapshot import _date
from .wire import _code

SCALE = Decimal('0.000001')


def _decimal(value):
    if type(value) not in (Decimal, int, str):
        raise ValueError('Exact decimal indicator value required')
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError('Finite indicator value required')
        return result
    except InvalidOperation as error:
        raise ValueError('Invalid indicator decimal') from error


def audit_stock(generation, code, source, actual):
    """Compare ordered snapshot points with CH FINAL rows, without truncation.

    Both inputs are iterables, not materialized panels. An extra, missing,
    duplicate, reordered, wrong-generation or stale-revision row fails closed.
    Decimal arithmetic deliberately mirrors the public six-place HALF_UP
    contract independently of the Java producer.
    """
    identity(generation); _code(code)
    window = deque(maxlen=20)
    missing = object()
    count = 0
    first = last = None
    try:
        with localcontext() as context:
            context.prec = 80
            for point, row in zip_longest(source, actual, fillvalue=missing):
                if point is missing or row is missing:
                    raise ValueError('Indicator logical coverage mismatch')
                event = point['event']
                day = _date(event['tradeDate']).isoformat()
                if (point['generation'] != generation or event['tsCode'] != code
                        or row['generation'] != generation or row['ts_code'] != code
                        or str(row['trade_date']) != day or (last and day <= last)):
                    raise ValueError('Indicator identity/order mismatch')
                revision = integer(point['sourceRevision'], 0, 2**63-1)
                if integer(row['source_revision'], 0, 2**63-1) != revision:
                    raise ValueError('Indicator source revision mismatch')
                close, volume = _decimal(event['close']), _decimal(event['volume'])
                window.append((close, volume))
                def average(period, index):
                    if len(window) < period:
                        return None
                    return (sum(p[index] for p in list(window)[-period:]) / period).quantize(SCALE, rounding=ROUND_HALF_UP)
                volume_average = average(5, 1)
                expected = dict(close=close.quantize(SCALE, rounding=ROUND_HALF_UP),
                    volume=volume.quantize(SCALE, rounding=ROUND_HALF_UP),
                    pct_chg=None if event['pctChg'] is None else _decimal(event['pctChg']).quantize(SCALE, rounding=ROUND_HALF_UP),
                    ma5=average(5, 0), ma10=average(10, 0), ma20=average(20, 0),
                    vol_ma5=volume_average, vol_ma10=average(10, 1),
                    volume_ratio=None if volume_average is None or volume_average == 0 else (volume / volume_average).quantize(SCALE, rounding=ROUND_HALF_UP))
                for field, value in expected.items():
                    observed = row[field]
                    if (value is None and observed is not None) or (value is not None and (observed is None or _decimal(observed) != value)):
                        raise ValueError('Indicator value mismatch: ' + field)
                if integer(row['window_size'], 1, 20) != len(window):
                    raise ValueError('Indicator observation window mismatch')
                warmup = row['is_warmup']
                if type(warmup) not in (int, bool) or warmup not in (0, 1) or bool(warmup) != (len(window) < 20):
                    raise ValueError('Indicator warmup mismatch')
                count += 1
                first = first or day
                last = day
        if not count:
            raise ValueError('Empty stock projection cannot prove coverage')
        return dict(generation=generation, code=code, rows=count, first=first, last=last)
    except (KeyError, TypeError, InvalidOperation) as error:
        raise ValueError('Malformed indicator audit input') from error
