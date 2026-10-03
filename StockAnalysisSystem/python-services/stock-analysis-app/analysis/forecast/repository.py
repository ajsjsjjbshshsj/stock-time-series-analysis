"""Transactional publication and bounded, validated reads through DatabaseConnector."""
import json
import pandas as pd
from sqlalchemy import text
from .artifacts import ArtifactStore
from .contracts import ANALYSIS_TYPE, validate_date, validate_model_id, validate_payload, validate_stock, parse_payload


class DependencyError(RuntimeError):
    """Sanitized database dependency failure."""


class ForecastRepository:
    def __init__(self, connector, artifact_store=None):
        self.connector = connector
        self.artifact_store = artifact_store or ArtifactStore()

    def load_market(self, ts_code, start, end):
        validate_stock(ts_code)
        validate_date(start)
        validate_date(end)
        if start > end:
            raise ValueError('Invalid date range')
        sql = text('''SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
                     d.vol, d.amount, b.turnover_rate, b.pe_ttm, b.pb
                     FROM stock_daily d LEFT JOIN stock_daily_basic b
                     ON d.ts_code=b.ts_code AND d.trade_date=b.trade_date
                     WHERE d.ts_code=:stock AND d.trade_date>=:start AND d.trade_date<=:end
                     ORDER BY d.trade_date ASC LIMIT 2501''')
        try:
            with self.connector.session_scope() as session:
                rows = session.execute(sql, dict(stock=ts_code, start=start, end=end)).mappings().all()
            result = pd.DataFrame(rows)
        except Exception:
            raise DependencyError('Forecast database unavailable') from None
        if len(result) > 2500:
            raise ValueError('History exceeds 2500 rows')
        return result

    def publish(self, payload):
        validate_payload(payload)
        # Validate files FIRST; append-only predict revisions may only change latest/cutoff.
        _, frozen = self.artifact_store.load(payload['model_id'])
        for key in frozen:
            if key not in ('latest', 'data_cutoff') and frozen[key] != payload.get(key):
                raise ValueError('Publication differs from frozen model')
        if payload['data_cutoff'] < frozen['data_cutoff']:
            raise ValueError('Publication cutoff regressed')
        serialized = json.dumps(payload, allow_nan=False)
        try:
            with self.connector.session_scope() as session:
                session.execute(text('''INSERT INTO analysis_result
                    (ts_code, analysis_date, analysis_type, result, prediction, confidence)
                    VALUES (:stock, :date, :type, :result, :prediction, NULL)'''),
                    dict(stock=payload['ts_code'], date=payload['latest']['signal_date'],
                         type=ANALYSIS_TYPE, result=serialized,
                         prediction=payload['latest']['predicted_return']))
        except Exception:
            raise DependencyError('Forecast database unavailable') from None
        return payload['model_id']

    def _records(self, ts_code=None, limit=100, model_id=None):
        where = 'analysis_type=:type'
        params = dict(type=ANALYSIS_TYPE)
        if ts_code is not None:
            validate_stock(ts_code)
            where += ' AND ts_code=:stock'
            params['stock'] = ts_code
        params['limit'] = limit
        if model_id is not None:
            # Restrict before LIMIT so an old immutable ID remains discoverable.
            where += ' AND result LIKE :model_match'
            params['model_match'] = '%'+model_id+'%'
        try:
            with self.connector.session_scope() as session:
                rows = session.execute(text('SELECT ts_code, result FROM analysis_result WHERE '+where+
                    ' ORDER BY id DESC LIMIT :limit'), params).mappings().all()
        except Exception:
            raise DependencyError('Forecast database unavailable') from None
        valid = []
        for row in rows:
            try:
                payload = parse_payload(row['result'])
                if payload['ts_code'] == row['ts_code']:
                    valid.append(payload)
                elif model_id is not None:
                    raise DependencyError('Forecast publication unavailable')
            except (ValueError, TypeError):
                if model_id is not None:
                    raise DependencyError('Forecast publication unavailable') from None
                continue
        return valid

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Limit must be 1..100')
        return limit

    def models(self, ts_code, limit=20):
        self._limit(limit)
        result, ids = [], set()
        for payload in self._records(ts_code, 100):
            if payload['model_id'] not in ids:
                ids.add(payload['model_id'])
                result.append({k: v for k, v in payload.items() if k not in ('test_series', 'latest', 'feature_importance')})
            if len(result) == limit:
                break
        return result

    def get(self, ts_code, model_id):
        validate_stock(ts_code)
        validate_model_id(model_id)
        return next((p for p in self._records(ts_code, 100, model_id) if p['model_id'] == model_id), None)

    def get_model(self, model_id):
        validate_model_id(model_id)
        return next((p for p in self._records(limit=100, model_id=model_id) if p['model_id'] == model_id), None)

    def results(self, ts_code, limit=20):
        return [{k: p[k] for k in ('model_id', 'ts_code', 'data_cutoff', 'target', 'horizon', 'metrics', 'latest')}
                for p in self._records(ts_code, self._limit(limit))]

    def health(self):
        try:
            with self.connector.session_scope() as session:
                session.execute(text('SELECT 1'))
        except Exception:
            raise DependencyError('Forecast database unavailable') from None
        return True
