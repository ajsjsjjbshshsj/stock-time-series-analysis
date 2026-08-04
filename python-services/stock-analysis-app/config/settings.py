"""
全局配置参数
所有敏感信息（Token、密码）必须通过环境变量或 .env 文件提供，
禁止在代码中写死，禁止在日志中打印。
"""
import os
from dotenv import load_dotenv

# 优先加载应用自身配置；在多语言仓库中回退到仓库根目录的共享配置。
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPOSITORY_ROOT = os.path.abspath(os.path.join(_APP_ROOT, '..', '..'))

for _env_path in (
    os.path.join(_APP_ROOT, '.env'),
    os.path.join(_REPOSITORY_ROOT, '.env'),
):
    if os.path.isfile(_env_path):
        load_dotenv(_env_path)
        break


# ── 敏感配置校验 ─────────────────────────────────────────────

def _require_env(key: str) -> str:
    """读取必须存在的环境变量，缺失时抛出明确提示。"""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"[配置缺失] 环境变量 {key} 未设置。"
            f"请在项目根目录 .env 文件中配置，参考 .env.example"
        )
    return value


# ── 敏感信息脱敏工具 ─────────────────────────────────────────

def mask_secret(value: str, visible: int = 4) -> str:
    """将敏感值仅保留前 N 位，其余用 * 替换，用于日志输出。"""
    if not value or len(value) <= visible:
        return '****'
    return value[:visible] + '*' * (len(value) - visible)


# ── 数据库配置 ───────────────────────────────────────────────

_DB_PASSWORD = _require_env('DB_PASSWORD')  # 无默认值，缺失即报错

DATABASE_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '3306'),
    'user': os.getenv('DB_USER', 'root'),
    'password': _DB_PASSWORD,
    'database': os.getenv('DB_NAME', 'stock_analysis'),
    'charset': 'utf8mb4',
    'connect_timeout': 10,        # 连接超时（秒）
    'pool_timeout': 30,           # 连接池获取超时（秒）
    'pool_pre_ping': True,        # 连接活跃检测（防 MySQL gone away）
    'pool_recycle': 3600,         # 连接回收周期（秒）
}

# ── Tushare API 配置 ─────────────────────────────────────────

TUSHARE_TOKEN = _require_env('TUSHARE_TOKEN')  # 无默认值，缺失即报错

# ── Akshare 配置（无需 token）────────────────────────────────

AKSHARE_ENABLED = True

# ── 数据采集配置 ─────────────────────────────────────────────

DATA_COLLECTION = {
    '默认开始日期': '2024-01-01',
    '默认结束日期': None,  # None 表示当前日期
    'batch_size': 100,     # 批量采集的股票数量
    'retry_times': 3,      # 失败重试次数
    'retry_delay': 1.0,    # 重试基础间隔（秒），指数退避
    'request_interval': 0.5,  # 请求间隔（秒），避免频率限制
}

# ── 技术分析指标参数 ─────────────────────────────────────────

TECHNICAL_INDICATORS = {
    'ma_periods': [5, 10, 20, 60],  # 均线周期
    'rsi_period': 14,               # RSI 周期
    'macd_fast': 12,                # MACD 快线
    'macd_slow': 26,                # MACD 慢线
    'macd_signal': 9,               # MACD 信号线
}

# ── 机器学习模型配置 ─────────────────────────────────────────

RANDOM_SEED = 42  # 全局随机种子，确保实验可复现

MODEL_CONFIG = {
    # 通用参数
    'random_seed': RANDOM_SEED,
    'train_ratio': 0.6,
    'val_ratio': 0.2,
    'test_ratio': 0.2,

    # XGBoost
    'xgboost_params': {
        'max_depth': 6,
        'learning_rate': 0.1,
        'n_estimators': 100,
        'random_state': RANDOM_SEED,
    },

    # LightGBM (排名模型)
    'lgb_params': {
        'objective': 'regression',
        'metric': 'rmse',
        'num_leaves': 63,
        'learning_rate': 0.05,
        'n_estimators': 500,
        'seed': RANDOM_SEED,
    },

    # LSTM
    'lstm_epochs': 50,
    'lstm_batch_size': 32,
    'lstm_sequence_length': 60,

    # Transformer
    'transformer_sequence_length': 20,
    'transformer_epochs': 100,
    'transformer_batch_size': 64,
    'transformer_lr': 0.001,
}

# ── 日志配置 ─────────────────────────────────────────────────

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = 'logs/stock_analysis.log'          # 保留向后兼容
LOG_DIR = 'logs'
LOG_MAX_BYTES = 10 * 1024 * 1024             # 单文件上限 10 MB
LOG_BACKUP_COUNT = 5                          # 滚动保留份数

# ── 排名选股策略配置 ─────────────────────────────────────────

RANKING_CONFIG = {
    'top_n': 10,              # 默认选股数量
    'rebalance_days': 5,      # 调仓周期（交易日）
    'probe_n_iter': 10,       # 探针法迭代轮数
    'probe_n_noise': 10,      # 探针法噪声特征数
    'lgb_params': {           # LightGBM 排名模型参数
        'objective': 'regression',
        'metric': 'rmse',
        'num_leaves': 63,
        'learning_rate': 0.05,
        'n_estimators': 500,
    },
}


# ── 安全输出（供日志使用）────────────────────────────────────

def safe_config_repr() -> str:
    """返回配置摘要字符串，敏感字段已脱敏，可安全写入日志。"""
    return (
        "DATABASE_CONFIG: host=%s, port=%s, user=%s, password=%s, database=%s | "
        "TUSHARE_TOKEN: %s"
        % (
            DATABASE_CONFIG['host'],
            DATABASE_CONFIG['port'],
            DATABASE_CONFIG['user'],
            mask_secret(_DB_PASSWORD),
            DATABASE_CONFIG['database'],
            mask_secret(TUSHARE_TOKEN),
        )
    )
