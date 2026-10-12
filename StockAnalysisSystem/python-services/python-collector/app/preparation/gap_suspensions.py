"""Read only relevant, sealed single-day suspension proofs; never infer from emptiness."""
from collections import defaultdict


def cached_no_quote(store, keys, *, references=None):
    keys=set(keys)
    if not keys:return {}
    codes=sorted({code for code,day in keys})
    start,end=min(day for code,day in keys),max(day for code,day in keys)
    if references is None:
        references=[]
        for offset in range(0,len(codes),500):
            references.extend(store.repository.list('suspend_d',codes[offset:offset+500],start,end))
    references=[ref for ref in references if ref['kind']=='suspend_d' and ref['start_date']==ref['end_date']
        and (ref['ts_code'],ref['start_date']) in keys]
    if not references:return {}
    evidence=store.load(references,codes=codes,start=start,end=end)
    events=defaultdict(list)
    for row in evidence['suspend_d']:
        events[(row['ts_code'],row['trade_date'])].append(row)
    queried=set(evidence['queried_suspensions'])
    return {key:dict(source_kind='registry',references=[ref for ref in references
            if (ref['ts_code'],ref['start_date'])==key])
        for key,rows in events.items() if key in keys and key in queried
        and rows and all(row['suspend_type']=='S' and row['suspend_timing'] is None for row in rows)}
