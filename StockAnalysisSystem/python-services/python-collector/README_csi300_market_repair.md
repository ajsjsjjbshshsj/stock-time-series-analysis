# 沪深300历史来源修复（B1）

本工具为固定池 `csi300_5739ddc829ad3650` 补真实历史来源，范围2023-10-09至2026-09-30。不训练、不删股、不更新旧行、不向Kafka重放，也不重算Flink/ClickHouse。
请在当前规范仓库的 `StockAnalysisSystem/python-services/python-collector` 中运行。下面各命令均为单行，按顺序执行；权限不足/冷却/来源或数据库冲突时停止并保留证据。

## 五个阶段

1. `D:/Python/python.exe scripts/collect_csi300_market_repair.py --stage inventory`：只读MySQL，独立保存日线、基本指标、IPO和实际表schema；复用已封日历，不调用行情接口。
2. `D:/Python/python.exe scripts/collect_csi300_market_repair.py --stage acquire`：缺日期批量采日线、基本缺行、复权因子、停复牌。成功分片SHA校验后可续跑；共用原限频锁，网络最多3次，权限/schema错误不重试，分钟/小时冷却61/3601秒退出。
3. `D:/Python/python.exe scripts/collect_csi300_market_repair.py --stage assemble`：保留原始值与来源，封新数据包；未知缺口不冒充全天停牌。
4. `D:/Python/python.exe scripts/collect_csi300_market_repair.py --stage import --allow-insert`：仅插原本缺失的股日键，每批最多500；读回核实，相同已有值不再写，不同值停止。所有旧行包含id和时间戳都须保持不变。
5. `D:/Python/python.exe scripts/collect_csi300_market_repair.py --stage verify --verify-db`：离线复核源SHA和归因，再只读数据库核对导入；不带`--verify-db`则完全离线，不需要Token或DB_PASSWORD。

默认新输出为规范仓库下 `StockAnalysisSystem/.runtime/csi300_repair/20261009_0930/`，四个子目录 baseline、acquisition、snapshot、import。不能在旧来源包内写新账本。坏schema/无校验的半分片保留在原目录，调查后使用同一修复根下的新输出目录，不删除失败证据。
完成源包是不可变的；同一目录身份改变会拒绝，不使用另一组股票或另一窗口强行续跑。进程异常退出留下锁时，先确认没有原进程、查看证据再由操作人员处理，工具不自动删别人锁。

## 如何解读结果

- `acquisition_complete`表示全部计划请求成功取得并校验了响应，包括真实空响应；不代表每股每天都有行情。
- `source_issue_counts`保留未解释缺行情、缺基本行、缺因子和冲突。全天S且没有日内时段可作停牌证据；R、日内S、S/R矛盾、空响应均不能自动解释成全天停牌。
- `db_import_complete`仅代表已验证插入候选全部在数据库中且旧行未变，不代表所有源问题已解决。账本`recorded_inserted`是已记录事务的实际新增数；崩溃后恢复发现的相同键计`recorded_already_equal`，不伪称都是本工具插入。
- `model_ready`始终false：300身份与股票映射不变，但每日报价行数可以因停牌/IPO不同；B2才处理缺值掩码、短历史成员、标签和训练。不能据B1把旧20股模型称为300股模型。

原价不复权，复权因子单独保存；成交量为手、成交额千元、市值万元，不将前复权价写回旧原价。PE/PE_TTM合法空值保留为null：API返回null与仅在DB观察到null有不同来源状态，不能据null单独断言公司亏损。不填0、不捏造停牌/上市前行情。
固定当前成分回溯历史仍有生存者偏差，不是历史动态成分池盲测。旧任务成功标记、模型/缓存/报告、原成分和股票基础资料均不改。

## 阅读代码

先读 `app/market_data/repair_contract.py`（基线/请求）→ `repair_acquisition.py`（共享限频/分片）→ `repair_snapshot.py`（归因/离线重验）→ `app/repositories/market_repair_repository.py`（只插/事务/存量校验）→ `scripts/collect_csi300_market_repair.py`（阶段入口）。对应新增测试覆盖空值、停牌、限额、SHA篡改、竞争插入及回滚。
代码/测试/此业务指南可以提交；Token/.env、原始分片、Parquet、账本、模型和本地规划不能公开提交。
