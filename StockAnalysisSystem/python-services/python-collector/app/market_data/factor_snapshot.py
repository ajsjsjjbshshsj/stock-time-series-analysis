"""Serial, resumable real factor snapshots; no database or analysis dependency."""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd


def collect_factor_snapshot(client, market, directory, *, interval=.5, sleep=time.sleep, clock=time.time):
    """Each year/stock is small enough to audit exact expected sessions.

    External quota errors are not hidden: minute quotas cool a full minute,
    hourly quotas stop and persist a retry deadline for subsequent resumes.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    interval = max(.5, float(interval))
    if not np.isfinite(interval):
        raise ValueError('Finite request interval required')
    ledger_path = directory/'acquisition.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {'segments':[]}
    if ledger.get('retry_not_before',0)>clock():
        raise RuntimeError(f"Factor quota cooldown remains {ledger['retry_not_before']-clock():.0f}s")
    panel = market[['ts_code','trade_date']].copy()
    panel['trade_date'] = pd.to_datetime(panel.trade_date.astype(str))
    if panel.empty or panel.isna().any().any() or panel.duplicated().any():
        raise ValueError('Invalid source market identities')
    parts=[]
    last_start=None
    for code, group in panel.groupby('ts_code',sort=True):
        for year, dates in group.groupby(group.trade_date.dt.year):
            expected=set(dates.trade_date)
            first,last=min(expected),max(expected)
            kwargs=dict(ts_code=code,start_date=first.strftime('%Y%m%d'),end_date=last.strftime('%Y%m%d'),
                        fields='ts_code,trade_date,adj_factor')
            shard=directory/f'{code}_{first:%Y%m%d}_{last:%Y%m%d}.parquet'
            cached=shard.exists()
            if cached:
                frame=pd.read_parquet(shard)
            else:
                for attempt in range(3):
                    if last_start is not None:
                        sleep(max(0.,interval-(clock()-last_start)))
                    last_start=clock()
                    try:
                        frame=client.adj_factor(**kwargs)
                        break
                    except Exception as error:
                        message=str(error).lower()
                        ledger['failed']=kwargs
                        quota=any(word in message for word in ('频率','每分钟','每小时','小时','rate limit'))
                        if quota:
                            cooldown=3601 if '小时' in message else 61
                            ledger['retry_not_before']=clock()+cooldown
                        ledger_path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
                        temporary=quota or any(w in message for w in ('timeout','timed out','connection','502','503','504','暂时'))
                        permanent=any(w in message for w in ('token','权限','积分','参数','permission','unauthorized','forbidden'))
                        if permanent or not temporary or '小时' in message or attempt==2:
                            raise
                        delay=61 if quota else 2**(attempt+1)
                        print(f'FACTOR_RETRY stock={code} year={year} cooldown={delay}s',flush=True)
                        sleep(delay)
                else:
                    raise RuntimeError('Factor retries exhausted')
            if frame is None or frame.empty or not {'ts_code','trade_date','adj_factor'}.issubset(frame):
                raise ValueError(f'Factor coverage/schema unavailable for {code}/{year}')
            frame=frame[['ts_code','trade_date','adj_factor']].copy()
            frame['trade_date']=pd.to_datetime(frame.trade_date.astype(str))
            frame['adj_factor']=pd.to_numeric(frame.adj_factor).astype(float)
            if (set(frame.ts_code)!={code} or set(frame.trade_date)!=expected
                    or frame.duplicated(['ts_code','trade_date']).any()
                    or not np.isfinite(frame.adj_factor).all() or (frame.adj_factor<=0).any()):
                raise ValueError(f'Factor coverage/values invalid for {code}/{year}')
            frame=frame.sort_values('trade_date').reset_index(drop=True)
            if not cached:
                frame.to_parquet(shard,index=False)
            record=dict(**kwargs,rows=len(frame),cache_hit=cached,checked_at=clock())
            ledger['segments'].append(record)
            ledger.pop('failed',None)
            ledger.pop('retry_not_before',None)
            ledger_path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
            parts.append(frame)
            print(f'FACTOR_SEGMENT stock={code} year={year} rows={len(frame)} cached={cached}',flush=True)
    return pd.concat(parts,ignore_index=True).sort_values(['ts_code','trade_date']).reset_index(drop=True)
