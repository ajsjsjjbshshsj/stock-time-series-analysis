"""Durable receipts for candidate keys, never a full old-value baseline."""
import re
from app.market_data.repair_contract import canonical
from app.preparation.contracts import identity
from app.repositories.gap_import_repository import validate_targets


def import_gap_candidates(repository, candidates, *, binding, allowed_keys, schema,
                          receipt_store, on_progress):
    if (set(binding) != {'batch_sha256', 'coverage_sha256', 'request_sha256'}
            or any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None for value in binding.values())
            or not set(candidates) <= {'stock_daily', 'stock_daily_basic'}):
        raise ValueError('GAP_IMPORT_BINDING_INVALID')
    candidates = canonical(candidates)
    batches = {}
    for table in sorted(candidates):
        rows = candidates[table]
        validate_targets(table, rows, allowed_keys[table])
        for offset in range(0, len(rows), 500):
            chunk = rows[offset:offset+500]
            batches[identity(dict(table=table, rows=chunk))] = (table, chunk)
    bound = dict(binding, candidates_sha256=identity(candidates),
        allowed_keys_sha256=identity({table: sorted([list(key) for key in keys]) for table, keys in allowed_keys.items()}),
        schema_sha256=identity(schema))
    state = receipt_store.load()
    if state is None:
        state = dict(binding=bound, batches={})
    if state.get('binding') != bound or not isinstance(state.get('batches'), dict):
        raise ValueError('GAP_RECEIPT_BINDING_CHANGED')
    for bid, receipt in state['batches'].items():
        if bid not in batches:
            raise ValueError('GAP_RECEIPT_UNKNOWN_BATCH')
        table, chunk = batches[bid]
        keys = [[row['ts_code'], row['trade_date']] for row in chunk]
        if (set(receipt) != {'table', 'keys', 'inserted', 'already_present', 'conflicts', 'owned_insert_keys'}
                or receipt['table'] != table or receipt['keys'] != keys
                or any(type(receipt[k]) is not int or receipt[k] < 0 for k in ('inserted', 'already_present'))
                or receipt['inserted']+receipt['already_present'] != len(keys)
                or len(receipt['owned_insert_keys']) != receipt['inserted']
                or len({tuple(key) for key in receipt['owned_insert_keys']}) != receipt['inserted']
                or not {tuple(key) for key in receipt['owned_insert_keys']} <= {tuple(key) for key in keys}):
            raise ValueError('GAP_RECEIPT_INVALID')
    receipt_store.save(state)
    for bid, (table, rows) in batches.items():
        if bid not in state['batches']:
            result = repository.insert_batch(table, rows, allowed_keys=allowed_keys[table], schema=schema[table])
            state['batches'][bid] = dict(table=table, keys=[[r['ts_code'], r['trade_date']] for r in rows], **result)
            receipt_store.save(state)
            on_progress(dict(completed_batches=len(state['batches']), total_batches=len(batches)))
    missing, conflicts = [], []
    for bid, (table, rows) in batches.items():
        receipt = state['batches'][bid]
        checked = repository.verify_targets(table, rows, owned_insert_keys=receipt['owned_insert_keys'], schema=schema[table])
        missing.extend(checked['missing'])
        conflicts.extend(receipt['conflicts'])
        conflicts.extend(checked['conflicts'])
    return dict(db_import_complete=not missing and not conflicts, missing=missing, conflicts=conflicts,
        inserted=sum(r['inserted'] for r in state['batches'].values()),
        already_present=sum(r['already_present'] for r in state['batches'].values()))
