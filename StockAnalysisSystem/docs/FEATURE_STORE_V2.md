# 传统特征库 V2

`stock_features_v2` 是未标准化、因果的 62 列基础特征。列名与顺序由
`data_processor.feature_schema.FEATURE_COL_MAP` 唯一定义。预热期与新股的未知指标
保存为 NULL，不补零；最新无标签行情仍保留。旧 `stock_features` 和旧模型不会被覆盖。

## 安装与构建

配置现有数据库环境变量并安装分析服务依赖后，在分析服务目录执行。以下命令都是
显式操作，导入模块、查询 API、加载模型不会创建表或自动采集。

```sh
python -m scripts.feature_store migrate
python -m scripts.feature_store build --start-date 2023-01-01 --cutoff 2026-10-13
python -m scripts.feature_store publish --build-id <构建输出的32位标识>
python -m scripts.feature_store inspect
python -m scripts.feature_store rollback --build-id <先前已发布标识>
```

可通过 `FEATURE_STORE_ROOT` 或 `build --output` 指定输出目录；相对目录以项目根为基准。
默认 `.runtime/feature_store`。运行产物、源快照、测试、凭据均不提交到 Git。
每次构建单独封存每只股票的完整原始历史、精确来源哈希和 Parquet 文件哈希，逐股计算，
不复用已标准化的旧特征，不因停牌或新股短历史删除股票。

## 发布与故障恢复

构建只写独立暂存表及 BUILDING 元数据，不自动发布或训练。发布重新验证原始数据、
完整键覆盖、DOUBLE 模式、特征输出哈希和算法身份，通过后在自己持有的数据库命名锁下
用单条 RENAME 换表，再事务激活 READY 元数据。旧发布表保留为带构建标识的 archive。
不会覆盖同名 archive，也不会释放其他进程的锁。

DDL 与元数据事务之间存在短窗口；读取校验构建标识和完整请求键，窗口中可能明确报
`FeatureStoreUnavailable`，绝不返回混合数据或回退旧表。普通激活失败会恢复换表；
身份未知或连接导致提交状态无法判定时必须由操作人员核对，不能强制清锁或删表。
失败暂存表和来源证据保留，修复后新建构建，不重用失败身份。

读取同时验证数据库内便携的每股来源清单，因此相同键的价格被修改也会拒绝旧特征。
恢复数据库到另一台机器后无需原机器绝对路径；若要审计原文件，还须转移来源快照目录。

## 三条模型管线的边界

- 传统研究/排名：从此库取基础特征，标签在训练副本中从已绑定原始价格重建，
  股票池横截面特征不写全市场表；标准化与筛选只能在训练段拟合。
- V0.7/V0.8 正式预测：保留原始行情固定特征协议，不改走此库。
- Transformer：保留独立特征、原始缓存、模型 scaler 和契约，不替换为 62 列。

特征库发布成功不代表三个策略已重训、验证或上线。旧模型不能猜测新契约。
