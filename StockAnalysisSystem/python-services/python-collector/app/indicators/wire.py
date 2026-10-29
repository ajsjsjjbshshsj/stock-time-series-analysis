"""Deterministic V2 transport envelopes; no broker or database side effects."""
from datetime import datetime, timezone
import re
import simplejson

from app.events.event_factory import EventFactory
from app.kafka.serializer import KafkaJsonSerializer
from app.models.stock_daily import StockDailyRecord
from .capture import FIELDS
from .outbox import identity, integer
from .snapshot import _date


def require_binding(binding):
    generation = identity(binding['id'])
    integer(binding['partitions'], 1, 256)
    if (binding['input_topic'] != f'stock.indicators.{generation}.raw.v2'
            or binding['output_topic'] != f'stock.indicators.{generation}.daily.v2'):
        raise ValueError('Generation transport binding mismatch')


def _code(value):
    if type(value) is not str or re.fullmatch(r'(?:(?:60|68)[0-9]{4}\.SH|(?:0[0-9]{5}|30[0-9]{4})\.SZ)', value) is None:
        raise ValueError('Invalid indicator stock code')
    return value


def _loads(payload):
    if not isinstance(payload, (str, bytes)) or len(payload) > 1024 * 1024:
        raise ValueError('Invalid indicator payload size')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate indicator JSON field')
            result[key] = value
        return result
    result = simplejson.loads(payload, use_decimal=True, object_pairs_hook=unique)
    if type(result) is not dict:
        raise ValueError('Indicator JSON object required')
    return result


def _encode(generation, operation, revision, code, day, event):
    identity(generation); _code(code)
    integer(revision, 0 if operation in ('SNAPSHOT', 'SEED_END') else 1, 2**63-1)
    value = dict(generation=generation, schemaVersion=2, operation=operation,
                 sourceRevision=revision, tsCode=code, tradeDate=day, event=event)
    KafkaJsonSerializer._validate_json_numbers(value)
    return simplejson.dumps(value, use_decimal=True, allow_nan=False, separators=(',', ':')).encode('utf-8')


def seed_end(generation, code):
    return _encode(generation, 'SEED_END', 0, code, None, None)


def snapshot_envelope(generation, line):
    point = _loads(line)
    if (set(point) != {'schemaVersion', 'generation', 'sourceRevision', 'event'}
            or type(point['schemaVersion']) is not int or point['schemaVersion'] != 2
            or point['generation'] != generation):
        raise ValueError('Snapshot generation/schema mismatch')
    event = point['event']
    if type(event) is not dict or type(event.get('schemaVersion')) is not int or event['schemaVersion'] != 1:
        raise ValueError('Snapshot daily event required')
    day = _date(event.get('tradeDate')).isoformat()
    return _encode(generation, 'SNAPSHOT', point['sourceRevision'], event.get('tsCode'), day, event)


def mutation_envelope(generation, row):
    payload = _loads(row['payload'])
    if set(payload) != set(FIELDS):
        raise ValueError('Captured daily fields mismatch')
    code = _code(row['ts_code'])
    day = _date(row['trade_date'])
    if payload['ts_code'] != code or payload['trade_date'] != day.isoformat():
        raise ValueError('Captured daily identity mismatch')
    operation = row['operation']
    if operation not in ('UPSERT', 'DELETE'):
        raise ValueError('Invalid captured operation')
    captured = integer(row['captured_at'], 0, 253402300799)
    event = None
    if operation == 'UPSERT':
        values = dict(payload, trade_date=day, source='MYSQL_CAPTURE')
        record = EventFactory.create_daily_event(StockDailyRecord(**values),
            trace_id=f'indicator-capture:{row["id"]}',
            ingest_time=datetime.fromtimestamp(captured, tz=timezone.utc))
        event = _loads(KafkaJsonSerializer.serialize(record))
    return _encode(generation, operation, row['id'], code, day.isoformat(), event)


def partition_for(code, partitions):
    """Kafka Utils.toPositive(Utils.murmur2(UTF8(key))) % partitionCount."""
    data = _code(code).encode('utf-8')
    integer(partitions, 1, 256)
    mask = 0xffffffff
    multiplier = 0x5bd1e995
    value = (0x9747b28c ^ len(data)) & mask
    cursor = 0
    while cursor + 4 <= len(data):
        word = int.from_bytes(data[cursor:cursor+4], 'little')
        word = word * multiplier & mask
        word ^= word >> 24
        word = word * multiplier & mask
        value = ((value * multiplier) ^ word) & mask
        cursor += 4
    tail = data[cursor:]
    if tail:
        value ^= int.from_bytes(tail, 'little')
        value = value * multiplier & mask
    value ^= value >> 13
    value = value * multiplier & mask
    value ^= value >> 15
    return (value & 0x7fffffff) % partitions
