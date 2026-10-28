"""Explain missing scores from verified artifacts, never today's mutable quotes."""
from collections import Counter
from datetime import date
import re
from tasks.artifacts import read_json

LABELS={
    'SUSPENDED':'封存证据确认当日停牌',
    'WARMUP':'特征处于预热期，历史观测尚不足',
    'PRE_LISTING':'信号日期早于上市日期',
    'POST_DELISTING':'信号日期已不在上市有效期',
    'OUTSIDE_CODE_PERIOD':'信号日期不在该股票代码有效期',
    'UNVERIFIED':'未封存可逐股核实的原因，待核实',
}


def _day(value):
    if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:
        raise ValueError('Invalid coverage date')
    return value


def read_coverage(folder,selected,day,*,limit=20,after=0):
    """Caller must verify the result seal/binding and requester grant first."""
    _day(day)
    if (not isinstance(selected,list) or not 1<=len(selected)<=10000
            or any(not isinstance(c,str) or re.fullmatch(r'[0-9]{6}\.(SH|SZ)',c) is None for c in selected)
            or selected!=sorted(set(selected)) or type(limit) is not int or not 1<=limit<=100
            or type(after) is not int or not 0<=after<=10000):
        raise ValueError('Invalid coverage selection/page')
    import pyarrow.dataset as ds
    latest=folder/'latest_scores.parquet'
    paths=[latest] if latest.is_file() else sorted((folder/'scores').glob('*.parquet'))
    if not paths:raise ValueError('Missing sealed scores')
    scored=set()
    for batch in ds.dataset([str(p) for p in paths],format='parquet').scanner(columns=['ts_code','trade_date'],batch_size=1024).to_batches():
        for row in batch.to_pylist():
            if str(row['trade_date'])[:10]!=day:continue
            code=row['ts_code']
            if code in scored:raise ValueError('Duplicate signal score')
            scored.add(code)
    reasons={};path=folder/'eligibility.json'
    if path.exists():
        exclusions=read_json(path,max_bytes=8*1024*1024).get('exclusions')
        if not isinstance(exclusions,list) or len(exclusions)>10000:raise ValueError('Invalid sealed eligibility')
        seen=set()
        for row in exclusions:
            if not isinstance(row,dict):raise ValueError('Invalid sealed exclusion')
            if 'ts_code' not in row:
                # Older single-stock models recorded a count, not a dated reason.
                if set(row)=={'excluded_rows'} and type(row['excluded_rows']) is int and row['excluded_rows']>=0:continue
                raise ValueError('Invalid legacy eligibility')
            code=row['ts_code']
            if not isinstance(code,str) or re.fullmatch(r'[0-9]{6}\.(SH|SZ)',code) is None or code in seen:
                raise ValueError('Duplicate/invalid exclusion stock')
            seen.add(code)
            if 'excluded_ranges' not in row:continue  # Old counts cannot prove a date-specific reason.
            ranges=row['excluded_ranges']
            if not isinstance(ranges,dict):raise ValueError('Invalid dated eligibility proof')
            matching=[]
            for reason,spans in ranges.items():
                if not isinstance(reason,str) or not isinstance(spans,list):raise ValueError('Invalid exclusion ranges')
                for span in spans:
                    if not isinstance(span,list) or len(span)!=2:raise ValueError('Invalid exclusion range')
                    start,end=map(_day,span)
                    if start>end:raise ValueError('Reversed exclusion range')
                    if start<=day<=end:matching.append(reason)
            if len(matching)>1:raise ValueError('Contradictory exclusion reasons')
            if matching:
                if code in scored:raise ValueError('Scored stock also excluded')
                reasons[code]=matching[0] if matching[0] in LABELS else 'UNVERIFIED'
    missing=[c for c in selected if c not in scored]
    items=[]
    for code in missing[after:after+limit]:
        reason=reasons.get(code,'UNVERIFIED')
        items.append(dict(ts_code=code,signal_date=day,reason=reason,reason_label=LABELS[reason],
            provenance='sealed_eligibility' if reason!='UNVERIFIED' else 'not_recorded'))
    return dict(signal_date=day,selected_count=len(selected),scored_count=len(selected)-len(missing),missing_count=len(missing),
        reason_counts=dict(Counter(reasons.get(c,'UNVERIFIED') for c in missing)),items=items,
        next_cursor=after+limit if after+limit<len(missing) else None,provenance='sealed_result_only')
