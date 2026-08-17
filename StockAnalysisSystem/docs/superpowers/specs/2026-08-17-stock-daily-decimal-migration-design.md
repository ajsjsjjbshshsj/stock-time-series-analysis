# StockDaily FLOAT 到 DECIMAL 迁移设计

## 1. 背景

`stock_analysis.stock_daily` 目前约有 484 万行数据，价格、涨跌、成交量和成交额字段仍使用 MySQL `FLOAT`。V0.3 真实 Tushare 双写对账中，价格字段一致，但 `vol` 和 `amount` 出现了最高约 `0.00026%` 的二进制浮点舍入误差。

本次迁移只处理 `stock_daily`，不修改 `stock_basic`、`stock_features`、`analysis_result`、`collection_task` 或 `collection_delivery`。

## 2. 目标

- 将价格和涨跌字段改为 `DECIMAL(20,6)`。
- 将成交量和成交额字段改为 `DECIMAL(24,4)`。
- 保留主键、业务唯一键、普通索引、时间字段和已有数据。
- 迁移过程可验证、可回滚，不直接在原表上执行高风险类型转换。
- 表切换后重新执行真实 Collector 写入和 MySQL/Kafka/Java 对账。

## 3. 字段映射

| 字段 | 原类型 | 目标类型 |
|---|---|---|
| `open` | `FLOAT` | `DECIMAL(20,6)` |
| `high` | `FLOAT` | `DECIMAL(20,6)` |
| `low` | `FLOAT` | `DECIMAL(20,6)` |
| `close` | `FLOAT` | `DECIMAL(20,6)` |
| `pre_close` | `FLOAT` | `DECIMAL(20,6)` |
| `change` | `FLOAT` | `DECIMAL(20,6)` |
| `pct_chg` | `FLOAT` | `DECIMAL(20,6)` |
| `vol` | `FLOAT` | `DECIMAL(24,4)` |
| `amount` | `FLOAT` | `DECIMAL(24,4)` |

## 4. 迁移方案

采用影子表和原子重命名：

1. 检查磁盘空间、MySQL 版本、原表结构、行数和索引。
2. 创建 `stock_daily_decimal`，复制原表的非数值结构、主键和索引，并使用目标 `DECIMAL` 类型。
3. 使用 `INSERT INTO ... SELECT ...` 将原表数据复制到影子表。
4. 校验总行数、主键范围、业务唯一键、空值数量和数值聚合结果。
5. 暂停 Python Collector 写入，确认没有正在运行的数据库写事务。
6. 使用一个 `RENAME TABLE` 语句原子交换：
   - `stock_daily` → `stock_daily_float_backup`
   - `stock_daily_decimal` → `stock_daily`
7. 校验新表结构、索引、行数和查询能力。
8. 执行一批真实 Tushare 数据写入并完成 MySQL/Kafka/Java 对账。
9. 保留 `stock_daily_float_backup`，本次任务不删除备份表。

## 5. 一致性检查

切换前必须全部满足：

- 新旧表 `COUNT(*)` 相等。
- 新旧表 `MIN(id)`、`MAX(id)` 相等。
- 新表不存在重复的 `(ts_code, trade_date)`。
- 各数值字段的 `NULL` 数量相等。
- 按固定样本比较代码、日期和全部行情字段。
- 新表主键、唯一键和 `trade_date` 索引存在。

由于原数据已经以 `FLOAT` 存储，迁移只能精确保留当前数据库中的数值语义，无法恢复写入前已经丢失的尾数。迁移后的新数据会按 `DECIMAL` 保存，不再产生同类二进制浮点存储误差。

## 6. 写入暂停与并发控制

- 迁移复制期间允许查询原表。
- 切换前停止受控 Collector 写入。
- Java Consumer 不写 MySQL，无需因本次迁移停止。
- 原子重命名只在全部校验通过后执行。
- 如果检测到影子表或备份表名称冲突，停止迁移并人工确认，不覆盖任何表。

## 7. 回滚

切换后若验证失败，停止 Collector 写入并执行反向原子重命名：

```sql
RENAME TABLE
  stock_daily TO stock_daily_decimal_failed,
  stock_daily_float_backup TO stock_daily;
```

回滚后重新检查原表行数、索引和 Collector 写入。失败的新表保留用于诊断，不自动删除。

## 8. 验收标准

- `stock_daily` 九个行情数值字段均为目标 `DECIMAL` 类型。
- 原有约 484 万行数据和业务唯一键完整保留。
- `stock_daily_float_backup` 存在且可用于回滚。
- Python Collector 能继续写入 MySQL。
- 真实 `dual` 批次的 MySQL、Kafka 和 Java Consumer 数量一致。
- 新写入数据与 Tushare 在声明的小数位范围内一致。
- 不提交 `.env`、Token、密码、数据库导出文件或运行日志。
