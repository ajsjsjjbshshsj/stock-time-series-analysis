"""原始市场参考数据访问层。

负责写入每日估值指标和指数/行业成分股快照，并提供补采任务所需的
轻量查询。这里不调用任何外部行情 SDK，也不负责提交事务。
"""

from datetime import date, datetime
from typing import Iterable, List, Optional

from sqlalchemy import text

from app.utils.logger import get_logger

logger = get_logger(__name__)

TABLE_STOCK_DAILY = "stock_daily"
TABLE_STOCK_DAILY_BASIC = "stock_daily_basic"
TABLE_STOCK_CONSTITUENT = "stock_constituent"

_BATCH_SIZE = 5000


class MarketDataRepository:
    """每日指标和成分股快照的 MySQL 仓库。"""

    def __init__(self, session):
        self.session = session

    def save_daily_basic(self, records: List[dict], batch_size: int = _BATCH_SIZE) -> int:
        """按 ``ts_code + trade_date`` 批量新增或更新每日指标。"""
        if not records:
            return 0

        stmt = text(
            f"INSERT INTO `{TABLE_STOCK_DAILY_BASIC}` "
            "(`ts_code`, `trade_date`, `turnover_rate`, `pe`, `pe_ttm`, "
            "`pb`, `ps`, `total_mv`, `source`) "
            "VALUES (:ts_code, :trade_date, :turnover_rate, :pe, :pe_ttm, "
            ":pb, :ps, :total_mv, :source) "
            "ON DUPLICATE KEY UPDATE "
            "turnover_rate = VALUES(turnover_rate), pe = VALUES(pe), "
            "pe_ttm = VALUES(pe_ttm), pb = VALUES(pb), ps = VALUES(ps), "
            "total_mv = VALUES(total_mv), source = VALUES(source), "
            "updated_at = CURRENT_TIMESTAMP"
        )
        return self._batch_execute(stmt, records, batch_size, TABLE_STOCK_DAILY_BASIC)

    def save_constituents(self, records: List[dict], batch_size: int = _BATCH_SIZE) -> int:
        """按分组、股票和快照日期批量新增或更新成分股。"""
        if not records:
            return 0

        stmt = text(
            f"INSERT INTO `{TABLE_STOCK_CONSTITUENT}` "
            "(`group_type`, `group_code`, `ts_code`, `as_of_date`, `weight`, `source`) "
            "VALUES (:group_type, :group_code, :ts_code, :as_of_date, :weight, :source) "
            "ON DUPLICATE KEY UPDATE "
            "weight = VALUES(weight), source = VALUES(source)"
        )
        return self._batch_execute(stmt, records, batch_size, TABLE_STOCK_CONSTITUENT)

    def replace_constituents(
        self,
        records: List[dict],
        batch_size: int = _BATCH_SIZE,
    ) -> int:
        """原子替换一张成分股快照，清除已调出的旧成员。"""
        if not records:
            return 0
        snapshot = {
            key: records[0][key]
            for key in ('group_type', 'group_code', 'as_of_date')
        }
        if any(
            any(record[key] != value for key, value in snapshot.items())
            for record in records
        ):
            raise ValueError('constituent records must belong to one snapshot')

        self.session.execute(
            text(
                f'DELETE FROM `{TABLE_STOCK_CONSTITUENT}` '
                'WHERE group_type = :group_type AND group_code = :group_code '
                'AND as_of_date = :as_of_date'
            ),
            snapshot,
        )
        return self.save_constituents(records, batch_size=batch_size)

    def list_stock_daily_trade_dates(
        self,
        start_date: Optional[date | str] = None,
        end_date: Optional[date | str] = None,
    ) -> List[date]:
        """返回行情表已有的交易日，供每日指标历史补采使用。"""
        clauses = []
        params = {}
        if start_date is not None:
            clauses.append("trade_date >= :start_date")
            params["start_date"] = self._as_date(start_date)
        if end_date is not None:
            clauses.append("trade_date <= :end_date")
            params["end_date"] = self._as_date(end_date)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        stmt = text(
            f"SELECT DISTINCT trade_date FROM `{TABLE_STOCK_DAILY}`"
            f"{where} ORDER BY trade_date"
        )
        result = self.session.execute(stmt, params)
        return [row[0] for row in result]

    def count_daily_basic_by_date(self, trade_date: date | str) -> int:
        """统计指定交易日已落库的每日指标数量。"""
        result = self.session.execute(
            text(
                f"SELECT COUNT(*) FROM `{TABLE_STOCK_DAILY_BASIC}` "
                "WHERE trade_date = :trade_date"
            ),
            {"trade_date": self._as_date(trade_date)},
        )
        return result.scalar() or 0

    def _batch_execute(self, stmt, records: List[dict], batch_size: int, table_name: str) -> int:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        total = 0
        for offset in range(0, len(records), batch_size):
            batch = records[offset:offset + batch_size]
            self.session.execute(stmt, batch)
            total += len(batch)
            logger.debug("已写入 %s/%s 条 → %s", total, len(records), table_name)
        return total

    @staticmethod
    def _as_date(value: date | str) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        normalized = value.replace("-", "")
        return datetime.strptime(normalized, "%Y%m%d").date()
