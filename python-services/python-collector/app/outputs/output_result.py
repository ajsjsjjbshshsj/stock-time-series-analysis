from dataclasses import dataclass

from app.events.stock_daily_event import StockDailyEvent


@dataclass(frozen=True)
class DailyOutputBatch:
    records: list[dict]
    events: list[StockDailyEvent]




@dataclass(frozen=True)
class OutputResult:
    output_type: str
    expected_count: int
    success_count: int
    failure_count: int
    errors: list[str]

    @property
    def success(self) -> bool:
        return self.failure_count == 0

