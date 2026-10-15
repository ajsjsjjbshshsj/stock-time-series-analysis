"""Bounded read-only frozen transformer signal packets; no training or inference imports."""
import re
from datetime import date as _calendar_date
from pathlib import Path

from fastapi import HTTPException, Request

PACKET_FILES = ('payload.json', 'marker.json', 'receipt.json', 'seal.json')
DEFAULT_SIGNALS_ROOT = Path(__file__).resolve().parents[1] / 'models' / 'transformer'
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _iso_day(value):
    if type(value) is not str or not _DATE_RE.fullmatch(value):
        raise ValueError('Expected ISO date')
    _calendar_date.fromisoformat(value)
    return value


def _discover(root):
    """Map each complete packet date to its store root; ambiguity fails closed."""
    result = {}
    for store_root in sorted(root.glob('forward_signals_*')):
        signals = store_root / 'signals'
        if not store_root.is_dir() or not signals.is_dir():
            continue
        for folder in sorted(signals.iterdir()):
            if not folder.is_dir() or not _DATE_RE.fullmatch(folder.name):
                continue
            if any(not (folder / name).is_file() for name in PACKET_FILES):
                continue
            if result.setdefault(folder.name, store_root) != store_root:
                raise ValueError('Signal date published in more than one store')
    return result


def _build(folder, store_name, signal_date):
    # Lazy import: the store chain pulls pandas/sklearn and must never load torch.
    from analysis.transformer_forward_store import ForwardSignalStore, digest, identity, read

    store_root = folder.parent.parent
    binding = read(store_root / 'binding.json')
    verified = ForwardSignalStore(store_root, binding).verify(signal_date)
    marker = read(folder / 'marker.json')
    payload = read(folder / 'payload.json')
    payload_sha256 = digest(folder / 'payload.json')
    if marker['files'].get('payload.json') != payload_sha256:
        raise ValueError('Payload digest differs from marker evidence')
    codes = list(binding['codes'])
    per_model = sorted(payload['models'], key=lambda m: (m['seed'], m['mode']))
    scores = {code: [] for code in codes}
    for model in per_model:
        rows = {row['ts_code']: row for row in model['scores']}
        for code in codes:
            row = rows[code]
            scores[code].append(dict(seed=model['seed'], mode=model['mode'],
                                     raw=row['raw'],
                                     nonnegative_variance=row['nonnegative_variance']))
    means = {code: dict(
        raw=sum(row['raw'] for row in scores[code]) / len(scores[code]),
        nonnegative_variance=sum(row['nonnegative_variance'] for row in scores[code]) / len(scores[code]),
    ) for code in codes}
    ranks = {policy: {code: position for position, code in
                      enumerate(sorted(codes, key=lambda c: (-means[c][policy], c)), 1)}
             for policy in ('raw', 'nonnegative_variance')}
    consensus = [dict(ts_code=code,
                      mean_raw=means[code]['raw'],
                      mean_nonnegative_variance=means[code]['nonnegative_variance'],
                      rank_raw=ranks['raw'][code],
                      rank_nonnegative_variance=ranks['nonnegative_variance'][code],
                      scores=scores[code]) for code in codes]
    return dict(signal_date=verified['signal_date'],
                status=verified['status'],
                prospective_eligible=verified['prospective_eligible'],
                published_at=verified['published_at'],
                deadline=verified['deadline'],
                entry_date=verified['entry_date'],
                exit_date=verified['exit_date'],
                frozen_at=binding['frozen_at'],
                cutoff=binding['cutoff'],
                store=store_name,
                available_dates=[],
                primary_policy='raw',
                universe=codes,
                models=verified['model_summaries'],
                consensus=consensus,
                integrity=dict(verified=True,
                               marker_sha256=digest(folder / 'marker.json'),
                               receipt_sha256=verified['receipt_sha256'],
                               binding_sha256=identity(binding),
                               payload_sha256=payload_sha256))


def register_transformer_signal_routes(app, signals_root=None):
    # Runtime import reuses V0.7 request semantics without an initialization cycle.
    from api.forecast import _query, _validate

    root = Path(signals_root) if signals_root is not None else DEFAULT_SIGNALS_ROOT

    @app.get('/api/analysis/transformer/signals')
    def transformer_signals(request: Request, date: str = None):
        _query(request, {'date'})
        if date is not None:
            _validate(_iso_day, date)
        try:
            if not root.is_dir():
                raise LookupError('Signals root unavailable')
            found = _discover(root)
            if not found:
                raise LookupError('No sealed signal packet')
        except LookupError:
            raise HTTPException(404, 'Resource not found') from None
        except Exception:
            raise HTTPException(503, 'Transformer signal dependency unavailable') from None
        dates = sorted(found)
        signal_date = dates[-1] if date is None else date
        if signal_date not in found:
            raise HTTPException(404, 'Resource not found')
        try:
            # Packet mtimes cannot attest to binding or evidence integrity, and
            # may be preserved across edits. Verify every response, including
            # repeat requests; never reuse a previously verified response body.
            store_root = found[signal_date]
            body = _build(store_root / 'signals' / signal_date, store_root.name, signal_date)
            return dict(body, available_dates=dates)
        except Exception:
            raise HTTPException(503, 'Transformer signal dependency unavailable') from None
