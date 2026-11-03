"""Bounded, non-sensitive activity reports; no free-form log transport."""
import re
import time

STEPS = frozenset(('SNAPSHOT','RAW_VERIFY','FEATURE_READ','FEATURE_COMPUTE',
    'CACHE_READ','MATRIX_WRITE','SCORE','PROBE','TRAIN_EPOCH','TRAIN_ITERATION','SEAL'))


def normalize_activity(value):
    if not isinstance(value,dict) or not {'step','elapsed_ms'} <= set(value) or set(value)-{
        'step','elapsed_ms','stock','cache_hits','checkpoint_epoch'}:raise ValueError('Activity schema')
    if not isinstance(value['step'],str) or value['step'] not in STEPS:raise ValueError('Activity step')
    for key,maximum in (('elapsed_ms',86400000),('cache_hits',10000),('checkpoint_epoch',10000)):
        if key in value and (type(value[key]) is not int or not 0<=value[key]<=maximum):raise ValueError('Activity number')
    if 'stock' in value and (not isinstance(value['stock'],str) or not re.fullmatch(r'\d{6}\.(SH|SZ)',value['stock'])):
        raise ValueError('Activity stock')
    return dict(value)


class ActivityReporter:
    def __init__(self,emit,*,clock=time.monotonic,min_interval=20):
        self.emit=emit;self.clock=clock;self.started=clock();self.last=None;self.min_interval=min_interval

    def __call__(self,step,*,stock=None,cache_hits=None,checkpoint_epoch=None,force=False):
        now=self.clock()
        if not force and self.last is not None and now-self.last<self.min_interval:return
        value=dict(step=step,elapsed_ms=int(max(0,now-self.started)*1000))
        for key,item in (('stock',stock),('cache_hits',cache_hits),('checkpoint_epoch',checkpoint_epoch)):
            if item is not None:value[key]=item
        value=normalize_activity(value);self.emit('activity',**value);self.last=now
