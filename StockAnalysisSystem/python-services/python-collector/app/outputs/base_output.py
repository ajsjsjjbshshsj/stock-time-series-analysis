from abc import ABC, abstractmethod

from app.outputs.output_result import DailyOutputBatch, OutputResult


class BaseOutput(ABC):
    @abstractmethod
    def deliver(self, batch: DailyOutputBatch) -> OutputResult:
        raise NotImplementedError