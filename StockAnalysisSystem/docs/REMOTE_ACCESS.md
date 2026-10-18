# 用户、管理员与受保护入口

## 当前范围

新增登录、两角色权限、账户管理、持久化会话/登录限流与可选HTTPS网关。账户不由前端localStorage决定，后端每次读取当前账户权限。普通用户只读，管理员才能管理账户；初始密码及重置密码后要求先改密。

已增加[训练任务与独立worker](TRAINING_TASKS.md)：首项仅单股CPU XGBoost工程候选，管理员在`/#/training`管理；LightGBM/Transformer任务适配与候选审核发布仍是下一阶段。没有自动上线任何20股工程模型。受保护模式下其他未接线的训练按钮仍显示未就绪，不返回模拟成功。现有Transformer信号读取每次都复核封存哈希，历史诊断信号不是当前预测。

## 本机兼容与受保护模式

`AUTH_MODE=local`保留原本机工作流，**没有认证保护，不得暴露到公网**。用户入口固定真实数据，旧的演示偏好不影响用户页面。管理员本机演示偏好仍保留；受保护运行全部使用真实接口，没有合格结果就显示未就绪/错误，不回落mock。

生产配置位于现有环境变量或项目`.env`，实际凭据不提交Git：

```dotenv
AUTH_MODE=protected
AUTH_ORIGIN=https://your-hostname.example
AUTH_SECRET=
AUTH_ALLOW_LOCAL_HTTP=false
AUTH_TRUSTED_PROXIES=127.0.0.1/32
```

`AUTH_SECRET`自行生成至少32字节随机值，所有API实例共享并安全保管，不复制示例密码。`AUTH_ORIGIN`必须是浏览器实际访问的精确HTTPS origin，不能带路径、查询串或末尾斜杠。TLS Cookie为HttpOnly/Secure/host-only，写请求校验Origin及与当前会话绑定的CSRF。CSRF只在前端内存，会话令牌不进入localStorage。

仅本地开发时可显式设置`AUTH_ALLOW_LOCAL_HTTP=true`及`AUTH_ORIGIN=http://127.0.0.1:5173`。该例外只接受loopback hostname，不是远程配置，也不能用于HTTPS网关。

## 显式建表与创建首管理员

先安装应用requirements，配置现有MySQL连接；在`python-services/stock-analysis-app`目录运行。以下命令均为单行：

```powershell
python scripts/manage_accounts.py migrate
python scripts/manage_accounts.py bootstrap
```

第一条仅创建五张`analysis_auth_*`认证表，不改行情、特征库或模型表；导入应用与HTTP请求不自动建表。等价MySQL DDL在`infrastructure/mysql/migrations/V0.9_004_auth.sql`。第二条交互读取用户名和隐藏密码；没有默认管理员或公开注册，已有账户后不能重新bootstrap覆盖管理员。

登录后管理员在“账户管理”创建user/admin、启用/停用、修改角色或重置密码。最后一个有效管理员不能被停用或降权；检查在数据库事务中串行化。权限修改、重置或自行改密都会撤销目标旧会话。会话8小时绝对过期、30分钟空闲过期，登录失败按账户及可信来源持久限流。

## Linux服务器单入口

本仓库不自动申请服务器、域名或证书。准备有效证书、私钥和DNS后再启用：

1. MySQL配置`bind-address=127.0.0.1`，防火墙不开放3306；Java服务显式使用`--server.address=127.0.0.1`，监听原8080/8083端口。
2. API应用加载上述protected环境，使用`python -m uvicorn api.forecast:app --host 127.0.0.1 --port 8084 --no-proxy-headers`。`--no-proxy-headers`确保来源限流只信任我们明确设置的网关来源头；不要信任任意客户端X-Forwarded-For。
3. 基础设施用远程覆盖配置替换公开端口：`docker compose --env-file .env -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.remote.yml up -d`（在StockAnalysisSystem目录，Compose2.24.4+）。Kafka、Flink、KafkaUI只绑定loopback，ClickHouse原本已为loopback。
4. 前端目录执行`npm ci`及`npm run build`，正式提供静态dist，不公开Vite。Streamlit及其他调试服务不启动，或只绑定loopback，不通过网关公开。
5. 配置`SERVER_NAME`、与它一致的`AUTH_ORIGIN=https://SERVER_NAME`、`TLS_CERT_FILE`、`TLS_KEY_FILE`。证书/密钥路径为部署者真实文件，不能提交仓库。运行`docker compose --env-file .env -f infrastructure/remote/docker-compose.yml up -d`。

远程网关使用Linux host network，在443终止TLS并连接loopback API，不是Windows Docker Desktop的直接部署承诺。启动器检查证书、上游loopback约束及认证后端就绪/Origin一致性；若后端仍为local模式或未迁移，拒绝启动。公开接口不提供readiness/check/check-admin旁路。

Java行情接口先通过FastAPI会话检查；消费监控先检查admin。管理API本身再次检查admin，不能靠前端隐藏按钮或伪造身份头取得权限。防火墙/云安全组只开放需要的HTTPS入口；**必须实际检查3306/8080/8083/8084/9092等端口不可从外部访问**，仅有网关配置不足以保护其他公开监听。

镜像使用已验收的Docker Official Images镜像副本摘要，可通过`GATEWAY_IMAGE`显式升级；升级后重新验证，不使用未知来源镜像。没有为生产使用测试自签名证书。

## 验收与回退边界

验证匿名请求401、user管理请求403、初始强制改密、刷新后会话恢复、退出旧Cookie失效和旧mock偏好不会覆盖真实数据。隔离HTTPS验收可以用测试证书与合成行情，但不能据此声称真实预测已发布。训练任务需要另行显式迁移任务表、配置可信日历并独立启动worker，具体见TRAINING_TASKS.md。

回退时先停止公开网关并关闭外部端口，再恢复仅本机local工作流；不删除认证表或行情，不改旧模型。**不要在公网网关仍运行时将后端改成local**。已接入的单股工程任务也不会自动发布；回退前需正常停止worker并妥善处理中断任务。
