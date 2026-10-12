"""Hand-written reference records, no SDK/config imports or real database access."""
def reference(code='000001.SZ',**changes):
    row=dict(ts_code=code,symbol=code[:6],name='测试股票',area='深圳',industry='银行',
        market='主板',exchange='SZSE',curr_type='CNY',list_status='L',list_date='19910403',delist_date=None)
    row.update(changes)
    return row

def calendar():
    return dict(source='tushare.trade_cal',rows=[dict(cal_date=day,is_open=opened) for day,opened in
        [('2026-09-25',1),('2026-09-26',0),('2026-09-27',0),('2026-09-28',1),('2026-09-29',1)]])
