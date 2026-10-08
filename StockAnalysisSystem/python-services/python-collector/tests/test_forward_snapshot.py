"""Remote fake only; exercise real paced acquisition, validation and cache files."""
import importlib
import json
import pandas as pd
import pytest


def api():
    return importlib.import_module('app.market_data.forward_snapshot')


class Client:
    def __init__(self, bad=None):
        self.bad, self.calls = bad, []
    def trade_calendar(self, **kwargs):
        self.calls.append(('calendar', kwargs))
        frame = pd.DataFrame({'exchange': ['SZSE']*3, 'cal_date': ['20261008','20261009','20261010'], 'is_open':[1,1,0]})
        if self.bad == 'calendar_missing':
            frame = frame.iloc[:2]
        return frame
    def daily(self, **kwargs):
        self.calls.append(('daily', kwargs))
        if self.bad in ('minute', 'hour', 'permission', 'network'):
            messages = {'minute':'每分钟访问频率超限 secret123', 'hour':'每小时最多访问1次 secret123',
                        'permission':'token无效 secret123', 'network':'connection timeout secret123'}
            raise RuntimeError(messages[self.bad])
        frame = pd.DataFrame({'ts_code':[kwargs['ts_code']]*2, 'trade_date':['20261008','20261009'],
                              'open':[10.,11.], 'high':[12.,12.], 'low':[9.,9.], 'close':[11.,11.],
                              'vol':[100.,200.], 'amount':[1000.,2000.]})
        if self.bad == 'missing':
            frame = frame.iloc[:1]
        elif self.bad == 'duplicate':
            frame = pd.concat([frame,frame.iloc[[0]]])
        elif self.bad == 'future':
            frame.loc[0,'trade_date']='20261012'
        elif self.bad == 'zero':
            frame.loc[0,'open']=0.
        elif self.bad == 'nan':
            frame.loc[0,'vol']=float('nan')
        return frame
    def adj_factor(self, **kwargs):
        self.calls.append(('factor', kwargs))
        return pd.DataFrame({'ts_code':[kwargs['ts_code']]*2,'trade_date':['20261008','20261009'],'adj_factor':[2.,2.]})


def collect(client, path, clock=lambda:100., sleep=lambda n:None):
    return api().collect_market_extension(client, ['000001.SZ','000002.SZ'], ['2026-10-08','2026-10-09'],
        path, binding={'freeze_sha':'a'*64}, interval=.5, clock=clock, sleep=sleep)


def test_valid_extension_resume_preserves_bytes_without_requests(tmp_path):
    result = collect(Client(), tmp_path)
    assert result['daily_rows'] == 4 and result['factor_rows'] == 4
    before = {p.name:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    client = Client('permission')
    assert collect(client,tmp_path) == result
    assert client.calls == []
    assert before == {p.name:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    daily, factors, manifest = api().load_snapshot(tmp_path, {'freeze_sha':'a'*64})
    assert len(daily) == len(factors) == 4 and manifest == result


@pytest.mark.parametrize('bad',['missing','duplicate','future','zero','nan'])
def test_invalid_market_never_completes(tmp_path,bad):
    with pytest.raises(ValueError):
        collect(Client(bad),tmp_path)
    assert not (tmp_path/'acquisition_manifest.json').exists()
    evidence=json.loads((tmp_path/'acquisition.json').read_text(encoding='utf-8'))
    assert evidence['failure']=='SCHEMA'
    assert evidence['request']['ts_code']=='000001.SZ'


@pytest.mark.parametrize('bad,cooldown,calls',[('minute',61.,1),('hour',3601.,1),('permission',0.,1),('network',0.,3)])
def test_quota_or_permission_errors_are_safe_and_bounded(tmp_path,bad,cooldown,calls,capsys):
    client=Client(bad)
    with pytest.raises(RuntimeError) as caught:
        collect(client,tmp_path)
    assert len(client.calls) == calls
    assert 'secret123' not in str(caught.value)
    evidence=(tmp_path/'acquisition.json').read_text(encoding='utf-8')
    assert 'secret123' not in evidence
    assert 'secret123' not in capsys.readouterr().out
    if cooldown:
        assert json.loads(evidence)['retry_not_before'] >= 100.+cooldown
        second=Client()
        with pytest.raises(RuntimeError):
            collect(second,tmp_path)
        assert second.calls == []


def test_every_actual_request_is_paced(tmp_path):
    now=[100.]
    observed=[]
    class Timed(Client):
        def daily(self,**kwargs):
            observed.append(now[0]); return super().daily(**kwargs)
        def adj_factor(self,**kwargs):
            observed.append(now[0]); return super().adj_factor(**kwargs)
    collect(Timed(),tmp_path,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
    assert len(observed)==4
    assert all(b-a>=.5 for a,b in zip(observed,observed[1:]))


def test_changed_file_or_identity_cannot_resume(tmp_path):
    collect(Client(),tmp_path)
    with pytest.raises(ValueError):
        api().load_snapshot(tmp_path,{'freeze_sha':'b'*64})
    path=tmp_path/'daily.parquet'
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError):
        collect(Client(),tmp_path)


def test_complete_calendar_and_missing_calendar_fail_closed(tmp_path):
    calendar=api().collect_calendar(Client(),'2026-10-08','2026-10-10',tmp_path,interval=.5,clock=lambda:100.,sleep=lambda n:None)
    assert calendar['exchange']=='SZSE'
    assert calendar['rows']==[{'cal_date':'2026-10-08','is_open':1},{'cal_date':'2026-10-09','is_open':1},{'cal_date':'2026-10-10','is_open':0}]
    client=Client('calendar_missing')
    with pytest.raises(ValueError):
        api().collect_calendar(client,'2026-10-08','2026-10-10',tmp_path/'bad',interval=.5,clock=lambda:100.,sleep=lambda n:None)
