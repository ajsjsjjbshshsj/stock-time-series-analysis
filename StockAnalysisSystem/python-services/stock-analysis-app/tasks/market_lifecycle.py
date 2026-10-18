"""Bind the collector's verified lifecycles and code periods, never infer from quotes."""
import hashlib
import sys
from .market_evidence import collector_root
from data_processor.feature_store_contract import date_only,validate_stock_code


def _policy():
    root=collector_root()
    if str(root) not in sys.path:sys.path.append(str(root))
    from app.nightly.symbol_periods import period_for
    return period_for


def validate_lifecycle(code,value):
    validate_stock_code(code)
    if not isinstance(value,dict) or set(value)!={'list_date','delist_date','valid_from','valid_until'}:
        raise ValueError('Verified lifecycle required')
    for key,day in value.items():
        if day is None and key!='list_date':continue
        if not isinstance(day,str) or str(date_only(day))!=day:raise ValueError('Invalid lifecycle date')
    if value['delist_date'] is not None and value['delist_date']<value['list_date']:raise ValueError('Invalid lifecycle range')
    if {k:value[k] for k in ('valid_from','valid_until')}!=_policy()(code):raise ValueError('Code validity policy mismatch')
    return value


def inactive_reason(day,lifecycle):
    day=str(day)
    if day<lifecycle['list_date']:return 'PRE_LISTING'
    if lifecycle['delist_date'] is not None and day>=lifecycle['delist_date']:return 'POST_DELISTING'
    if ((lifecycle['valid_from'] is not None and day<lifecycle['valid_from'])
            or (lifecycle['valid_until'] is not None and day>=lifecycle['valid_until'])):return 'OUTSIDE_CODE_PERIOD'
    return None


def bind_lifecycles(root,references,end):
    period_for=_policy()
    from app.nightly.universe import latest_local_reference
    from app.nightly.symbol_periods import reference_rows
    bundle=latest_local_reference(root)
    if bundle is None:raise ValueError('Sealed lifecycle reference required')
    rows={r['ts_code']:r for r in reference_rows(bundle['rows'])};stocks={}
    def iso(value):
        if value is None:return None
        if len(value)==8:value=f'{value[:4]}-{value[4:6]}-{value[6:]}'
        return str(date_only(value))
    for reference in references:
        code=reference['ts_code'];listed=iso(reference['list_date'])
        if listed is None:raise ValueError('Unknown listing boundary')
        row=rows.get(code)
        if row is None:
            if listed<=end:raise ValueError('Unknown in-range lifecycle')
            delisted=None  # Proven pre-listing throughout this bound request.
        else:
            if iso(row['list_date'])!=listed:raise ValueError('Master/lifecycle listing conflict')
            delisted=None if row['delist_date'] in (None,'') else iso(row['delist_date'])
        stocks[code]=validate_lifecycle(code,dict(list_date=listed,delist_date=delisted,**period_for(code)))
    return dict(reference_sha256=bundle['manifest_sha256'],
        policy_sha256=hashlib.sha256((collector_root()/'app/nightly/symbol_periods.py').read_bytes()).hexdigest(),stocks=stocks)
