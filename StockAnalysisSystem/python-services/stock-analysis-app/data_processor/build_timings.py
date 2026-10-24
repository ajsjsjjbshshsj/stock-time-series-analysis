"""Diagnostic monotonic durations, independent of data and model contracts."""
from contextlib import contextmanager,nullcontext
from time import perf_counter


class BuildTimings:
    def __init__(self,*,clock=perf_counter):
        self.clock=clock;self.phases={}

    @contextmanager
    def measure(self,name):
        start=self.clock()
        try:yield
        finally:
            phase=self.phases.setdefault(name,dict(seconds=0.,calls=0))
            phase['seconds']+=self.clock()-start;phase['calls']+=1

    def snapshot(self):
        return dict(schema_version=1,phases={name:dict(value) for name,value in self.phases.items()})


def measured(timings,name):
    return nullcontext() if timings is None else timings.measure(name)
