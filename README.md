# StockAnalysisSystem

按语言和部署职责组织的股票分析单仓库。

```text
StockAnalysisSystem/
├── python-services/
│   ├── stock-analysis-app/   # 分析、特征、训练、预测和可视化
│   └── python-collector/      # 独立数据采集服务
├── java-services/
│   ├── common-model/         # 跨 Java 模块共享模型
│   └── kafka-consumer-service/
├── infrastructure/
└── docs/
```

常用入口：

```powershell
# 原股票分析系统
cd python-services/stock-analysis-app
python main.py --help

# 独立采集服务
cd python-services/python-collector
python app/main.py --help

# Java Kafka Consumer
cd java-services/kafka-consumer-service
mvn test
```

仓库根目录的 `.env` 由两个 Python 模块共享，提交配置示例时仅修改 `.env.example`，不要提交真实 Token 或密码。
