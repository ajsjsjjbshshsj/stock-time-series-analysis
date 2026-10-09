"""Trusted sessions, publication boundaries, and every retained historical row."""
from datetime import datetime
import importlib
import pandas as pd
import pytest


def api():
    return importlib.import_module('analysis.transformer_forward_contracts')


def calendar():
    opened={'2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09',
            '2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'}
    return dict(source='tushare.trade_cal',exchange='SZSE',start='2026-09-28',end='2026-10-16',
                rows=[dict(cal_date=str(d.date()),is_open=int(str(d.date()) in opened)) for d in pd.date_range('2026-09-28','2026-10-16')])


def at(text):
    return datetime.fromisoformat(text+'+08:00')


def history():
    codes=[f'{i:06}.SZ' for i in range(20)]
    dates=['2026-09-28','2026-09-29','2026-09-30','2026-10-08']
    daily=pd.DataFrame([dict(ts_code=c,trade_date=d,open=10.,high=12.,low=9.,close=11.,vol=100.,amount=1000.) for c in codes for d in dates])
    factors=daily[['ts_code','trade_date']].assign(adj_factor=2.)
    frozen=daily[daily.trade_date<='2026-09-30'].copy()
    old_factors=factors[factors.trade_date<='2026-09-30'].copy()
    metadata=dict(stockid2idx={c:i for i,c in enumerate(codes)},feature_history_start='2026-09-28',
                  stock_history_starts={c:'2026-09-28' for c in codes})
    return frozen,old_factors,daily,factors,metadata


def test_sessions_follow_calendar_not_weekday_guess():
    assert api().session_dates(calendar(),'2026-10-08')==dict(signal_date='2026-10-08',entry_date='2026-10-09',exit_date='2026-10-15')
    assert api().select_signal_date(calendar(),at('2026-10-08T15:59:59'),None,'2026-09-30') is None
    assert api().select_signal_date(calendar(),at('2026-10-08T16:00:00'),None,'2026-09-30')=='2026-10-08'


@pytest.mark.parametrize('time,eligible',[('2026-10-08T16:00:00',True),('2026-10-09T08:59:59',True),('2026-10-09T09:00:00',False)])
def test_publication_uses_strict_completion_deadline(time,eligible):
    result=api().classify_publication('2026-10-08',calendar(),at(time),at('2026-10-05T15:54:01'),'2026-09-30')
    assert result['prospective_eligible'] is eligible
    assert result['status']==('PROSPECTIVE_CANDIDATE' if eligible else 'LATE_DIAGNOSTIC')


@pytest.mark.parametrize('bad',['missing','duplicate','flag','exchange'])
def test_calendar_drift_rejected(bad):
    cal=calendar()
    if bad=='missing': cal['rows'].pop(1)
    elif bad=='duplicate': cal['rows'].append(cal['rows'][0])
    elif bad=='flag': cal['rows'][0]['is_open']=True
    else: cal['exchange']='SSE'
    with pytest.raises(ValueError): api().validate_calendar(cal)


def test_naive_future_unready_or_pre_freeze_signal_is_rejected():
    for requested in ('2026-10-07','2026-10-09','2026-10-08'):
        with pytest.raises(ValueError):
            api().select_signal_date(calendar(),at('2026-10-08T15:00:00'),requested,'2026-09-30')
    with pytest.raises(ValueError):
        api().classify_publication('2026-10-08',calendar(),datetime(2026,10,8,17),at('2026-10-05T15:00:00'),'2026-09-30')
    with pytest.raises(ValueError):
        api().classify_publication('2026-10-08',calendar(),at('2026-10-08T17:00:00'),at('2026-10-08T18:00:00'),'2026-09-30')


def test_history_proof_includes_frozen_prefix_and_new_signal_session():
    frozen,old,daily,factors,metadata=history()
    proof=api().validate_history(frozen,old,daily,factors,calendar(),'2026-10-08',metadata)
    assert proof['dates']==['2026-09-28','2026-09-29','2026-09-30','2026-10-08']
    assert proof['codes']==sorted(metadata['stockid2idx'])
    assert proof['signal_date']=='2026-10-08'


@pytest.mark.parametrize('bad',['middle','start','price','factor','future','mapping','unknown'])
def test_history_loss_or_revision_rejected_even_when_last_window_exists(bad):
    frozen,old,daily,factors,metadata=history()
    if bad in ('middle','start'):
        daily=daily[daily.trade_date!=('2026-09-29' if bad=='middle' else '2026-09-28')]
    elif bad=='price': daily.loc[0,'open']=20.
    elif bad=='factor': factors.loc[0,'adj_factor']=3.
    elif bad=='future': daily=pd.concat([daily,daily[daily.trade_date=='2026-10-08'].assign(trade_date='2026-10-09')])
    elif bad=='mapping': metadata['stockid2idx']['000000.SZ']=1
    else: daily.loc[0,'ts_code']='999999.SZ'
    with pytest.raises(ValueError):
        api().validate_history(frozen,old,daily,factors,calendar(),'2026-10-08',metadata)


def test_fixed_training_factor_basis_and_new_factor_provenance():
    _,_,daily,factors,metadata=history()
    factors.loc[factors.trade_date=='2026-10-08','adj_factor']=4.
    contract=dict(version=1,mode='adjusted',bases={c:2. for c in metadata['stockid2idx']},
                  factor_snapshot_sha256='a'*64,volume_unit='lots',amount_unit='thousand_CNY',
                  vwap_unit='CNY_per_share',origin='fixed_first_observed_factor')
    provenance=dict(training_factor_sha256='a'*64,combined_factor_sha256=api().frame_hash(factors))
    result=api().forward_model_panel(daily,factors,contract,provenance)
    new=result[result.trade_date==pd.Timestamp('2026-10-08')]
    assert new.open.tolist()==[20.]*20
    assert new.vol.tolist()==[100.]*20 and new.amount.tolist()==[1000.]*20
    assert result.attrs['factor_provenance']==provenance
