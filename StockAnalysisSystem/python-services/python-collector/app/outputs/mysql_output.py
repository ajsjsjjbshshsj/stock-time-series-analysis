from app.outputs.base_output import BaseOutput
from app.outputs.output_result import DailyOutputBatch, OutputResult
from app.repositories.stock_repository import StockRepository


class MysqlOutput(BaseOutput):
    OUTPUT_TYPE = "MYSQL"

    def __init__(self, stock_repository: StockRepository):
        self.stock_repository = stock_repository

    def deliver(self, batch: DailyOutputBatch) -> OutputResult:
        records = batch.records
        expected_count = len(records)

        if expected_count == 0:
            return OutputResult(
                output_type=self.OUTPUT_TYPE,
                expected_count=0,
                success_count=0,
                failure_count=0,
                errors=[],
            )

        try:
            saved_count = self.stock_repository.save_daily_records(records)

            if not isinstance(saved_count, int):
                raise ValueError(
                    "StockRepository.save_daily_records() "
                    "必须返回整数"
                )

            if saved_count < 0 or saved_count > expected_count:
                raise ValueError(
                    f"MySQL 返回的写入数量不合法: {saved_count}"
                )

            return OutputResult(
                output_type=self.OUTPUT_TYPE,
                expected_count=expected_count,
                success_count=saved_count,
                failure_count=expected_count - saved_count,
                errors=[],
            )

        except Exception as exc:
            return OutputResult(
                output_type=self.OUTPUT_TYPE,
                expected_count=expected_count,
                success_count=0,
                failure_count=expected_count,
                errors=[str(exc)],
            )